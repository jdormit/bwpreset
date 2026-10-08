"""Physical values translated through Bitwig parameter descriptors."""

import math
from dataclasses import dataclass

from . import model as m


@dataclass(frozen=True)
class Seconds:
    value: float


@dataclass(frozen=True)
class Hz:
    value: float


@dataclass(frozen=True)
class Decibels:
    value: float


@dataclass(frozen=True)
class Rate:
    """Cycles per selected timebase, before descriptor scaling."""

    value: float


@dataclass(frozen=True)
class Beats:
    """Cycle duration in quarter-note beats."""

    value: float
    beats_per_bar: float | None = None


UNIT_VALUES = (Seconds, Hz, Decibels, Rate, Beats)
TIMEBASE_BEATS = {
    3: 2,
    4: 1,
    5: 0.5,
    6: 0.25,
    7: 0.125,
    10: 3,
    11: 1.5,
    12: 0.75,
    13: 0.375,
    14: 0.1875,
    16: 4 / 3,
    17: 2 / 3,
    18: 1 / 3,
    19: 1 / 6,
    20: 1 / 12,
    9: 6,
    15: 8 / 3,
}
BAR_MULTIPLIERS = {2: 1}


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def enum_options(descriptor):
    if descriptor is None or descriptor.cls != m.ENUM_DESCRIPTOR:
        return {}
    return {m.get(o, m.F_OPTION_INDEX): m.get(o, m.F_OPTION_LABEL) for o in m.get(descriptor, m.F_ENUM_OPTIONS, [])}


def enum_value(value, descriptor):
    options = enum_options(descriptor)
    if isinstance(value, str):
        matches = [i for i, label in options.items() if label.casefold() == value.casefold()]
        if len(matches) != 1:
            raise ValueError(f"enum must be one of {options}")
        return matches[0]
    if options and value not in options:
        raise ValueError(f"enum must be one of {options}")
    return value


def engine_to_stored(value, scaling):
    if scaling == 0:
        return value
    if scaling == 5:
        return math.cbrt(value)
    if value <= 0:
        raise ValueError("this scaling requires a positive value")
    if scaling == 1:
        return 20 * math.log10(value)
    if scaling == 2:
        return 69 + 12 * math.log2(value / 440)
    if scaling == 3:
        return math.log10(value)
    if scaling == 4:
        return 1 / value
    if scaling == 6:
        return 12 * math.log2(value)
    if scaling == 7:
        return math.log(value)
    raise ValueError(f"physical conversion for scaling {scaling} is not mapped")


def to_stored(value, descriptor, *, timebase=None, legacy_rate=False):
    if not isinstance(value, UNIT_VALUES):
        return value
    v = value.value
    if not finite(v):
        raise ValueError("physical values must be finite numbers")
    unit, scaling = m.get(descriptor, 0x128), m.get(descriptor, 0x126, 0)
    if timebase is not None:
        if timebase == 22:
            raise ValueError("Hold has no physical rate; set the timebase without a rate")
        if isinstance(value, Rate):
            if v <= 0:
                raise ValueError("rate must be positive")
            engine = 1 / v if timebase in TIMEBASE_BEATS | BAR_MULTIPLIERS and not legacy_rate else v
        elif isinstance(value, Beats) and timebase in TIMEBASE_BEATS | BAR_MULTIPLIERS:
            if v <= 0:
                raise ValueError("beat duration must be positive")
            if timebase in BAR_MULTIPLIERS:
                if not finite(value.beats_per_bar) or value.beats_per_bar <= 0:
                    raise ValueError("bar timebases require Beats(..., beats_per_bar=...)")
                base = value.beats_per_bar * BAR_MULTIPLIERS[timebase]
            else:
                base = TIMEBASE_BEATS[timebase]
            engine = base / v if legacy_rate else v / base
        elif isinstance(value, (Hz, Seconds)) and timebase in (0, 1):
            if v <= 0:
                raise ValueError("rate or period must be positive")
            engine = (v if isinstance(value, Hz) else 1 / v) / (1000 if timebase == 1 else 1)
        else:
            raise ValueError("physical rate requires Hertz/Kilohertz; use Beats or Rate for a beat timebase")
    elif isinstance(value, Seconds) and unit in (3, 18):
        if v < 0:
            raise ValueError("seconds must be nonnegative")
        engine = v
    elif isinstance(value, Hz) and unit == 4:
        if v <= 0:
            raise ValueError("frequency must be positive")
        engine = v
    elif isinstance(value, Decibels) and unit == 2:
        engine = 10 ** (v / 20)
    else:
        raise ValueError(f"{type(value).__name__} is incompatible with descriptor unit {unit}")
    result = engine_to_stored(engine, scaling)
    if not math.isfinite(result):
        raise ValueError("conversion produced a non-finite stored value")
    return result


def validate_range(value, descriptor):
    if descriptor is None or descriptor.cls not in (m.DESCRIPTOR, 0x8F):
        return
    lo, hi = (
        (m.get(descriptor, 0x159), m.get(descriptor, 0x15A))
        if descriptor.cls == 0x8F
        else (m.get(descriptor, 0x124), m.get(descriptor, 0x125))
    )
    below = lo is not None and value < lo and not math.isclose(value, lo, rel_tol=1e-12, abs_tol=1e-12)
    above = hi is not None and value > hi and not math.isclose(value, hi, rel_tol=1e-12, abs_tol=1e-12)
    if below or above:
        raise ValueError(f"stored value {value} is outside [{lo}, {hi}]")
