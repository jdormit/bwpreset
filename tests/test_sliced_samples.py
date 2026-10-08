import pytest

from bwpreset import model as m
from bwpreset.assets import AudioFile, FileReference, Slice, SlicedSample, apply_asset, decode_asset
from bwpreset.codec import Obj


def audio():
    return AudioFile(FileReference(source_path="tone.wav"), sample_rate=48000, frames=48000, channels=1)


def test_sliced_sample_authoring_and_named_round_trip():
    value = SlicedSample(audio(), [Slice(0), Slice(0.5, parameter_1=0.25)], duration_mode="loop_ping_pong", assignment="select_knob")
    parameter = apply_asset(Obj(0x212, [(m.F_NAME, 8, "SAMPLE")]), value)
    decoded = decode_asset(parameter)
    assert isinstance(decoded, SlicedSample)
    assert [s.start for s in decoded.slices] == [0, 0.5]
    assert decoded.slices[1].parameter_1 == 0.25
    assert decoded.duration_mode == "loop_ping_pong"
    assert decoded.assignment == "select_knob"
    assert m.get(decoded.to_object(), 0x3EAC) == 4


def test_slice_edits_preserve_unknown_fields():
    original = SlicedSample(audio(), [Slice(0)]).parameter_object()
    slice_obj = m.get(m.get(original, 0x74C), 0x3EA7)[0]
    slice_obj.fields.append((99999, 8, "future setting"))
    decoded = decode_asset(original)
    decoded.slices[0].parameter_2 = -0.5
    rebuilt = apply_asset(original, decoded)
    point = m.get(m.get(rebuilt, 0x74C), 0x3EA7)[0]
    assert m.get(point, 99999) == "future setting"
    assert m.get(point, 0x3EBA) == -0.5


def test_slices_validate_time_and_order():
    with pytest.raises(ValueError):
        SlicedSample(audio(), [Slice(0.7), Slice(0.2)])
    with pytest.raises(ValueError):
        SlicedSample(audio(), [Slice(1.1)])
    with pytest.raises(ValueError):
        Slice(float("nan"))
