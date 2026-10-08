import uuid

import pytest

from bwpreset import model as m
from bwpreset.bindings import (
    BindingError,
    DeviceSource,
    HardwarePort,
    PreferencesBus,
    HardwareBindings,
    NoSource,
    TrackSource,
    apply_binding,
    decode_binding,
    parameter_from_definition,
    SelectorKind,
)
from bwpreset.codec import Obj, Preset, parse, serialize


def parameter(cls=0x37C, name="SOURCE"):
    return Obj(cls, [(m.F_NAME, 8, name), (0x10FF if cls == 0x37C else 0x10FE, 10, None), (0x7777, 8, "keep")])


@pytest.mark.parametrize(
    "source",
    [
        NoSource(),
        DeviceSource("CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:AUDIO_OUTPUT"),
        DeviceSource("AUDIO_OUTPUT", bus_type="unsupported"),
        DeviceSource("CONTENTS/DRUM_PADS/PAD36/MIXER/PRE_FADER", bus_type="mono", relative_to="device_chain"),
        DeviceSource("NOTE_OUTPUT", signal="note", relative_to="device_chain"),
        TrackSource(uuid.UUID(int=12), "audio", "stereo"),
        TrackSource(uuid.UUID(int=13), "note"),
        TrackSource(uuid.UUID(int=14), pre_fader=True),
        HardwarePort("Interface", "CoreAudio", "machine", "input", "device", ("in-3",), client_id="client"),
        HardwarePort("Interface", "CoreAudio", "machine", "input", "device", ("in-3", "in-4")),
    ],
)
def test_named_binding_roundtrip(source):
    original = parameter()
    result = apply_binding(original, source)
    assert result is not original
    assert m.get(original, 0x10FF) is None
    assert m.get(result, 0x7777) == "keep"
    preset = Preset("00010002000000000000000000000000000000", [], result, 1)
    assert decode_binding(parse(serialize(preset)).body) == source


def test_output_hardware_and_no_source():
    source = HardwarePort("Interface", "CoreAudio", "machine", "output", "device", ("out-7",))
    bound = apply_binding(parameter(0x37B, "OUTPUT"), source)
    assert decode_binding(bound) == source
    assert decode_binding(apply_binding(bound, NoSource())) == NoSource()


def test_ownership_and_direction_validation():
    assert DeviceSource("AUDIO_INPUT").scope == "device-local"
    assert TrackSource(uuid.UUID(int=1), "note").scope == "project-local"
    with pytest.raises(BindingError):
        apply_binding(parameter(), Obj(1))
    with pytest.raises(BindingError):
        apply_binding(parameter(0x37B), TrackSource(uuid.UUID(int=1), "note"))
    with pytest.raises(BindingError):
        apply_binding(parameter(), HardwarePort("i", "a", "m", "output", "d", ("p",)))
    with pytest.raises(BindingError):
        TrackSource("not-a-uuid", "audio")
    with pytest.raises(BindingError):
        HardwarePort("i", "a", "m", "input", "d", ())


def test_unknown_payload_is_reported_not_passthrough():
    with pytest.raises(BindingError, match="unsupported"):
        decode_binding(Obj(0x37C, [(0x10FF, 9, Obj(0x999))]))


def test_hardware_edit_preserves_unknown_configuration_fields():
    source = HardwarePort("Interface", "CoreAudio", "machine", "input", "device", ("in-3",))
    parameter = apply_binding(Obj(0x37C, [(m.F_NAME, 8, "INPUT")]), source)
    payload = m.get(parameter, 0x10FF)
    m.get(payload, 4496)[0].fields.append((0x777, 8, "keep config"))
    updated = apply_binding(parameter, HardwarePort("Interface", "CoreAudio", "machine", "input", "device", ("in-4",)))
    assert m.get(m.get(m.get(updated, 0x10FF), 4496)[0], 0x777) == "keep config"
    assert decode_binding(parameter).channel_ids == ("in-3",)


def test_hardware_labels_are_independent_and_reapply_preserves_field_order():
    source = HardwarePort(
        "User label", "CoreAudio", "machine", "input", "device", ("in-3",), client_name="Driver client", device_name="Backend device"
    )
    bound = apply_binding(parameter(), source)
    assert decode_binding(bound) == source
    before = serialize(Preset("00010002000000000000000000000000000000", [], bound, 1))
    after = serialize(Preset("00010002000000000000000000000000000000", [], apply_binding(bound, decode_binding(bound)), 1))
    assert after == before


def test_definition_kind_validation_and_explicit_kind_after_parse():
    audio = parameter_from_definition(Obj(0x359, [(0x10F5, 8, "SOURCE")]))
    note = parameter_from_definition(Obj(0x35C, [(0x10F5, 8, "SOURCE")]))
    hardware = parameter_from_definition(Obj(0x35B, [(0x10F5, 8, "INPUT")]))
    with pytest.raises(BindingError):
        apply_binding(audio, TrackSource(uuid.UUID(int=2), "note"))
    with pytest.raises(BindingError):
        apply_binding(note, DeviceSource("AUDIO_OUTPUT"))
    with pytest.raises(BindingError):
        apply_binding(hardware, DeviceSource("AUDIO_INPUT"))
    with pytest.raises(BindingError):
        apply_binding(parameter(), TrackSource(uuid.UUID(int=2), "note"), kind=SelectorKind.AUDIO_SIDECHAIN)
    assert decode_binding(apply_binding(note, DeviceSource("NOTE_OUTPUT", signal="note"))) == DeviceSource("NOTE_OUTPUT", signal="note")
    parsed = parse(serialize(Preset("00010002000000000000000000000000000000", [], audio, 1))).body
    assert not hasattr(parsed, "binding_kind")
    with pytest.raises(BindingError):
        apply_binding(parsed, TrackSource(uuid.UUID(int=2), "note"), kind=SelectorKind.AUDIO_SIDECHAIN)


def test_source_wrapper_rejects_destination_payload_on_decode():
    source = HardwarePort("i", "a", "m", "output", "d", ("p",))
    payload = m.get(apply_binding(parameter(0x37B), source), 0x10FE)
    with pytest.raises(BindingError):
        decode_binding(Obj(0x37C, [(0x10FF, 9, payload)]))


@pytest.mark.parametrize(
    "atom_cls,name,param_cls",
    [(0x359, "SOURCE", 0x37C), (0x35C, "NOTE_SIDECHAIN", 0x37C), (0x35B, "INPUT", 0x37C), (0x35A, "OUTPUT", 0x37B)],
)
def test_definition_selectors(atom_cls, name, param_cls):
    atom = Obj(atom_cls, [(0x10F5, 8, name), (0x10FB, 8, "No Input")])
    result = parameter_from_definition(atom)
    assert result.cls == param_cls
    assert m.get(result, m.F_NAME) == name
    assert decode_binding(result) == NoSource()


def legacy_config(machine="machine-a", api="ASIO", bus_uuid=uuid.UUID(int=21)):
    return Obj(
        0x45B,
        [
            (2022, 8, "User input"),
            (2023, 8, machine),
            (4513, 8, api),
            (4514, 8, "configuration-a"),
            (7448, 8, "Input 3+4"),
            (2024, 0x15, bus_uuid),
        ],
    )


@pytest.mark.parametrize("direction,cls,fid", [("input", 0x37C, 0x10FF), ("output", 0x37B, 0x10FE)])
def test_legacy_preferences_bus_field_mapping(direction, cls, fid):
    payload = Obj(
        0x42A if direction == "input" else 0x427,
        ([(4497, 1, 1)] if direction == "input" else []) + [(4496 if direction == "input" else 4493, 0x12, [legacy_config()])],
    )
    original = Obj(cls, [(m.F_NAME, 8, "DEVICE"), (fid, 9, payload)])
    expected = PreferencesBus(
        "User input", "ASIO", "machine-a", direction, "Input 3+4", uuid.UUID(int=21), configuration_id="configuration-a"
    )
    decoded = decode_binding(original)
    assert decoded == expected
    assert decoded.scope == "machine-local"
    p = Preset("00010002000000000000000000000000000000", [], original, 1)
    assert serialize(Preset(p.header, [], apply_binding(original, decoded), 1)) == serialize(p)


def test_multi_machine_mixed_configs_and_unknown_fields_follow_reordering():
    config_a, config_b = legacy_config(), legacy_config("machine-b", "CoreAudio", uuid.UUID(int=22))
    config_a.fields.append((0x777, 8, "belongs to a"))
    config_b.fields.append((0x777, 8, "belongs to b"))
    original = Obj(0x37C, [(m.F_NAME, 8, "SOURCE"), (0x10FF, 9, Obj(0x42A, [(4497, 1, 1), (4496, 0x12, [config_a, config_b])]))])
    decoded = decode_binding(original)
    assert isinstance(decoded, HardwareBindings)
    assert [c.machine_id for c in decoded.configurations] == ["machine-a", "machine-b"]
    new_port = HardwarePort("User input", "ALSA", "machine-c", "input", "device", ("port-l", "port-r"))
    edited = apply_binding(original, HardwareBindings((decoded.configurations[1], new_port, decoded.configurations[0])))
    configs = m.get(m.get(edited, 0x10FF), 4496)
    assert [m.get(c, 0x777) for c in configs] == ["belongs to b", None, "belongs to a"]
    assert decode_binding(edited).configurations == (decoded.configurations[1], new_port, decoded.configurations[0])
    assert len(m.get(m.get(original, 0x10FF), 4496)) == 2


def test_multi_machine_channel_modes_and_direction_are_explicit():
    mono = HardwarePort("Input", "ASIO", "machine-a", "input", "device", ("p1",))
    stereo = HardwarePort("Input", "CoreAudio", "machine-b", "input", "device", ("l", "r"))
    with pytest.raises(BindingError, match="bus_type"):
        HardwareBindings((mono, stereo))
    bindings = HardwareBindings((mono, stereo), bus_type="unsupported")
    assert decode_binding(apply_binding(parameter(), bindings)) == bindings
    with pytest.raises(BindingError):
        HardwareBindings((mono, HardwarePort("Output", "ASIO", "machine-a", "output", "d", ("p",))))
    with pytest.raises(BindingError):
        apply_binding(parameter(0x37B), bindings)
    empty = HardwareBindings((), direction="input", bus_type="unsupported")
    assert decode_binding(apply_binding(parameter(), empty)) == empty
    output = HardwareBindings((), direction="output")
    assert decode_binding(apply_binding(parameter(0x37B), output)) == output


def test_same_machine_port_reorder_and_edit_preserve_profile_data():
    a = HardwarePort("Input", "ASIO", "machine", "input", "device", ("a",))
    b = HardwarePort("Input", "ASIO", "machine", "input", "device", ("b",))
    original = apply_binding(parameter(), HardwareBindings((a, b)))
    configs = m.get(m.get(original, 0x10FF), 4496)
    configs[0].fields.append((0x777, 8, "a"))
    configs[1].fields.append((0x777, 8, "b"))
    reordered = apply_binding(original, HardwareBindings((b, a)))
    assert [m.get(c, 0x777) for c in m.get(m.get(reordered, 0x10FF), 4496)] == ["b", "a"]


def test_optional_port_labels_and_preferences_uuid_defaults_are_named():
    value = HardwarePort(
        "User label",
        "CoreAudio",
        "machine",
        "input",
        "device",
        ("l", "r"),
        port_name="Line 3/4",
        user_defined_device_name="Rack",
        user_defined_port_name="CV",
        is_default_recording_input=False,
        port_id="stereo-port-identifier",
    )
    assert decode_binding(apply_binding(parameter(), value)) == value
    prefs = PreferencesBus("", "", "", "input", "", None, bus_type="unsupported")
    assert decode_binding(apply_binding(parameter(), prefs)) == prefs
    assert m.get(m.get(m.get(apply_binding(parameter(), prefs), 0x10FF), 4496)[0], 2024) is None
    omitted_width = HardwareBindings((value,), bus_type=None)
    edited = apply_binding(apply_binding(parameter(), value), omitted_width)
    assert m.get(m.get(edited, 0x10FF), 4497) is None
    assert decode_binding(edited) == omitted_width


def test_multi_machine_output_configs_and_explicit_machine_filter():
    a = HardwarePort("Output", "ASIO", "a", "output", "d", ("p",))
    b = PreferencesBus("Output", "CoreAudio", "b", "output", "CV 7", uuid.UUID(int=7))
    binding = HardwareBindings((a, b))
    assert binding.for_machine("b", "CoreAudio") == (b,)
    assert binding.for_machine("b", "ASIO") == ()
    assert decode_binding(apply_binding(parameter(0x37B), binding)) == binding
    with pytest.raises(BindingError):
        apply_binding(parameter(), binding, kind=SelectorKind.HARDWARE_INPUT)


def test_clearing_optional_fields_does_not_preserve_them_as_unknown():
    value = HardwarePort("Input", "ASIO", "machine", "input", "device", ("p",), port_name="Old label", is_default_recording_input=True)
    bound = apply_binding(parameter(), value)
    changed = HardwarePort("Input", "ASIO", "machine", "input", "device", ("p",))
    assert decode_binding(apply_binding(bound, changed)) == changed


def test_profile_reorder_reserves_all_exact_matches_before_edited_fallback():
    a = HardwarePort("Input", "ASIO", "machine", "input", "device", ("a",))
    b = HardwarePort("Input", "ASIO", "machine", "input", "device", ("b",))
    c = HardwarePort("Input", "ASIO", "machine", "input", "device", ("c",))
    original = apply_binding(parameter(), HardwareBindings((a, b)))
    configs = m.get(m.get(original, 0x10FF), 4496)
    configs[0].fields.append((0x777, 8, "a"))
    configs[1].fields.append((0x777, 8, "b"))
    edited = apply_binding(original, HardwareBindings((c, a)))
    assert [m.get(config, 0x777) for config in m.get(m.get(edited, 0x10FF), 4496)] == ["b", "a"]


def test_preferences_profiles_do_not_invent_or_silently_override_wrapper_mode():
    profile = PreferencesBus("Input", "ASIO", "machine", "input", "CV", uuid.UUID(int=1), bus_type="mono")
    with pytest.raises(BindingError):
        HardwareBindings((profile,), bus_type="stereo")
    with pytest.raises(BindingError):
        HardwareBindings((), direction="input")
    absent = PreferencesBus("Input", "ASIO", "machine", "input", "CV", uuid.UUID(int=1), bus_type=None)
    decoded = decode_binding(apply_binding(parameter(), absent))
    assert decoded == absent
    assert HardwareBindings((decoded,)).bus_type is None


def test_preferences_bus_requires_bus_identity_not_channel_guesses():
    with pytest.raises(BindingError):
        PreferencesBus("Input", "ASIO", "machine", "input", "Input 1", "not-a-uuid")
    with pytest.raises(BindingError):
        HardwareBindings((Obj(0x45B),), direction="input")


@pytest.mark.parametrize("name", ["INPUT", "SOURCE", "DEVICE"])
def test_hardware_selector_identifiers_are_not_hardcoded(name):
    p = parameter_from_definition(Obj(0x35B, [(0x10F5, 8, name)]))
    binding = PreferencesBus("Input", "ASIO", "machine", "input", "Input 3", uuid.UUID(int=4), bus_type="mono")
    result = apply_binding(p, binding)
    assert m.get(result, m.F_NAME) == name
    assert decode_binding(result) == binding
