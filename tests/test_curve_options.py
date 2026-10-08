import copy

import pytest

from bwpreset import model as m
from bwpreset.codec import Obj, Writer, write_object
from bwpreset.curves import Curve


def encoded(obj):
    writer = Writer()
    write_object(writer, obj)
    return writer.buf


def test_named_markers_and_playback_have_separate_storage():
    curve = Curve(
        [(0, 0), (0.5, 1), (0.5, 0.6), (2, 0)],
        kind="envelope",
        sustain=2,
        loop_start=1,
        loop_end=2,
        playback="ping_pong",
        bipolar=True,
        reflect=True,
        anti_alias=True,
    )
    data = curve.to_object()
    assert m.get(data, 0x362D) == 1
    assert m.get(data, 0x3788) == 2
    assert m.get(data, 0x3665) == 1
    assert m.get(data, 0x3666) == 2
    param = curve.to_parameter(Obj(0x128C, [(m.F_NAME, 8, "CURVE")]))
    assert m.get(param, 0x3842) == 3
    assert m.get(param, 0x36AF) is True
    assert m.get(param, 0x3986) is True
    assert m.get(param, 0x3843) is True
    restored = Curve.from_parameter(param)
    assert restored.options["sustain"] == 2
    assert restored.options["release_start"] == 2
    assert restored.options["playback"] == "ping_pong"
    assert encoded(restored.to_parameter()) == encoded(param)


def test_release_start_is_the_same_marker_as_sustain():
    assert m.get(Curve([(0, 1), (1, 0)], release_start=0).to_object(), 0x3788) == 0
    with pytest.raises(ValueError, match="same"):
        Curve([(0, 1), (1, 0)], sustain=0, release_start=1)
    with pytest.raises(ValueError, match="indices"):
        Curve([(0, 1), (1, 0)], sustain=0, release_start=False)


@pytest.mark.parametrize(
    "options",
    [
        {"sustain": True},
        {"sustain": 2},
        {"loop_start": -2},
        {"loop_start": 1, "loop_end": 0},
        {"kind": "unknown"},
        {"playback": 2},
        {"bipolar": 1},
    ],
)
def test_invalid_options_are_rejected(options):
    with pytest.raises(ValueError):
        Curve([(0, 0), (1, 1)], **options)


def test_wrapper_flags_and_unknown_scalars_survive_repr_and_template_merge():
    source = Obj(
        0x128C,
        [
            (m.F_NAME, 8, "CURVE"),
            (0x361D, 9, Curve([(0, 0), (1, 1)]).to_object()),
            (0x3842, 1, 2),
            (0x36AF, 5, True),
            (0x7777, 8, "future"),
            (0x361E, 10, None),
        ],
    )
    original = copy.deepcopy(source)
    curve = eval(repr(Curve.from_parameter(source)), {"Curve": Curve})
    assert encoded(curve.to_parameter()) == encoded(source)
    template = Obj(0x128C, [(m.F_NAME, 8, "CURVE"), (0x3842, 1, 0), (0x8888, 5, True)])
    merged = curve.to_parameter(template)
    assert m.get(merged, 0x3842) == 2
    assert m.get(merged, 0x7777) == "future"
    assert m.get(merged, 0x8888) is True
    assert encoded(source) == encoded(original)


def test_clearing_markers_uses_minus_one_and_wide_indices_use_smallest_width():
    curve = Curve([(i, 0) for i in range(150)], sustain=140, loop_start=None, loop_end=None)
    assert (0x3788, 2, 140) in curve.to_object().fields
    assert m.get(curve.to_object(), 0x3665) == -1


def test_wrapper_without_embedded_data_is_explicitly_unsupported():
    with pytest.raises(ValueError, match="embedded"):
        Curve.from_parameter(Obj(0x128C, [(0x361D, 10, None)]))


def test_options_without_template_keep_parameter_identity():
    parameter = Curve([(0, 0), (1, 1)], playback="loop").to_parameter()
    assert m.get(parameter, m.F_NAME) == "CURVE"
    template = Obj(0x128C, [(m.F_NAME, 8, "OTHER")])
    assert m.get(Curve.from_parameter(parameter).to_parameter(template), m.F_NAME) == "OTHER"


def test_shortening_points_cannot_leave_out_of_range_markers():
    curve = Curve([(0, 0), (0.5, 1), (1, 0)], sustain=2)
    with pytest.raises(ValueError, match="indices"):
        Curve([(0, 0), (1, 1)], settings=curve.settings)
