"""Embedded BWCURVE data. Public points are (position, value, outgoing bend)."""

import copy
import math
from pathlib import Path

from . import model as m
from .codec import Obj, parse

CURVE_PARAM = 0x128C
CURVE_DATA = 0x127F
CURVE_POINT = 0x127E
F_EMBEDDED = 0x361D
F_POINTS = 0x3603
F_VALUE = 0x35FD
F_POSITION = 0x35FE
F_BEND = 0x35FF
F_NAME = 0x360B
F_EXTERNAL = 0x361E
F_KIND = 0x362D
F_HOLD = 0x3788
F_LOOP_START = 0x3665
F_LOOP_END = 0x3666
KINDS = {"cycle": 0, "envelope": 1, "transfer": 2}
PLAYBACK = {"straight": 0, "one_shot": 0, "hold": 1, "loop": 2, "ping_pong": 3}
WRAPPER_FLAGS = {"bipolar": 0x36AF, "reflect": 0x3986, "anti_alias": 0x3843}
F_PLAYBACK = 0x3842
_UNSET = object()


def _put(fields, fid, t, value):
    for i, (f, _, _) in enumerate(fields):
        if f == fid:
            fields[i] = (fid, t, value)
            return
    fields.append((fid, t, value))


def _enum(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {list(choices)}")
    return choices[value]


DEFAULT_SETTINGS = [
    (0x362D, 1, 0),
    (0x3646, 5, True),
    (0x3601, 1, 16),
    (0x3602, 1, 1),
    (0x3635, 1, 0),
    (F_POINTS, 0x12, None),
    (0x3788, 1, -1),
    (0x3665, 1, -1),
    (0x3666, 1, -1),
    (F_NAME, 8, "Custom"),
    (0x3621, 8, "bwpreset"),
    (0x3623, 8, ""),
    (0x3622, 8, "Envelope"),
    (0x36A6, 8, ""),
]


class Curve:
    """A curve with nonnegative positions, values 0..1 and outgoing bends -1..1.

    Bend shapes the segment from this point to the next. Two-element points
    default to a straight segment. Repeated positions make
    vertical steps. Imported curves retain the original editor and loop metadata.
    settings is the raw field list used by the decompiler for lossless rebuilding.
    """

    def __init__(
        self,
        points,
        *,
        name: str | None = None,
        settings=None,
        wrapper_settings=None,
        kind=_UNSET,
        sustain=_UNSET,
        release_start=_UNSET,
        loop_start=_UNSET,
        loop_end=_UNSET,
        playback=_UNSET,
        bipolar=_UNSET,
        reflect=_UNSET,
        anti_alias=_UNSET,
    ):
        normalized = []
        for point in points:
            if len(point) not in (2, 3):
                raise ValueError("curve points must be (position, value[, bend])")
            numbers = tuple(point) + ((0.0,) if len(point) == 2 else ())
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
                raise ValueError("curve coordinates must be finite numbers")
            x, y, bend = map(float, numbers)
            if x < 0 or not 0 <= y <= 1 or not -1 <= bend <= 1:
                raise ValueError("positions must be nonnegative; values 0..1; bends -1..1")
            if normalized and x < normalized[-1][0]:
                raise ValueError("curve points must be ordered by position")
            normalized.append((x, y, bend))
        if len(normalized) < 2:
            raise ValueError("a curve needs at least two points")
        self.points = tuple(normalized)
        self.settings = copy.deepcopy(DEFAULT_SETTINGS if settings is None else settings)
        if sum(f == F_POINTS for f, _, _ in self.settings) != 1:
            raise ValueError("curve settings require exactly one point-list placeholder")
        for fid, t, value in self.settings:
            if fid == F_POINTS:
                if t != 0x12 or value is not None:
                    raise ValueError("invalid curve point-list placeholder")
            elif t not in (1, 2, 3, 4, 5, 6, 7, 8) or not isinstance(value, (int, float, bool, str)):
                raise ValueError(f"unsupported curve setting {fid:#x}; expected a scalar")
        if name is not None:
            if not isinstance(name, str):
                raise ValueError("curve name must be a string")
            self.settings = [(f, t, name if f == F_NAME else v) for f, t, v in self.settings]
        self.wrapper_settings = copy.deepcopy([] if wrapper_settings is None else wrapper_settings)
        for fid, t, value in self.wrapper_settings:
            if fid == F_EMBEDDED:
                if t != 9 or value is not None:
                    raise ValueError("invalid embedded curve placeholder")
            elif not ((t == 0x0A and value is None) or (t in (1, 2, 3, 4, 5, 6, 7, 8) and isinstance(value, (int, float, bool, str)))):
                raise ValueError(f"unsupported curve wrapper setting {fid:#x}; expected a scalar or null")
        if kind is not _UNSET:
            _put(self.settings, F_KIND, 1, _enum(kind, KINDS, "kind"))
        for value in (sustain, release_start):
            if value is not _UNSET and value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError("curve markers must be point indices, or None to clear")
        if sustain is not _UNSET and release_start is not _UNSET and sustain != release_start:
            raise ValueError("sustain and release_start are the same hold marker and must agree")
        hold = sustain if sustain is not _UNSET else release_start
        markers = ((F_HOLD, hold), (F_LOOP_START, loop_start), (F_LOOP_END, loop_end))
        for fid, value in markers:
            if value is _UNSET:
                continue
            value = -1 if value is None else value
            if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value < len(self.points):
                raise ValueError("curve markers must be point indices, or None to clear")
            _put(self.settings, fid, 1 if value <= 127 else 2 if value <= 32767 else 3, value)
        stored = dict((f, v) for f, _, v in self.settings)
        for fid in (F_HOLD, F_LOOP_START, F_LOOP_END):
            value = stored.get(fid, -1)
            if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value < len(self.points):
                raise ValueError("curve markers must be point indices, or None to clear")
        start, end = stored.get(F_LOOP_START, -1), stored.get(F_LOOP_END, -1)
        if start >= 0 and end >= 0 and start > end:
            raise ValueError("loop_start must not follow loop_end")
        if playback is not _UNSET:
            _put(self.wrapper_settings, F_PLAYBACK, 1, _enum(playback, PLAYBACK, "playback"))
        for key, value in (("bipolar", bipolar), ("reflect", reflect), ("anti_alias", anti_alias)):
            if value is not _UNSET:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a bool")
                _put(self.wrapper_settings, WRAPPER_FLAGS[key], 5, value)

    @property
    def options(self):
        data, wrapper = m.fields(self.to_object()), dict((f, v) for f, _, v in self.wrapper_settings)
        result = {}
        for name, fid in (("sustain", F_HOLD), ("release_start", F_HOLD), ("loop_start", F_LOOP_START), ("loop_end", F_LOOP_END)):
            if fid in data:
                result[name] = None if data[fid] == -1 else data[fid]
        for name, values, fid, stored in (("kind", KINDS, F_KIND, data), ("playback", PLAYBACK, F_PLAYBACK, wrapper)):
            if fid in stored:
                reverse = {v: k for k, v in values.items() if k != "one_shot"}
                if stored[fid] not in reverse:
                    raise ValueError(f"unknown curve {name} {stored[fid]!r}")
                result[name] = reverse[stored[fid]]
        result.update((name, wrapper[fid]) for name, fid in WRAPPER_FLAGS.items() if fid in wrapper)
        return result

    def to_parameter(self, template: Obj | None = None) -> Obj:
        """Embed into a copied parameter, preserving imported wrapper state."""
        if template is not None and template.cls != CURVE_PARAM:
            raise ValueError("expected a curve parameter")
        if template is None:
            fields = copy.deepcopy(self.wrapper_settings or [(m.F_NAME, 8, "CURVE")])
            if not any(f == m.F_NAME for f, _, _ in fields):
                fields.insert(0, (m.F_NAME, 8, "CURVE"))
        else:
            fields = copy.deepcopy(template.fields)
            for f, t, value in self.wrapper_settings:
                if f not in (F_EMBEDDED, m.F_NAME):
                    _put(fields, f, t, copy.deepcopy(value))
        _put(fields, F_EMBEDDED, 9, self.to_object())
        if any(f == F_EXTERNAL for f, _, _ in fields):
            _put(fields, F_EXTERNAL, 0x0A, None)
        return Obj(CURVE_PARAM, fields)

    @classmethod
    def from_parameter(cls, param: Obj):
        """Read embedded geometry and all scalar/null wrapper fields."""
        if param.cls != CURVE_PARAM:
            raise ValueError("expected a curve parameter")
        data = m.get(param, F_EMBEDDED)
        if not isinstance(data, Obj):
            raise ValueError("curve parameter has no embedded curve; resolve its resource first")
        curve = cls.from_object(data)
        return cls(
            curve.points,
            settings=curve.settings,
            wrapper_settings=[(f, 9 if f == F_EMBEDDED else t, None if f == F_EMBEDDED else v) for f, t, v in param.fields],
        )

    def to_object(self) -> Obj:
        points = [Obj(CURVE_POINT, [(F_VALUE, 7, y), (F_POSITION, 7, x), (F_BEND, 7, bend)]) for x, y, bend in self.points]
        return Obj(CURVE_DATA, [(f, t, points if f == F_POINTS else copy.deepcopy(v)) for f, t, v in self.settings])

    @classmethod
    def from_object(cls, data: Obj):
        if data.cls != CURVE_DATA:
            raise ValueError("expected BWCURVE data")
        raw = m.get(data, F_POINTS)
        if len({id(p) for p in raw}) != len(raw):
            raise ValueError("shared curve point objects are not supported")
        expected = {(F_POSITION, 7), (F_VALUE, 7), (F_BEND, 7)}
        for p in raw:
            if p.cls != CURVE_POINT or len(p.fields) != 3 or {(f, t) for f, t, _ in p.fields} != expected:
                raise ValueError("unsupported curve point fields")
        points = [(m.get(p, F_POSITION), m.get(p, F_VALUE), m.get(p, F_BEND)) for p in raw]
        curve = cls(points, settings=[(f, t, None if f == F_POINTS else v) for f, t, v in data.fields])
        return curve

    @classmethod
    def from_file(cls, path: str | Path):
        return cls.from_object(parse(Path(path).read_bytes()).body)

    def __repr__(self):
        wrapper = f", wrapper_settings={self.wrapper_settings!r}" if self.wrapper_settings else ""
        return f"Curve({list(self.points)!r}, settings={self.settings!r}{wrapper})"
