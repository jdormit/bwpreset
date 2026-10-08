from pathlib import Path

import pytest

from bwpreset import model as m
from bwpreset.build import Patch, PatchError
from bwpreset.codec import Obj, parse, serialize, walk
from bwpreset.curves import Curve
from bwpreset.decompile import decompile


def curve_param(patch):
    preset = parse(serialize(patch.to_preset()))
    return next(o for o in walk(preset.body) if o.cls == 0x128C)


def test_points_are_written_in_bitwig_order():
    p = Patch("Poly Grid", "curve")
    p.add("Envelope/Segments", 0, 0, CURVE=Curve([(0, 0), (0.2, 1, -0.5), (1, 0)]))
    inner = m.get(curve_param(p), 0x361D)
    assert [(m.get(pt, 0x35FE), m.get(pt, 0x35FD), m.get(pt, 0x35FF)) for pt in m.get(inner, 0x3603)] == [
        (0, 0, 0),
        (0.2, 1, -0.5),
        (1, 0, 0),
    ]
    assert m.get(inner, 0x360B) != "Spike"


def test_vertical_steps_are_valid():
    Curve([(0, 0), (0.5, 0), (0.5, 1), (1, 1)])


@pytest.mark.parametrize(
    "points", [[], [(0, 0)], [(0.8, 0), (0.2, 1)], [(-0.1, 0), (1, 1)], [(0, 0), (1, float("nan"))], [(0, 0), (1, 1, 2)]]
)
def test_invalid_points_are_rejected(points):
    with pytest.raises(ValueError):
        Curve(points)


def test_wrong_curve_value_does_not_mutate_patch():
    p = Patch("Poly Grid", "curve")
    seg = p.add("Envelope/Segments", 0, 0)
    with pytest.raises(PatchError):
        seg.set(RATE=1.0, CURVE="wrong")
    assert seg.used == {}


def test_curve_decompiles_without_losing_points_or_settings():
    p = Patch("Poly Grid", "curve")
    p.add("Envelope/Segments", 0, 0, CURVE=Curve([(0, 0), (0.5, 1, 0.4), (1, 0)]))
    code, unsupported = decompile(parse(serialize(p.to_preset())))
    assert not any("CURVE" in u for u in unsupported)
    ns = {}
    exec(code, ns)
    assert repr(Curve.from_object(m.get(curve_param(ns["p"]), 0x361D))) == repr(Curve.from_object(m.get(curve_param(p), 0x361D)))


def test_import_preserves_factory_curve_data():
    path = Path.home() / "Library/Application Support/Bitwig/Bitwig Studio/installed-packages/5.0/Bitwig/Essentials/Curves/ADSR 1.bwcurve"
    source = parse(path.read_bytes()).body
    p = Patch("Poly Grid", "import")
    p.add("Envelope/Segments", 0, 0, CURVE=Curve.from_file(path))
    data = m.get(curve_param(p), 0x361D)
    from bwpreset.codec import Writer, write_object

    a, b = Writer(), Writer()
    write_object(a, source)
    write_object(b, data)
    assert a.buf == b.buf


def test_null_embedded_template_becomes_an_object():
    p = Patch("Poly Grid", "curve")
    seg = p.add("Envelope/Segments", 0, 0)
    template = seg.available["CURVE"]
    template.fields = [(f, 0x0A if f == 0x361D else t, None if f == 0x361D else v) for f, t, v in template.fields]
    seg.set(CURVE=Curve([(0, 0), (1, 1)]))
    assert len(m.get(m.get(curve_param(p), 0x361D), 0x3603)) == 2


def test_unknown_point_fields_are_not_silently_dropped():
    data = Curve([(0, 0), (1, 1)]).to_object()
    m.get(data, 0x3603)[0].fields.append((99, 7, 1.0))
    with pytest.raises(ValueError, match="point fields"):
        Curve.from_object(data)


def test_object_settings_are_reported_by_decompiler():
    p = Patch("Poly Grid", "curve")
    seg = p.add("Envelope/Segments", 0, 0, CURVE=Curve([(0, 0), (1, 1)]))
    m.get(seg.used["CURVE"], 0x361D).fields.append((99, 9, Obj(100, [])))
    code, unsupported = decompile(parse(serialize(p.to_preset())))
    assert "Envelope/Segments.CURVE" in unsupported
    exec(code, {})
