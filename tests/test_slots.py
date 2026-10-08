import io
import uuid
import zipfile

import pytest

from bwpreset import model as m
from bwpreset.codec import Obj, Preset, parse, serialize, walk
from bwpreset.slots import Device, SlotError, insert_slot, slot_devices, slot_path, slot_targets, register_slot_targets


def device(name="Effect", nested=None):
    param = Obj(m.P_NUMBER, [(m.F_NAME, 8, "MIX"), (0x136, 7, 0.5)])
    params = [param] + ([fx_slot("INNER", nested)] if nested is not None else [])
    return Obj(
        m.DEVICE,
        [
            (m.F_NAME, 8, ""),
            (m.F_DEVICE_ID, 0x15, uuid.UUID(int=1)),
            (m.F_TITLE, 8, name),
            (m.F_CONTENTS, 9, Obj(m.CONTENTS, [(m.F_NAME, 8, "CONTENTS"), (m.F_PARAMS, 0x12, params)])),
            (0x777, 9, param),
        ],
    )


def fx_slot(name, devices):
    return Obj(
        m.FX_SLOT,
        [
            (m.F_NAME, 8, name),
            (
                m.F_CHAIN,
                9,
                Obj(
                    0x18F,
                    [
                        (m.F_NAME, 8, "Chain"),
                        (m.F_CHAIN_DEVICES, 9, Obj(0x33, [(m.F_NAME, 8, ""), (m.F_CHAIN_LIST, 0x12, devices)])),
                        (0x999, 8, "chain state"),
                    ],
                ),
            ),
        ],
    )


def preset(root, meta=None):
    return Preset("00010002000000000000000000000000000000", meta or [], Obj(m.PRESET, [(m.F_DEVICE, 9, root)]), 1)


def host():
    root = device("Grid")
    m.set_field(m.get(root, m.F_CONTENTS), m.F_PARAMS, [fx_slot("POST_FX", []), fx_slot("PRE_FX", [])])
    return preset(root)


def test_insert_file_preserves_graph_dependencies_and_targets(tmp_path):
    source = preset(device(nested=[device("Nested")]), [("referenced_packaged_file_ids", 0x19, ["asset-a"])])
    path = tmp_path / "effect.bwpreset"
    path.write_bytes(serialize(source))
    dest = host()
    inserted = insert_slot(dest, "fx", Device.from_file(path))
    assert inserted.path == "CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:"
    child = slot_devices(m.get(dest.body, m.F_DEVICE), "fx")[0]
    assert child is inserted.obj
    assert m.get(child, 0x777) is m.params(child)[0]
    assert m.get(child, 0x777) is not m.get(m.get(source.body, m.F_DEVICE), 0x777)
    assert dict((k, v) for k, t, v in dest.meta)["referenced_packaged_file_ids"] == ["asset-a"]
    targets = slot_targets(m.get(dest.body, m.F_DEVICE))
    assert "CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:CONTENTS/MIX" in targets
    assert "CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:CONTENTS/INNER/Chain/DEVICE_CHAIN/0:CONTENTS/MIX" in targets
    assert inserted["MIX"].descriptor().cls == m.DESCRIPTOR
    assert parse(serialize(dest)).body.cls == m.PRESET


def test_append_keeps_indices_and_existing_chain():
    dest = host()
    first = insert_slot(dest, "note_fx", Device.from_object(device()))
    second = insert_slot(dest, "PRE_FX", Device.from_object(device()))
    assert first.path.endswith("/0:") and second.path.endswith("/1:")
    assert slot_path("note_fx", 1, "CONTENTS/MIX") == second["MIX"].path
    with pytest.raises(SlotError):
        slot_path("fx", -1)
    with pytest.raises(SlotError):
        insert_slot(dest, "missing", Device.from_object(device()))


def test_attachment_merge_and_atomic_rejection():
    dest = host()
    source = preset(device())
    source.attachment = b"uninterpreted attachment"
    with pytest.raises(SlotError, match="attachment"):
        insert_slot(dest, "fx", Device.from_preset(source))
    assert not slot_devices(m.get(dest.body, m.F_DEVICE), "fx")


def zip_attachment(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def test_zip_dependencies_merge_and_collision_is_atomic():
    dest = host()
    dest.attachment = zip_attachment({"old.bin": b"old"})
    source = preset(device())
    source.attachment = zip_attachment({"new.bin": b"new"})
    insert_slot(dest, "fx", Device.from_preset(source))
    with zipfile.ZipFile(io.BytesIO(dest.attachment)) as archive:
        assert archive.read("new.bin") == b"new"
        assert archive.read("old.bin") == b"old"
    source.attachment = zip_attachment({"old.bin": b"different"})
    before = serialize(dest)
    with pytest.raises(SlotError, match="conflicting"):
        insert_slot(dest, "fx", Device.from_preset(source))
    assert serialize(dest) == before


def test_nested_modulator_target_and_internal_paths_stay_relative():
    effect = device()
    route = Obj(
        m.MOD_ROUTING,
        [(m.F_ROUTE_TARGET, 8, "CONTENTS/MIX"), (m.F_ROUTE_DESCRIPTOR, 9, Obj(m.DESCRIPTOR, [(0x124, 7, -2.0), (0x125, 7, 2.0)]))],
    )
    source = Obj(m.MOD_SOURCE, [(m.F_NAME, 8, "OUT"), (m.F_ROUTINGS, 0x12, [route])])
    rate = Obj(m.P_NUMBER, [(m.F_NAME, 8, "RATE"), (0x136, 7, 0.2)])
    mod = Obj(m.MODULATOR, [(m.F_NAME, 8, "0"), (m.F_MODULE_CONTENTS, 9, Obj(m.CONTENTS, [(m.F_PARAMS, 0x12, [rate, source])]))])
    effect.fields.append((m.F_MODULATORS, 9, Obj(m.MODULATOR_LIST, [(m.F_LIST, 0x12, [mod])])))
    inserted = insert_slot(host(), "fx", Device.from_preset(preset(effect)))
    assert inserted["MODULATORS/0/CONTENTS/RATE"].path.endswith("0:MODULATORS/0/CONTENTS/RATE")
    assert m.get(inserted["MIX"].descriptor(), 0x125) == 2.0
    copied_route = next(o for o in walk(inserted.obj) if o.cls == m.MOD_ROUTING)
    assert m.get(copied_route, m.F_ROUTE_TARGET) == "CONTENTS/MIX"


def test_definition_defaults_and_descriptor(tmp_path):
    descriptor = Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 1.0)])
    atom = Obj(0x121, [(0x2BD, 8, "MIX"), (0x2BE, 9, descriptor), (0x2C8, 7, 0.75)])
    definition = preset(Obj(0xD4, [(0x181, 0x15, uuid.UUID(int=42)), (0x182, 8, "Effect"), (0x186, 8, "Audio FX"), (0xAD, 0x12, [atom])]))
    definition.body = m.get(definition.body, m.F_DEVICE)
    path = tmp_path / "effect.bwdevice"
    path.write_bytes(serialize(definition))
    source = Device.from_definition(path)
    inserted = insert_slot(host(), "fx", source)
    assert m.param_value(m.params(inserted.obj)[0]) == 0.75
    assert m.get(inserted["MIX"].descriptor(), 0x125) == 1


def test_hardware_definition_exposes_named_input_and_output_bindings(tmp_path):
    from bwpreset.bindings import HardwarePort, decode_binding, BindingError, DeviceSource

    atoms = [Obj(0x35B, [(0x10F5, 8, "INPUT")]), Obj(0x35A, [(0x10F5, 8, "OUTPUT")])]
    definition = Preset(
        "00010002000000000000000000000000000000",
        [],
        Obj(0xD4, [(0x181, 0x15, uuid.UUID(int=42)), (0x182, 8, "HW FX"), (0xAD, 0x12, atoms)]),
        1,
    )
    path = tmp_path / "hardware.bwdevice"
    path.write_bytes(serialize(definition))
    attached = insert_slot(host(), "fx", Device.from_definition(path))
    input_port = HardwarePort("User label", "CoreAudio", "machine", "input", "interface", ("in-1", "in-2"))
    output_port = HardwarePort("User label", "CoreAudio", "machine", "output", "interface", ("out-1", "out-2"))
    attached.bind("INPUT", input_port).bind("OUTPUT", output_port)
    assert [decode_binding(p) for p in m.params(attached.obj)] == [input_port, output_port]
    with pytest.raises(BindingError):
        attached.bind("INPUT", DeviceSource("AUDIO_INPUT"))
    default = Obj(m.DEVICE, [(m.F_DEVICE_ID, 0x15, uuid.UUID(int=42)), (m.F_CONTENTS, 9, Obj(m.CONTENTS))])
    source = Device.from_definition(path, default_settings=default)
    assert [m.get(p, m.F_NAME) for p in m.params(source.obj)] == ["INPUT", "OUTPUT"]
    assert not m.params(default)


def test_invalid_targets_and_source_type():
    inserted = insert_slot(host(), "fx", Device.from_object(device()))
    with pytest.raises(SlotError):
        inserted["NOT_THERE"]
    with pytest.raises(SlotError):
        Device.from_object(Obj(m.MODULE))


def test_sparse_module_and_modulator_names_in_slot_targets():
    effect = device()
    cutoff = Obj(m.P_NUMBER, [(m.F_NAME, 8, "CUTOFF"), (0x136, 7, 60.0)])
    module = Obj(m.MODULE, [(m.F_NAME, 8, "7"), (m.F_MODULE_CONTENTS, 9, Obj(m.CONTENTS, [(m.F_PARAMS, 0x12, [cutoff])]))])
    m.params(effect).append(Obj(m.GRID, [(m.F_NAME, 8, "MODULES"), (m.F_LIST, 0x12, [module])]))
    rate = Obj(m.P_NUMBER, [(m.F_NAME, 8, "RATE"), (0x136, 7, 0.2)])
    mod = Obj(m.MODULATOR, [(m.F_NAME, 8, "12"), (m.F_MODULE_CONTENTS, 9, Obj(m.CONTENTS, [(m.F_PARAMS, 0x12, [rate])]))])
    effect.fields.append((m.F_MODULATORS, 9, Obj(m.MODULATOR_LIST, [(m.F_LIST, 0x12, [mod])])))
    dest = host()
    attached = insert_slot(dest, "fx", Device.from_object(effect))
    assert attached["CONTENTS/MODULES/7/CONTENTS/CUTOFF"].path in slot_targets(m.get(dest.body, m.F_DEVICE))
    assert attached["MODULATORS/12/CONTENTS/RATE"].path in slot_targets(m.get(dest.body, m.F_DEVICE))
    with pytest.raises(SlotError):
        attached["MODULATORS/0/CONTENTS/RATE"]


@pytest.mark.parametrize("chain_uuid", [uuid.UUID(int=0), uuid.UUID(int=123)])
def test_chain_identifiers_are_preserved(chain_uuid):
    effect = device(nested=[])
    chain = m.get(m.params(effect)[1], m.F_CHAIN)
    chain.fields.append((0x2AB8, 0x15, chain_uuid))
    attached = insert_slot(host(), "fx", Device.from_object(effect))
    assert m.get(m.get(m.params(attached.obj)[1], m.F_CHAIN), 0x2AB8) == chain_uuid


def test_ambiguous_targets_fail_and_top_level_controls_resolve():
    effect = device()
    m.params(effect).append(Obj(m.P_NUMBER, [(m.F_NAME, 8, "MIX"), (0x136, 7, 0.0)]))
    attached = insert_slot(host(), "fx", Device.from_object(effect))
    with pytest.raises(SlotError, match="ambiguous"):
        attached["MIX"]
    effect = device()
    on = Obj(m.P_BOOL, [(m.F_NAME, 8, "On"), (0x12F, 5, True)])
    effect.fields.append((0x888, 9, on))
    attached = insert_slot(host(), "fx", Device.from_object(effect, descriptors={"On": Obj(m.DESCRIPTOR)}))
    assert attached["On"].path.endswith("0:On")
    m.params(effect).append(Obj(m.P_BOOL, [(m.F_NAME, 8, "On"), (0x12F, 5, True)]))
    attached = insert_slot(host(), "fx", Device.from_object(effect))
    with pytest.raises(SlotError, match="ambiguous short"):
        attached["On"]
    assert attached["CONTENTS/On"].path.endswith("0:CONTENTS/On")


def test_binding_selector_inside_slot_is_atomic_and_preserves_shared_refs():
    from bwpreset.bindings import BindingError, HardwarePort, DeviceSource, SelectorKind, decode_binding

    effect = device()
    selector = Obj(0x37C, [(m.F_NAME, 8, "INPUT"), (0x10FF, 10, None)])
    m.params(effect).append(selector)
    effect.fields.append((0x999, 9, selector))
    attached = insert_slot(host(), "fx", Device.from_object(effect))
    port = HardwarePort("Interface", "CoreAudio", "machine", "input", "device", ("channel-3",))
    assert attached.bind("INPUT", port, kind=SelectorKind.HARDWARE_INPUT) is attached
    assert decode_binding(m.get(attached.obj, 0x999)) == port
    with pytest.raises(BindingError):
        attached.bind("INPUT", DeviceSource("AUDIO_INPUT"), kind=SelectorKind.HARDWARE_INPUT)
    assert decode_binding(m.get(attached.obj, 0x999)) == port


def test_patch_integration_routes_remotes_dependencies_and_foreign_ownership(monkeypatch):
    from bwpreset import build
    from bwpreset.bindings import DeviceSource, parameter_from_definition, decode_binding
    from bwpreset.build import Patch, PatchError

    base = host()
    root = m.get(base.body, m.F_DEVICE)
    root.fields.extend(
        [
            (m.F_PRESET_NAME, 8, ""),
            (m.F_MODULATORS, 9, Obj(m.MODULATOR_LIST, [(m.F_LIST, 0x12, [])])),
            (m.F_REMOTE_CONTROLS, 9, Obj(m.REMOTE_CONTROLS, [(m.F_REMOTE_PAGES, 0x12, [])])),
        ]
    )
    m.params(root).append(Obj(m.GRID, [(m.F_NAME, 8, "MODULES"), (m.F_LIST, 0x12, [])]))
    base.body.fields.append((m.F_SELECTION, 0x12, []))
    type_id = uuid.UUID(int=99)
    selector = parameter_from_definition(Obj(0x359, [(0x10F5, 8, "SOURCE")]))
    template = Obj(
        m.MODULE,
        [
            (m.F_NAME, 8, "0"),
            (m.F_TYPE, 0x15, type_id),
            (m.F_PRESET_NAME, 8, ""),
            (m.F_TITLE, 8, "Test"),
            (m.F_USER_TITLE, 8, ""),
            (m.F_X, 1, 0),
            (m.F_Y, 1, 0),
            (
                m.F_MODULE_CONTENTS,
                9,
                Obj(m.CONTENTS, [(m.F_PARAMS, 0x12, [selector, Obj(m.MOD_SOURCE, [(m.F_NAME, 8, "MOD"), (m.F_ROUTINGS, 0x12, [])])])]),
            ),
        ],
    )
    entry = {
        "id": str(type_id),
        "inputs": [],
        "outputs": [],
        "mod_sources": ["MOD"],
        "params": {"SOURCE": {"kind": "source"}, "MOD": {"kind": "mod_source"}},
    }
    data = ({"modules": {"Test": entry}, "modulators": {}, "devices": {}}, {type_id: template}, {"Test Grid": base})
    monkeypatch.setattr(build, "load", lambda: data)
    patch = Patch("Test Grid", "test")
    source = Device.from_preset(preset(device(), [("referenced_packaged_file_ids", 0x19, ["pack-asset"])]))
    m.params(source.obj).append(Obj(m.P_NUMBER, [(m.F_NAME, 8, "GAIN"), (0x136, 7, 1.0)]))
    effect = patch.slot("fx", source)
    direct_descriptor = Obj(m.DESCRIPTOR, [(0x124, 7, -4.0), (0x125, 7, 4.0)])
    effect.register_target("MIX", direct_descriptor)
    assert m.get(register_slot_targets(patch)[effect["MIX"].path].descriptor(), 0x125) == 4.0
    mod = patch.add("Test", 0, 0, SOURCE=DeviceSource("AUDIO_INPUT"))
    assert decode_binding(mod.used["SOURCE"]) == DeviceSource("AUDIO_INPUT")
    mod.route(effect["MIX"], 0.25)
    patch.remote(effect["MIX"], "Mix")
    full_path = effect["MIX"].path
    descriptor = Obj(m.DESCRIPTOR, [(0x124, 7, -1.0), (0x125, 7, 1.0)])
    registered = register_slot_targets(patch, "fx", descriptors={full_path: descriptor})
    assert registered[full_path].owner.obj is effect.obj
    assert registered[full_path].owner.patch is patch
    assert m.get(registered[full_path].descriptor(), 0x124) == -1.0
    mod.route(registered[full_path], 0.5)
    patch.remote(registered[full_path], "Registered mix")
    gain_path = effect["GAIN"].path
    register_slot_targets(patch, descriptors={gain_path: Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 8.0)])})
    effect.register_target("MIX", descriptor)
    assert m.get(register_slot_targets(patch)[gain_path].descriptor(), 0x125) == 8.0
    effect.register_target("MIX", direct_descriptor)
    assert m.get(register_slot_targets(patch)[full_path].descriptor(), 0x125) == 4.0
    with pytest.raises(SlotError):
        register_slot_targets(patch, descriptors={full_path: Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 99.0)]), gain_path: Obj(0x999)})
    assert m.get(register_slot_targets(patch)[full_path].descriptor(), 0x125) == 4.0
    final = patch.to_preset()
    assert m.get(m.get(mod.used["MOD"], m.F_ROUTINGS)[0], m.F_ROUTE_TARGET) == effect["MIX"].path
    remote = next(o for o in walk(final.body) if o.cls == m.REMOTE_CONTROL)
    assert m.get(remote, m.F_RC_TARGET) == effect["MIX"].path
    assert dict((k, v) for k, t, v in final.meta)["referenced_packaged_file_ids"] == ["pack-asset"]
    assert m.get(root, m.F_TITLE) == "Grid"
    foreign = Patch("Test Grid", "foreign").slot("fx", source)
    with pytest.raises(PatchError):
        patch.remote(foreign["MIX"])
    assert serialize(parse(serialize(final))) == serialize(final)


def test_legacy_and_multi_machine_bindings_inside_slot_device():
    from bwpreset.bindings import PreferencesBus, HardwareBindings, SelectorKind, decode_binding

    effect = device()
    m.params(effect).extend([Obj(0x37C, [(m.F_NAME, 8, name), (0x10FF, 10, None)]) for name in ("SOURCE", "DEVICE")])
    attached = insert_slot(host(), "fx", Device.from_object(effect))
    first = PreferencesBus("Input", "ASIO", "machine-a", "input", "CV 3", uuid.UUID(int=33), bus_type="mono")
    second = PreferencesBus("Input", "CoreAudio", "machine-b", "input", "CV 7", uuid.UUID(int=77), bus_type="mono")
    attached.bind("SOURCE", first, kind=SelectorKind.HARDWARE_INPUT)
    attached.bind("DEVICE", HardwareBindings((first, second)), kind=SelectorKind.HARDWARE_INPUT)
    assert decode_binding(m.params(attached.obj)[1]) == first
    assert decode_binding(m.params(attached.obj)[2]).configurations == (first, second)


def test_register_existing_slot_target_descriptors_and_reject_unknown_targets():
    dest = host()
    attached = insert_slot(dest, "fx", Device.from_object(device()))
    before = serialize(dest)
    ports = register_slot_targets(dest)
    assert ports[attached["MIX"].path].owner.obj is attached.obj
    assert serialize(dest) == before
    descriptor = Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 12.0)])
    root = m.get(dest.body, m.F_DEVICE)
    control = Obj(m.REMOTE_CONTROL, [(m.F_RC_TARGET, 8, attached["MIX"].path), (m.F_RC_DESCRIPTOR, 9, descriptor)])
    page = Obj(m.REMOTE_PAGE, [(m.F_PAGE_CONTROLS, 0x12, [control])])
    root.fields.append((m.F_REMOTE_CONTROLS, 9, Obj(m.REMOTE_CONTROLS, [(m.F_REMOTE_PAGES, 0x12, [page])])))
    assert m.get(register_slot_targets(dest)[attached["MIX"].path].descriptor(), 0x125) == 12.0
    with pytest.raises(SlotError):
        register_slot_targets(dest, descriptors={"CONTENTS/MISSING": Obj(m.DESCRIPTOR)})
    with pytest.raises(SlotError):
        attached.register_target("MIX", Obj(0x999))
    descriptor = Obj(m.DESCRIPTOR, [(0x124, 7, -3.0), (0x125, 7, 3.0)])
    port = attached.register_target("MIX", descriptor)
    m.set_field(descriptor, 0x124, -9.0)
    assert m.get(port.descriptor(), 0x124) == -3.0
    with pytest.raises(SlotError):
        attached.register_target("MIX", Obj(0xC6))
    effect = device()
    m.params(effect).append(Obj(m.P_BOOL, [(m.F_NAME, 8, "INVERT_LEFT"), (0x12F, 5, False)]))
    attached = insert_slot(dest, "fx", Device.from_object(effect))
    boolean_descriptor = Obj(0xC6, [(4755, 8, ""), (6957, 5, False)])
    ports = register_slot_targets(dest, descriptors={attached["INVERT_LEFT"].path: boolean_descriptor})
    assert ports[attached["INVERT_LEFT"].path].descriptor().cls == 0xC6


def test_registration_can_address_ambiguous_short_names_by_full_path():
    effect = device()
    effect.fields.append((0x888, 9, Obj(m.P_NUMBER, [(m.F_NAME, 8, "MIX"), (0x136, 7, 0.25)])))
    dest = host()
    attached = insert_slot(dest, "fx", Device.from_object(effect))
    ports = register_slot_targets(dest)
    assert ports[attached.path + "MIX"].path == attached.path + "MIX"
    assert ports[attached.path + "CONTENTS/MIX"].path == attached.path + "CONTENTS/MIX"


def test_relative_nested_path_is_not_mistaken_for_host_prefix():
    effect = device()
    m.params(effect).append(fx_slot("POST_FX", [device("Inner")]))
    dest = host()
    attached = insert_slot(dest, "fx", Device.from_object(effect))
    relative = "CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:CONTENTS/MIX"
    assert attached[relative].path == attached.path + relative
    assert register_slot_targets(dest)[attached.path + relative].path == attached.path + relative
