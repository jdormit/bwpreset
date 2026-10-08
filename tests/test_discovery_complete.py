import uuid

from bwpreset import model as m
from bwpreset.codec import Obj, Preset
from bwpreset.config import installation, library_root
from bwpreset.definitions import module_definition
from bwpreset.schema import Learner, finalize


def definition(kind="modulator"):
    return Preset(
        "0003000200ca00000000000000000000000000",
        [],
        Obj(
            0x6C7 if kind == "modulator" else 0x770,
            [
                (0x18E1, 0x15, uuid.UUID(int=1)),
                (0x18E2, 8, "HW CV In"),
                (0x18E7, 8, "Audio-driven"),
                (0xAD, 0x12, [Obj(0x35B, [(0x10F5, 8, "INPUT")]), Obj(0x2FE, [(0xE38, 8, "OUT")])]),
            ],
        ),
        1,
    )


def test_modulator_definition_has_hardware_binding_metadata():
    obj, _, _ = module_definition(definition(), kind="modulator")
    assert obj.cls == m.MODULATOR
    learner = Learner()
    learner.entry(obj, "modulator", (6, 1, 3))
    result = finalize(learner)["modulators"]["Audio-driven/HW CV In"]
    assert result["params"]["INPUT"]["selector_kind"] == "hardware_input"
    assert result["mod_sources"] == ["OUT"]


def test_data_and_flags_are_discovered_without_saved_presets():
    d = definition("module")
    m.set_field(
        d.body,
        0xAD,
        [Obj(0x74E, [(0x19D6, 8, "DATA"), (0x19D1, 0x17, [1.0, 0.0])]), Obj(0x6D7, [(0x18F3, 8, "BIPOLAR"), (0x18F2, 5, True)])],
    )
    obj, _, _ = module_definition(d)
    p = {m.get(x, m.F_NAME): x for x in m.params(obj)}
    assert m.param_value(p["DATA"]) == [1.0, 0.0]
    assert m.param_value(p["BIPOLAR"]) is True


def test_installation_override_supports_app_bundle_and_library_override(monkeypatch, tmp_path):
    app = tmp_path / "Bitwig.app"
    (app / "Contents").mkdir(parents=True)
    monkeypatch.setenv("BITWIG_HOME", str(app))
    assert installation() == app / "Contents"
    monkeypatch.setenv("BITWIG_LIBRARY_ROOT", str(tmp_path / "library"))
    assert library_root() == tmp_path / "library"
