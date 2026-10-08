import math

import pytest

from bwpreset import model as m
from bwpreset.codec import Obj
from bwpreset.units import Beats, Hz, Rate, to_stored


def descriptor():
    return Obj(m.DESCRIPTOR, [(0x126, 1, 3), (0x128, 1, 0)])


@pytest.mark.parametrize("legacy", [False, True])
def test_hertz_is_unchanged_by_legacy_rate(legacy):
    assert to_stored(Hz(2), descriptor(), timebase=0, legacy_rate=legacy) == pytest.approx(math.log10(2))


def test_beat_rate_direction_changes_with_legacy_flag():
    assert to_stored(Beats(2), descriptor(), timebase=4) == pytest.approx(math.log10(2))
    assert to_stored(Beats(2), descriptor(), timebase=4, legacy_rate=True) == pytest.approx(math.log10(0.5))
    assert to_stored(Rate(2), descriptor(), timebase=4) == pytest.approx(math.log10(0.5))


def test_whole_note_variants_do_not_depend_on_meter():
    assert to_stored(Beats(6), descriptor(), timebase=9) == 0
    assert to_stored(Beats(8 / 3), descriptor(), timebase=15) == 0
