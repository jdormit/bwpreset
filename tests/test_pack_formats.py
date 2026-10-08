import pytest

from bwpreset import model as m
from bwpreset.build import Patch
from bwpreset.codec import Obj, Preset, Tagged, parse, serialize, walk
from bwpreset.schema import BITWIG, load


def test_maps_and_tagged_backreferences_round_trip():
    shared = Obj(99, [(1, 7, 1.5)])
    root = Obj(
        100, [(1, 0x14, {"a": shared, "b": shared}), (2, 0x1A, Tagged(shared, "tag")), (3, 0x18, [0.125, 1.5]), (4, 0x0F, [-1, 65536])]
    )
    preset = Preset("0003000200ca00000000000000000000000000", [], root, 1, b"PK\x03\x04test")
    data = serialize(preset)
    result = parse(data)
    assert serialize(result) == data
    f = m.fields(result.body)
    assert f[1]["a"] is f[1]["b"] is f[2].value
    assert len(list(walk(result.body))) == 2
    assert result.attachment == preset.attachment


@pytest.mark.parametrize("name", ["Bite", "Heat", "Tuner", "XY", "CV In"])
def test_encoded_definitions_normalize_to_readable_files(name):
    path = BITWIG / "Resources/Library/modules" / f"{name}.bwmodule"
    definition = parse(path.read_bytes())
    normalized = serialize(definition)
    assert normalized[8:12] == b"0002"
    assert serialize(parse(normalized)) == normalized
    assert dict((k, v) for k, _, v in definition.meta)["device_name"] == name


def test_all_shipped_modules_are_available_to_builder(tmp_path):
    schema = load()[0]
    ids = {entry["id"] for entry in schema["modules"].values()}
    for path in (BITWIG / "Resources/Library/modules").glob("*.bwmodule"):
        assert str(m.get(parse(path.read_bytes()).body, 0x18E1)) in ids
    for kind in schema["modules"]:
        patch = Patch("Poly Grid", "one module")
        module = patch.add(kind, 0, 0)
        preset = parse(patch.save(tmp_path / "one.bwpreset").read_bytes())
        assert any(o.cls == m.MODULE and m.get(o, m.F_TYPE) == module.type_id for o in walk(preset.body))


def test_definition_can_supply_a_module_without_a_preset_example():
    from bwpreset.definitions import module_definition

    definition = parse((BITWIG / "Resources/Library/modules/Bite.bwmodule").read_bytes())
    module, descriptors, outputs = module_definition(definition)
    assert m.get(module, m.F_TITLE) == "Bite"
    parameters = {m.get(p, m.F_NAME): p for p in m.params(module)}
    assert parameters["OSC_A_LEVEL"].cls == m.P_NUMBER
    assert parameters["PHASE_IN"].cls == m.P_INPUT
    assert parameters["KEYTRACK"].cls == m.P_BOOL
    assert m.get(descriptors["OSC_A_LEVEL"], 0x125) == 1.0
    assert "OUT" in outputs
