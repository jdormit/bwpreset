import copy

import pytest

from bwpreset.codec import Obj
from bwpreset import model as m
from bwpreset.state import apply_state, get_state, apply_voice_stack_mode, get_voice_stack_mode


def test_module_state_preserves_unknown_fields_and_has_named_colors():
    obj = Obj(m.MODULE, [(0xA3, 5, True), (0x2643, 1, 0), (0x7777, 8, "keep")])
    assert apply_state(obj, enabled=False, color="mint", width=7, height=3) is obj
    assert get_state(obj) == {"enabled": False, "color": "mint", "width": 7, "height": 3}
    assert (0x2643, 1, 10) in obj.fields
    assert (0x2651, 1, 7) in obj.fields
    assert (0x2652, 1, 3) in obj.fields
    assert (0x7777, 8, "keep") in obj.fields


def test_modulator_per_voice_and_active_map_to_instance_fields():
    obj = Obj(m.MODULATOR, [(0xA3, 5, True), (0x1A19, 5, False)])
    apply_state(obj, active=False, per_voice=True, color="default")
    assert get_state(obj) == {"enabled": False, "per_voice": True, "color": "default"}
    assert (0x2643, 2, 999) in obj.fields
    assert m.get(obj, 0x1A19) is True


def test_poly_remaining_mono_mode_and_voice_stack_counts():
    obj = Obj(m.POLY, [(0x2901, 5, False), (0x28FF, 1, 1), (0x2900, 1, 1)])
    apply_state(obj, mono_mode="digi", voice_stacking=16, voices=64)
    assert get_state(obj) == {"voices": 64, "voice_stacking": 16, "mono_mode": "digi"}
    assert m.get(obj, 0x2901) is True
    apply_state(obj, alternate_mono_voices=False)
    assert get_state(obj)["mono_mode"] == "true"


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": 1},
        {"color": 4},
        {"width": 0},
        {"height": 100},
        {"per_voice": True},
        {"active": False, "enabled": True},
        {"active": True, "enabled": 1},
    ],
)
def test_invalid_state_is_atomic(settings):
    obj = Obj(m.MODULE, [(0xA3, 5, True), (0x7777, 8, "keep")])
    original = copy.deepcopy(obj)
    with pytest.raises(ValueError):
        apply_state(obj, color="blue", **settings) if "color" not in settings else apply_state(obj, **settings)
    assert obj.fields == original.fields


@pytest.mark.parametrize(
    "settings",
    [
        {"voices": 0},
        {"voices": 65},
        {"voice_stacking": 17},
        {"mono_mode": "unknown"},
        {"mono_mode": "digi", "alternate_mono_voices": False},
    ],
)
def test_invalid_poly_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        apply_state(Obj(m.POLY, []), **settings)


def test_unknown_enum_is_reported_instead_of_inventing_a_name():
    with pytest.raises(ValueError, match="color"):
        get_state(Obj(m.MODULE, [(0x2643, 1, 42)]))


@pytest.mark.parametrize("name, value", [("unipolar", 1), ("bipolar", 2), ("index", 3), ("map", 4)])
def test_voice_stack_distribution_modes(name, value):
    parameter = Obj(m.P_ENUM, [(m.F_NAME, 8, "DISTRIBUTION_TYPE"), (0x273, 1, 1), (0x7D3, 5, False)])
    apply_voice_stack_mode(parameter, name)
    assert m.param_value(parameter) == value
    assert get_voice_stack_mode(parameter) == name
    assert (0x7D3, 5, False) in parameter.fields
