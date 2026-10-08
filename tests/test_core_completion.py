import copy
import uuid

import pytest

from bwpreset import model as m
from bwpreset.build import Patch, PatchError, Port
from bwpreset.codec import Obj, Preset, serialize


def param(name, cls, value):
    fid = m.F_SOURCE if cls == m.P_INPUT else m.F_ROUTINGS if cls == m.MOD_SOURCE else m.VALUE_FIELD[cls]
    typ = {m.P_INPUT: 8, m.MOD_SOURCE: 0x12, m.P_ENUM: 3, m.P_INT: 3, m.P_BOOL: 5, m.P_DATA: 0x17}.get(cls, 7)
    return Obj(cls, [(m.F_NAME, 8, name), (fid, typ, value)])


@pytest.fixture
def catalog(monkeypatch):
    from bwpreset import build, decompile

    desc = Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 2.0), (0x126, 1, 5), (0x128, 1, 3)])
    enum = Obj(
        m.ENUM_DESCRIPTOR,
        [
            (
                m.F_ENUM_OPTIONS,
                0x12,
                [
                    Obj(m.ENUM_OPTION, [(m.F_OPTION_INDEX, 1, i), (m.F_OPTION_LABEL, 8, label)])
                    for i, label in [(0, "Hertz"), (1, "Kilohertz"), (4, "Quarter note")]
                ],
            )
        ],
    )
    template = Obj(
        m.MODULE,
        [
            (m.F_NAME, 8, "0"),
            (m.F_TYPE, 0x15, uuid.uuid4()),
            (m.F_PRESET_NAME, 8, ""),
            (m.F_USER_TITLE, 8, ""),
            (m.F_TITLE, 8, "Test"),
            (m.F_X, 1, 0),
            (m.F_Y, 1, 0),
            (
                m.F_MODULE_CONTENTS,
                9,
                Obj(
                    m.CONTENTS,
                    [
                        (
                            m.F_PARAMS,
                            0x12,
                            [
                                param("TIME", m.P_NUMBER, 0.5),
                                param("RATE", m.P_NUMBER, 1.0),
                                param("TIMEBASE", m.P_ENUM, 0),
                                param("IN", m.P_INPUT, ""),
                                param("MOD", m.MOD_SOURCE, []),
                                param("DATA", m.P_DATA, [0.0]),
                            ],
                        )
                    ],
                ),
            ),
        ],
    )
    device = Obj(
        m.DEVICE,
        [
            (m.F_PRESET_NAME, 8, "Test"),
            (m.F_DEVICE_ID, 0x15, uuid.uuid4()),
            (m.F_CONTENTS, 9, Obj(m.CONTENTS, [(m.F_PARAMS, 0x12, [])])),
            (m.F_MODULATORS, 9, Obj(m.MODULATOR_LIST, [(m.F_LIST, 0x12, [])])),
            (m.F_REMOTE_CONTROLS, 9, Obj(m.REMOTE_CONTROLS, [(m.F_REMOTE_PAGES, 0x12, [])])),
            (999, 9, Obj(m.GRID, [(m.F_LIST, 0x12, [])])),
        ],
    )
    preset = Preset(
        "00010002" + "0" * 28 + "\r\n",
        [("device_name", 8, "Test Grid")],
        Obj(m.PRESET, [(m.F_DEVICE, 9, device), (m.F_SELECTION, 0x12, [])]),
        1,
    )
    entry = {
        "id": str(m.get(template, m.F_TYPE)),
        "outputs": ["OUT"],
        "inputs": ["IN"],
        "mod_sources": ["MOD"],
        "params": {m.get(p, m.F_NAME): {"kind": m.PARAM_KINDS[p.cls]} for p in m.params(template)},
    }
    data = (
        {"modules": {"Test": entry}, "modulators": {}, "devices": {}},
        {
            m.get(template, m.F_TYPE): template,
            (m.get(template, m.F_TYPE), "TIME"): desc,
            (m.get(template, m.F_TYPE), "TIMEBASE"): enum,
            (m.get(template, m.F_TYPE), "RATE"): Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 32.0), (0x126, 1, 0), (0x128, 1, 0)]),
        },
        {"Test Grid": preset},
    )
    monkeypatch.setattr(build, "load", lambda: data)
    monkeypatch.setattr(decompile, "load", lambda: data)
    return data


def patch(catalog):
    return Patch("Test Grid", "test")


def test_graph_rejects_foreign_parameters_and_non_outputs(catalog):
    p, q = patch(catalog), patch(catalog)
    a, b = p.add("Test", 0, 0), q.add("Test", 0, 0)
    for source in (b, a["TIME"], Port(a, "NOPE")):
        with pytest.raises(PatchError):
            a.connect(IN=source)
    with pytest.raises(PatchError):
        a.route(b["TIME"], 0.5)
    with pytest.raises(PatchError):
        p.remote(b["TIME"])
    with pytest.raises(PatchError):
        a.out("NOPE")
    assert a.used == {}


def test_failed_multi_value_set_and_remote_are_atomic(catalog):
    p = patch(catalog)
    a = p.add("Test", 0, 0)
    with pytest.raises(PatchError):
        a.set(TIME=0.7, RATE=float("nan"))
    with pytest.raises(PatchError):
        p.remote(a["TIME"], slot=8)
    assert a.used == {} and p.remote_pages == []


def test_named_values_units_and_routing(catalog):
    from bwpreset.units import Hz, Seconds

    p = patch(catalog)
    a = p.add("Test", 0, 0, TIME=Seconds(0.125), TIMEBASE="Kilohertz", RATE=Hz(2000))
    assert m.param_value(a.used["TIME"]) == pytest.approx(0.5)
    assert m.param_value(a.used["RATE"]) == 2
    assert m.param_value(a.used["TIMEBASE"]) == 1
    a.route(a["TIME"], 0.25, normalized=True, mode="rectify_pos", enabled=False)
    r = m.get(a.used["MOD"], m.F_ROUTINGS)[0]
    assert m.get(r, m.F_ROUTE_AMOUNT) == 0.5
    assert m.get(r, m.F_ROUTE_MODE) == 6
    assert m.get(r, m.F_ROUTE_ENABLED) is False
    with pytest.raises(PatchError):
        a.set(TIMEBASE=3)
    with pytest.raises(PatchError):
        a.set(TIME=2.1)
    with pytest.raises(PatchError):
        a.set(DATA=[False])


def test_delete_reindexes_cables_routes_remotes_and_invalidates_handles(catalog):
    p = patch(catalog)
    a, b, c = [p.add("Test", i, 0) for i in range(3)]
    b.connect(IN=a)
    c.connect(IN=b)
    c.route(b["TIME"], 0.5)
    p.remote(c["TIME"])
    p.remote(b["TIME"])
    handle = c["TIME"]
    p.delete(b)
    assert c.index == 1 and handle.path == "CONTENTS/MODULES/1/CONTENTS/TIME"
    assert m.get(c.used["IN"], m.F_SOURCE) == ""
    assert m.get(c.used["MOD"], m.F_ROUTINGS) == []
    assert len(p.remote_pages[0][1]) == 1
    with pytest.raises(PatchError):
        p.remote(b["TIME"])


def test_from_preset_and_lossless_decompile_preserve_unknowns(catalog):
    from bwpreset.decompile import decompile

    p = patch(catalog)
    a = p.add("Test", 0, 0, TIME=0.5)
    a.obj.fields.append((0x7777, 8, "unknown"))
    a.used["TIME"].fields.append((0x7778, 8, "wrapper"))
    original = copy.deepcopy(p.to_preset())
    edited = Patch.from_preset(original)
    assert serialize(edited.to_preset()) == serialize(original)
    edited.modules[0].set(TIME=0.8)
    assert m.get(edited.modules[0].obj, 0x7777) == "unknown"
    assert m.get(edited.modules[0].used["TIME"], 0x7778) == "wrapper"
    code, issues = decompile(original, lossless=True)
    ns = {}
    exec(code, ns)
    assert serialize(ns["p"].to_preset()) == serialize(original)
    assert issues == []


def test_fragment_internal_paths_remap_and_failed_import_is_atomic(catalog):
    p, q = patch(catalog), patch(catalog)
    a, b = p.add("Test", 0, 0), p.add("Test", 1, 0)
    b.connect(IN=a)
    q.add("Test", 0, 0)
    imported = q.import_fragment(p.fragment(a, b), x=5)
    assert m.get(imported[b].used["IN"], m.F_SOURCE) == "CONTENTS/MODULES/1/CONTENTS/OUT"
    assert m.get(imported[a].obj, m.F_X) == 5
    with pytest.raises(PatchError):
        q.import_fragment(p.fragment(b))
    assert len(q.modules) == 3


def test_fragment_snapshot_survives_source_deletion(catalog):
    p, q = patch(catalog), patch(catalog)
    x, a, b = [p.add("Test", i, 0) for i in range(3)]
    b.connect(IN=a)
    fragment = p.fragment(a, b)
    p.delete(x)
    imported = q.import_fragment(fragment)
    assert m.get(imported[b].used["IN"], m.F_SOURCE) == imported[a].out().path


def test_import_delete_preserves_raw_pages_and_clears_selection(catalog):
    p = patch(catalog)
    a, b = p.add("Test", 0, 0), p.add("Test", 1, 0)
    p.remote(a["TIME"], slot=3)
    p.remote(b["TIME"], slot=6)
    original = p.to_preset()
    original.meta += [("referenced_module_ids", 0x19, [str(a.type_id)]), ("orig_file_checksum", 8, "stale")]
    m.set_field(p.preset.body, m.F_SELECTION, [a.obj, b.obj])
    edited = Patch.from_preset(original)
    edited.delete(edited.modules[0])
    preset = edited.to_preset()
    assert m.get(preset.body, m.F_SELECTION) == [edited.modules[0].obj]
    pages = m.get(m.get(edited.device, m.F_REMOTE_CONTROLS), m.F_REMOTE_PAGES)
    assert [m.get(rc, m.F_RC_INDEX) for rc in m.get(pages[0], m.F_PAGE_CONTROLS)] == [6]
    assert m.get(m.get(pages[0], m.F_PAGE_CONTROLS)[0], m.F_RC_TARGET) == edited.modules[0]["TIME"].path
    assert "orig_file_checksum" not in {k for k, _, _ in preset.meta}
    edited.delete(edited.modules[0])
    assert dict((k, v) for k, _, v in edited.to_preset().meta)["referenced_module_ids"] == []


def test_fragment_binding_replaces_target_descriptor(catalog):
    p, q = patch(catalog), patch(catalog)
    source, target = p.add("Test", 0, 0), p.add("Test", 1, 0)
    replacement = q.add("Test", 0, 0)
    source.route(target["TIME"], 0.5)
    imported = q.import_fragment(p.fragment(source), bindings={target["TIME"].path: replacement["RATE"]})
    route = m.get(imported[source].used["MOD"], m.F_ROUTINGS)[0]
    assert m.get(route, m.F_ROUTE_TARGET) == replacement["RATE"].path
    assert m.get(m.get(route, m.F_ROUTE_DESCRIPTOR), 0x125) == 32


def test_normalized_depth_requires_real_range(catalog):
    p = patch(catalog)
    a = p.add("Test", 0, 0)
    del catalog[1][(a.type_id, "RATE")]
    with pytest.raises(PatchError):
        a.route(a["RATE"], 0.5, normalized=True)
    assert a.used == {}


@pytest.mark.parametrize(
    "unit,scaling,value,expected",
    [
        (3, 5, ("Seconds", 0.125), 0.5),
        (18, 0, ("Seconds", 0.5), 0.5),
        (4, 2, ("Hz", 440), 69),
        (4, 3, ("Hz", 10), 1),
        (3, 4, ("Seconds", 2), 0.5),
        (2, 1, ("Decibels", -6), -6),
        (2, 0, ("Decibels", -6.020599913279624), 0.5),
        (2, 5, ("Decibels", -18.06179973983887), 0.5),
    ],
)
def test_descriptor_physical_conversions(unit, scaling, value, expected):
    from bwpreset import units

    descriptor = Obj(m.DESCRIPTOR, [(0x128, 1, unit), (0x126, 1, scaling)])
    physical = getattr(units, value[0])(value[1])
    assert units.to_stored(physical, descriptor) == pytest.approx(expected)


def test_synced_rate_requires_explicit_meter_and_rejects_hz(catalog):
    from bwpreset.units import Beats, Hz, Rate

    a = patch(catalog).add("Test", 0, 0, TIMEBASE="Quarter note", RATE=Beats(0.5))
    assert m.param_value(a.used["RATE"]) == 2
    a.set(RATE=Rate(3))
    with pytest.raises(PatchError):
        a.set(RATE=Hz(2))
    descriptor = catalog[1][(a.type_id, "RATE")]
    from bwpreset.units import to_stored

    with pytest.raises(ValueError, match="beats_per_bar"):
        to_stored(Beats(2), descriptor, timebase=2)
    assert to_stored(Beats(2, beats_per_bar=3), descriptor, timebase=2) == 1.5


def test_unknown_duplicate_parameters_survive_unrelated_edit(catalog):
    p = patch(catalog)
    a = p.add("Test", 0, 0, TIME=0.5)
    params = m.params(a.obj)
    params += [Obj(0x999, [(m.F_NAME, 8, "OPAQUE"), (0x55, 8, "one")]), Obj(0x999, [(m.F_NAME, 8, "OPAQUE"), (0x55, 8, "two")])]
    edited = Patch.from_preset(p.to_preset())
    edited.modules[0].set(TIME=0.7)
    assert [m.get(o, 0x55) for o in m.params(edited.modules[0].obj) if o.cls == 0x999] == ["one", "two"]


def test_binding_and_state_integrate_atomically(catalog):
    from bwpreset.bindings import DeviceSource, SOURCE_PARAMETER

    template = next(v for k, v in catalog[1].items() if isinstance(k, uuid.UUID))
    m.params(template).append(Obj(SOURCE_PARAMETER, [(m.F_NAME, 8, "BINDING"), (0x10FF, 10, None)]))
    a = patch(catalog).add("Test", 0, 0, BINDING=DeviceSource("CONTENTS"))
    assert m.get(a.used["BINDING"], 0x10FF).cls == 0x573
    a.state(color="blue", enabled=False, width=5)
    assert m.get(a.obj, m.F_COLOR) == 4 and m.get(a.obj, 0xA3) is False
    before = serialize(a.patch.to_preset())
    with pytest.raises(PatchError):
        a.state(enabled=True, width=0)
    assert serialize(a.patch.to_preset()) == before


def test_readable_decompiler_preserves_routing_flags_and_instance_state(catalog):
    from bwpreset.decompile import decompile

    p = patch(catalog)
    a = p.add("Test", 0, 0, TIME=0.5).state(color="blue", enabled=False, width=4)
    a.route(a["TIME"], 0.5, mode="cubic", enabled=False)
    code, issues = decompile(p.to_preset())
    ns = {}
    exec(code, ns)
    rebuilt = ns["p"].modules[0]
    assert m.get(rebuilt.obj, 0xA3) is False and m.get(rebuilt.obj, 0x2651) == 4
    route = m.get(rebuilt.used["MOD"], m.F_ROUTINGS)[0]
    assert m.get(route, m.F_ROUTE_ENABLED) is False and m.get(route, m.F_ROUTE_MODE) == 4
    assert not issues


def test_asset_assignment_tracks_dependencies_and_failed_batch_is_atomic(catalog, tmp_path):
    import wave
    from bwpreset.assets import AudioFile, Sample, SAMPLE_PARAM

    wav_path = tmp_path / "tiny.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setparams((1, 2, 44100, 4, "NONE", "not compressed"))
        wav.writeframes(b"\0" * 8)
    template = next(v for k, v in catalog[1].items() if isinstance(k, uuid.UUID))
    m.params(template).append(Obj(SAMPLE_PARAM, [(m.F_NAME, 8, "SAMPLE"), (0x74C, 10, None)]))
    p = patch(catalog)
    a = p.add("Test", 0, 0, SAMPLE=Sample(AudioFile.from_file(wav_path)))
    assert p.dependencies()[0].source_path == str(wav_path)
    before = serialize(p.to_preset())
    with pytest.raises(PatchError):
        a.set(TIME=0.7, SAMPLE=object())
    assert serialize(p.to_preset()) == before


def test_slot_target_ownership_and_dependency_metadata(catalog):
    from bwpreset.slots import Device

    p, q = patch(catalog), patch(catalog)
    chain = Obj(
        m.FX_SLOT,
        [(m.F_NAME, 8, "POST_FX"), (m.F_CHAIN, 9, Obj(0x200, [(m.F_CHAIN_DEVICES, 9, Obj(0x201, [(m.F_CHAIN_LIST, 0x12, [])]))]))],
    )
    m.params(p.device).append(chain)
    child = copy.deepcopy(q.device)
    m.params(child).append(param("GAIN", m.P_NUMBER, 0.5))
    desc = catalog[1][next(k for k in catalog[1] if isinstance(k, tuple) and k[1] == "TIME")]
    attached = p.slot("fx", Device.from_object(child, descriptors={"CONTENTS/GAIN": desc}))
    a = p.add("Test", 0, 0)
    a.route(attached["GAIN"], 0.5)
    p.remote(attached["GAIN"])
    p.remote(p.slot_device("fx", 0)["GAIN"])
    with pytest.raises(PatchError):
        q.remote(attached["GAIN"])
    assert str(m.get(child, m.F_DEVICE_ID)) in dict((k, v) for k, _, v in p.to_preset().meta)["referenced_device_ids"]
    edited = Patch.from_preset(p.to_preset())
    edited.remote(edited.slot_device("fx")["GAIN"])


def test_delete_modulation_scale_clears_scale_and_preserves_live_routings(catalog):
    p = patch(catalog)
    source, scale, target = [p.add("Test", i, 0) for i in range(3)]
    source.route(target["TIME"], 0.5, scale=scale["MOD"])
    source.route(target["TIME"], 0.8)
    p.delete(scale)
    routes = m.get(source.used["MOD"], m.F_ROUTINGS)
    assert [m.get(r, m.F_ROUTE_AMOUNT) for r in routes] == [0.5, 0.8]
    assert m.get(routes[0], m.F_ROUTE_SCALE_SOURCE) == ""
    assert m.get(routes[0], m.F_ROUTE_TARGET) == target["TIME"].path


def test_lossless_cli_output_retains_opaque_state_and_attachment(catalog, tmp_path, capsys):
    from bwpreset.cli import main

    p = patch(catalog)
    a = p.add("Test", 0, 0, TIME=0.5)
    a.obj.fields.append((0x7777, 8, "opaque"))
    p.preset.attachment = b"opaque attachment"
    data = serialize(p.to_preset())
    path = tmp_path / "source.bwpreset"
    path.write_bytes(data)
    main(["decompile", str(path)])
    code = capsys.readouterr().out
    ns = {}
    exec(code, ns)
    assert serialize(ns["p"].to_preset()) == data


def test_physical_rate_writes_explicit_hertz_default(catalog):
    from bwpreset.units import Hz

    a = patch(catalog).add("Test", 0, 0, RATE=Hz(2))
    assert m.param_value(a.used["TIMEBASE"]) == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 10**400])
def test_numeric_validation_is_finite_and_atomic(catalog, value):
    a = patch(catalog).add("Test", 0, 0)
    with pytest.raises(PatchError):
        a.set(TIME=0.5, RATE=value)
    assert a.used == {}


def test_import_uses_original_control_descriptor_when_discovery_lacks_it(catalog):
    from bwpreset.decompile import decompile

    template = next(v for k, v in catalog[1].items() if isinstance(k, uuid.UUID))
    type_id = m.get(template, m.F_TYPE)
    m.params(template).append(param("FLAG", m.P_BOOL, False))
    catalog[0]["modules"]["Test"]["params"]["FLAG"] = {"kind": "bool"}
    catalog[1][(type_id, "FLAG")] = copy.deepcopy(catalog[1][(type_id, "TIME")])
    p = patch(catalog)
    a = p.add("Test", 0, 0, FLAG=True)
    p.remote(a["FLAG"])
    original = copy.deepcopy(p.to_preset())
    del catalog[1][(type_id, "FLAG")]
    imported = Patch.from_preset(original)
    imported.remote(imported.modules[0]["FLAG"])
    code, issues = decompile(original)
    ns = {}
    exec(code, ns)
    assert serialize(ns["p"].to_preset()) == serialize(original)
    assert any("descriptor" in issue for issue in issues)


def test_readable_decompiler_scopes_root_grid_before_nested_grid(catalog):
    from bwpreset.decompile import decompile

    p, child = patch(catalog), patch(catalog)
    p.add("Test", 0, 0, TIME=0.5)
    child.add("Test", 0, 0, TIME=0.8)
    nested = child.to_preset().body
    p.device.fields.insert(0, (0x7777, 9, nested))
    code, _ = decompile(p.to_preset())
    ns = {}
    exec(code, ns)
    assert m.param_value(ns["p"].modules[0].used["TIME"]) == 0.5


def test_packaged_asset_metadata_and_asset_free_fragments(catalog):
    from bwpreset.assets import AudioFile, FileReference, Sample, SAMPLE_PARAM

    template = next(v for k, v in catalog[1].items() if isinstance(k, uuid.UUID))
    m.params(template).append(Obj(SAMPLE_PARAM, [(m.F_NAME, 8, "SAMPLE"), (0x74C, 10, None)]))
    p, q = patch(catalog), patch(catalog)
    reference = FileReference(package_path="factory/short.wav")
    sampled = p.add("Test", 0, 0, SAMPLE=Sample(AudioFile(reference, sample_rate=44100, frames=441, channels=1)))
    plain = p.add("Test", 1, 0)
    metadata = dict((k, v) for k, _, v in p.to_preset().meta)
    assert metadata["referenced_packaged_file_ids"] == ["factory/short.wav"]
    p.preset.attachment = b"source resources"
    q.preset.attachment = b"destination resources"
    q.import_fragment(p.fragment(plain))
    assert q.preset.attachment == b"destination resources"
    assert p.fragment(sampled).dependencies == (("referenced_packaged_file_ids", ("factory/short.wav",)),)


def test_sparse_import_optional_containers_preserve_then_accept_remotes(catalog):
    p = patch(catalog)
    p.add("Test", 0, 0, TIME=0.5)
    original = copy.deepcopy(p.to_preset())
    device = m.get(original.body, m.F_DEVICE)
    device.fields = [(f, t, v) for f, t, v in device.fields if f not in (m.F_MODULATORS, m.F_REMOTE_CONTROLS)]
    imported = Patch.from_preset(original)
    assert serialize(imported.to_preset()) == serialize(original)
    imported.remote(imported.modules[0]["TIME"])
    assert m.get(imported.device, m.F_REMOTE_CONTROLS) is None
    imported.to_preset()
    assert m.get(imported.device, m.F_REMOTE_CONTROLS).cls == m.REMOTE_CONTROLS
    imported.delete(imported.modules[0])
    assert imported.modules == []


def test_unknown_voice_choice_reports_lossless_fallback(catalog):
    from bwpreset.decompile import decompile

    p = patch(catalog)
    p.device.fields.append((0x7777, 9, Obj(m.POLY, [(0x40D6, 1, 99)])))
    original = p.to_preset()
    code, issues = decompile(original)
    ns = {}
    exec(code, ns)
    assert any("note_priority=99" in issue for issue in issues)
    assert serialize(ns["p"].to_preset()) == serialize(original)


def test_default_filename_keeps_display_names_inside_output_directory(catalog, monkeypatch, tmp_path):
    from bwpreset import build

    monkeypatch.setattr(build, "USER_PRESETS", tmp_path)
    p = Patch("Test Grid", "../A/B")
    output = p.save()
    assert output.parent == tmp_path / "bwpreset"
    assert p.name == "../A/B" and output.name == ".._A_B.bwpreset"


def test_import_restores_definition_selector_kind_for_binding_validation(catalog):
    from bwpreset.bindings import DeviceSource, NoSource, SelectorKind, SOURCE_PARAMETER

    template = next(v for k, v in catalog[1].items() if isinstance(k, uuid.UUID))
    binding = Obj(SOURCE_PARAMETER, [(m.F_NAME, 8, "BINDING"), (0x10FF, 10, None)])
    binding.binding_kind = SelectorKind.HARDWARE_INPUT
    m.params(template).append(binding)
    p = patch(catalog)
    p.add("Test", 0, 0, BINDING=NoSource())
    imported = Patch.from_preset(serialize(p.to_preset()))
    before = serialize(imported.to_preset())
    with pytest.raises(PatchError, match="hardware selectors"):
        imported.modules[0].set(BINDING=DeviceSource("CONTENTS"))
    assert serialize(imported.to_preset()) == before
