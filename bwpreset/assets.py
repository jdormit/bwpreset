"""Sampler and wavetable resources; mappings and evidence are in docs/assets.md."""

import copy
import math
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from . import model as m
from .codec import Obj, Reader, Writer, read_object, resolve_refs, walk, write_object

SAMPLE_PARAM = 0x212
SAMPLE_PLAYER_PARAM = 0x1622
WAVETABLE_PARAMS = {0xF38, 0x12AD, 0xF0E}
ASSET_PARAMETER_CLASSES = {SAMPLE_PARAM, SAMPLE_PLAYER_PARAM, *WAVETABLE_PARAMS}


def _put(obj, fid, value, typ=None):
    if typ is None:
        if value is None:
            typ = 10
        elif isinstance(value, Obj):
            typ = 9
        elif isinstance(value, bool):
            typ = 5
        elif isinstance(value, int):
            if not -(2**63) <= value < 2**63:
                raise ValueError("integer field does not fit int64")
            typ = 1 if -128 <= value < 128 else 2 if -32768 <= value < 32768 else 3 if -(2**31) <= value < 2**31 else 4
        elif isinstance(value, float):
            typ = 7
        elif isinstance(value, str):
            typ = 8
        else:
            raise TypeError(type(value))
    for i, (f, t, old) in enumerate(obj.fields):
        if f == fid:
            if t == typ and old == value:
                return
            obj.fields[i] = (fid, typ, value)
            return
    obj.fields.append((fid, typ, value))


def _portable(path):
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError("portable_path must be a nonempty POSIX relative path")
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or ":" in path or path.endswith("/"):
        raise ValueError("portable_path must stay inside the asset directory")
    return str(p)


def _valid_enum(value, options):
    return (isinstance(value, str) and value in options) or (type(value) is int and value in options.values())


def _wav_data(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("expected a RIFF WAV file")
    end = struct.unpack_from("<I", data, 4)[0] + 8
    if end > len(data):
        raise ValueError("truncated WAV data")
    pos, fmt, payloads = 12, None, []
    while pos < end:
        if pos + 8 > end:
            raise ValueError("truncated WAV chunk")
        tag, size = struct.unpack_from("<4sI", data, pos)
        pos += 8
        if pos + size > end:
            raise ValueError("truncated WAV chunk")
        chunk = data[pos : pos + size]
        if tag == b"fmt ":
            fmt = chunk
        elif tag == b"data":
            payloads.append(chunk)
        pos += size + (size & 1)
    if fmt is None or len(fmt) < 16 or not payloads:
        raise ValueError("WAV requires format and data chunks")
    encoding, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if encoding == 0xFFFE:
        if len(fmt) < 40 or fmt[26:40] != bytes.fromhex("000000001000800000aa00389b71"):
            raise ValueError("unsupported WAV extensible format")
        encoding = struct.unpack_from("<H", fmt, 24)[0]
    if encoding not in (1, 3) or (encoding == 1 and bits not in (8, 16, 24, 32)) or (encoding == 3 and bits not in (32, 64)):
        raise ValueError("WAV import supports PCM and IEEE float data")
    width = bits // 8
    payload = b"".join(payloads)
    if not channels or not rate or align != channels * width or len(payload) < align:
        raise ValueError("invalid WAV dimensions")
    payload = payload[: len(payload) // align * align]
    return rate, channels, width, len(payload) // align, encoding, payload


@dataclass
class Dependency:
    source_path: str | None
    portable_path: str | None
    package_path: str | None = None
    data: bytes | None = None

    def read_bytes(self, *, base_directory=None):
        if self.data is not None:
            return self.data
        if base_directory is not None and self.portable_path is not None:
            return (Path(base_directory) / _portable(self.portable_path)).read_bytes()
        if self.source_path is None:
            raise ValueError("dependency has no local source; resolve its package path or supply a portable base_directory")
        return Path(self.source_path).read_bytes()


class FileReference:
    def __init__(self, source_path=None, *, portable_path=None, package_path=None, sub_path="", name=None, size=None):
        self.source_path = str(Path(source_path).expanduser().absolute()) if source_path is not None else None
        self.portable_path = _portable(portable_path) if portable_path is not None else None
        self.package_path = package_path
        self.sub_path = _portable(sub_path) if sub_path else ""
        self.name = name or PurePosixPath(self.source_path or self.portable_path or self.package_path or "Asset").name
        self.size = size
        self._raw = None
        self._initial = self._state()

    def _state(self):
        return self.source_path, self.portable_path, self.package_path, self.sub_path, self.name, self.size

    def to_object(self):
        if self._raw is not None:
            result = copy.deepcopy(self._raw)
            self._edit_object(result)
            return result
        fields = [(0x512, 8, self.name), (0xD3A, 8, self.portable_path or ""), (0xCD4, 8, self.package_path or "")]
        if self.source_path is not None:
            fields.append((0xD3B, 9, Obj(0x2A, [(0x3B, 8, self.source_path)])))
        else:
            fields.append((0xD3B, 10, None))
        fields.append((0x15, 9, Obj(0x2B4, [(0xC5B, 0x12, [])])))
        if self.size is not None:
            fields.append((0x17, 3 if self.size < 2**31 else 4, self.size))
        return Obj(0x4A6, fields)

    def _edit_object(self, obj):
        if self._state() == self._initial:
            return
        if self.portable_path is not None and self.portable_path != self._initial[1]:
            _portable(self.portable_path)
        for index, fid, value in (
            (1, 0xD3A, self.portable_path or ""),
            (2, 0xCD4, self.package_path or ""),
            (4, 0x512, self.name),
            (5, 0x17, self.size),
        ):
            if self._state()[index] != self._initial[index]:
                _put(obj, fid, value)
        if self.source_path != self._initial[0]:
            location = m.get(obj, 0xD3B)
            if self.source_path is None:
                _put(obj, 0xD3B, None)
            else:
                if not isinstance(location, Obj):
                    location = Obj(0x2A)
                    _put(obj, 0xD3B, location)
                _put(location, 0x3B, self.source_path)
            obj.fields = [(f, t, v) for f, t, v in obj.fields if f != 0x513]

    @classmethod
    def from_object(cls, item):
        if not isinstance(item, Obj):
            raise ValueError("asset has no file reference")
        raw = m.get(item, 0x129E)
        if not isinstance(raw, Obj) or raw.cls != 0x4A6:
            raise ValueError("asset has no file reference")
        location = m.get(raw, 0xD3B)
        result = cls.__new__(cls)
        result.source_path = m.get(location, 0x3B) if isinstance(location, Obj) else None
        result.portable_path = m.get(raw, 0xD3A) or None
        result.package_path = m.get(raw, 0xCD4) or None
        result.sub_path, result.name, result.size = m.get(item, 0xFB8, ""), m.get(raw, 0x512), m.get(raw, 0x17)
        result._raw = raw
        result._initial = result._state()
        return result

    def dependency(self):
        return Dependency(self.source_path, self.portable_path, self.package_path)


def _file_item(cls, reference, title, fields):
    ref = reference.to_object()
    item = Obj(cls, [(0x129F, 8, title), (0x129E, 9, ref), (0xFB8, 8, reference.sub_path), *fields])
    _put(ref, 0x129C, [item], 0x12)
    return item


def _detach_item(item):
    if not isinstance(item, Obj):
        return
    reference = m.get(item, 0x129E)
    if isinstance(reference, Obj) and isinstance(m.get(reference, 0x129C), list):
        _put(reference, 0x129C, [v for v in m.get(reference, 0x129C) if v is not item], 0x12)


class AudioFile:
    def __init__(self, reference, *, sample_rate, frames, channels):
        if not isinstance(reference, FileReference):
            raise TypeError("reference must be a FileReference")
        if any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in (sample_rate, frames, channels)):
            raise ValueError("audio sample rate, frames and channels must be positive integers")
        self.reference, self.sample_rate, self.frames, self.channels = reference, sample_rate, frames, channels
        self._raw = None
        self._initial = reference, sample_rate, frames, channels

    @property
    def duration(self):
        return self.frames / self.sample_rate

    def _has_changes(self):
        return (
            self.reference,
            self.sample_rate,
            self.frames,
            self.channels,
        ) != self._initial or self.reference._state() != self.reference._initial

    @classmethod
    def from_file(cls, path, *, portable_path=None):
        path = Path(path)
        data = path.read_bytes()
        ref = FileReference(path, portable_path=portable_path, size=len(data))
        return cls.from_wav_bytes(data, ref)

    @classmethod
    def from_wav_bytes(cls, data, reference):
        rate, channels, _, frames, _, _ = _wav_data(data)
        return cls(reference, sample_rate=rate, frames=frames, channels=channels)

    def to_object(self):
        if any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in (self.sample_rate, self.frames, self.channels)):
            raise ValueError("audio sample rate, frames and channels must be positive integers")
        if self._raw is not None:
            result = copy.deepcopy(self._raw)
            if self.reference is self._initial[0]:
                self.reference._edit_object(m.get(result, 0x129E))
            else:
                _put(result, 0x129E, self.reference.to_object())
                _put(m.get(result, 0x129E), 0x129C, [result], 0x12)
            if self.reference.sub_path != m.get(self._raw, 0xFB8, ""):
                _put(result, 0xFB8, self.reference.sub_path)
            if (
                self.reference is not self._initial[0]
                or self.reference.name != self.reference._initial[4]
                or self.reference.sub_path != self.reference._initial[3]
            ):
                _put(result, 0x129F, self.reference.sub_path or self.reference.name)
            for fid, value, initial in ((0x94, self.sample_rate, self._initial[1]), (0x96, self.channels, self._initial[3])):
                if value != initial:
                    _put(result, fid, value)
            if (self.sample_rate, self.frames) != self._initial[1:3]:
                _put(result, 0x95, self.duration)
            if self._has_changes():
                _put(m.get(result, 0x129E), 0x129C, [result], 0x12)
            return result
        return _file_item(
            0x4A9,
            self.reference,
            self.reference.sub_path or self.reference.name,
            [
                (0x94, 3, self.sample_rate),
                (0x95, 7, self.duration),
                (0x96, 3, self.channels),
            ],
        )

    @classmethod
    def from_object(cls, item):
        if not isinstance(item, Obj):
            raise ValueError("asset has no file reference")
        rate, duration = m.get(item, 0x94), m.get(item, 0x95)
        result = cls(FileReference.from_object(item), sample_rate=rate, frames=round(duration * rate), channels=m.get(item, 0x96))
        result._raw = item
        return result


class Asset:
    _parameter = None

    def to_bytes(self):
        w = Writer()
        write_object(w, self.parameter_object())
        return bytes(w.buf)

    @staticmethod
    def from_bytes(data):
        r = Reader(data)
        obj = read_object(r)
        if r.pos != len(data):
            raise ValueError("trailing asset data")
        resolve_refs(r.objects)
        result = decode_asset(obj)
        if result is not None:
            result._restore_parameter = True
        return result

    def __repr__(self):
        return f"Asset.from_bytes(bytes.fromhex({self.to_bytes().hex()!r}))"


class PreservedAsset(Asset):
    """An imported empty or specialized resource outside the named authoring API."""

    def __init__(self, parameter):
        self._parameter = parameter

    def parameter_object(self):
        return copy.deepcopy(self._parameter)

    def to_object(self):
        return copy.deepcopy(m.get(self._parameter, 0x74C if self._parameter.cls == SAMPLE_PARAM else 0x2C34))


SAMPLE_FIELDS = {
    "keytrack": (0x75B, 7, 1.0),
    "root_key": (0x75C, 3, 60),
    "fine_tune": (0x75D, 7, 0.0),
    "gain_db": (0x9E6, 7, 0.0),
    "sample_start": (0x75E, 7, 0.0),
    "sample_end": (0x75F, 7, None),
    "loop_start": (0x760, 7, 0.0),
    "loop_end": (0x761, 7, None),
    "loop_crossfade_length": (0x762, 7, 0.0),
    "loop_mode": (0x763, 3, "off"),
    "reverse": (0x764, 5, False),
    "manual_bpm": (0x3EC5, 7, 60.0),
    "use_analyzed_bpm": (0x40CB, 5, False),
    "use_analyzed_root_f0": (0x40CC, 5, False),
}
LOOP_MODES = {"off": 0, "loop": 1, "ping_pong": 2}


class _Options:
    def __getattr__(self, name):
        options = self.__dict__.get("options", {})
        if name in options:
            return options[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in getattr(type(self), "FIELDS", {}) and "options" in self.__dict__:
            self.options[name] = value
        else:
            object.__setattr__(self, name, value)


class Sample(Asset, _Options):
    FIELDS = SAMPLE_FIELDS

    def __init__(self, source, *, keep_in_memory=None, **options):
        if not isinstance(source, AudioFile):
            raise TypeError("Sample source must be an AudioFile")
        self.source, self.options, self._raw = source, {}, None
        self.keep_in_memory = keep_in_memory
        for name, (_, _, default) in self.FIELDS.items():
            self.options[name] = source.duration if default is None else default
        self.options.update(options)
        self._initial_options = {}
        self._initial_source = source
        self._validate()

    def _validate(self):
        if self.options.keys() - self.FIELDS.keys():
            raise ValueError(f"unknown sample options: {self.options.keys() - self.FIELDS.keys()}")
        for name, value in self.options.items():
            if self._raw is not None and self._initial_options.get(name) == value:
                continue
            if name == "loop_mode":
                if not _valid_enum(value, LOOP_MODES):
                    raise ValueError("loop_mode must be off, loop or ping_pong")
            elif self.FIELDS[name][1] == 5:
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be boolean")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if (self._raw is None or self.root_key != self._initial_options.get("root_key")) and (
            not isinstance(self.root_key, int) or not 0 <= self.root_key <= 127
        ):
            raise ValueError("root_key must be a MIDI key 0..127")
        if (self._raw is None or self.manual_bpm != self._initial_options.get("manual_bpm")) and self.manual_bpm <= 0:
            raise ValueError("manual_bpm must be positive")
        geometry = ("sample_start", "sample_end", "loop_start", "loop_end", "loop_crossfade_length")
        changed = (
            self._raw is None
            or self.source is not self._initial_source
            or any(self.options[k] != self._initial_options.get(k) for k in geometry)
        )
        changed = changed or (self.source.sample_rate, self.source.frames, self.source.channels) != self.source._initial[1:]
        if not changed:
            return
        if not 0 <= self.sample_start < self.sample_end <= self.source.duration + 1e-6:
            raise ValueError("sample boundaries must fit inside the audio")
        if not self.sample_start <= self.loop_start <= self.loop_end <= self.sample_end:
            raise ValueError("loop boundaries must fit inside the sample")
        if not 0 <= self.loop_crossfade_length <= self.loop_end - self.loop_start:
            raise ValueError("loop crossfade must fit inside the loop")

    def to_object(self):
        obj = copy.deepcopy(self._raw) if self._raw is not None else Obj(0x210, [(0x2B9, 8, "SAMPLE")])
        self._edit_object(obj)
        return obj

    def _edit_object(self, obj):
        if (
            self._raw is None
            or self.options != self._initial_options
            or self.source is not self._initial_source
            or self.source._has_changes()
        ):
            self._validate()
        if self._raw is not None and (self.source is not self._initial_source or self.source._has_changes()):
            analyzer = m.get(obj, 0x4112)
            _detach_item(m.get(analyzer if isinstance(analyzer, Obj) else obj, 0x748))
        if self._raw is None or self.source is not self._initial_source:
            obj.fields = [(f, t, v) for f, t, v in obj.fields if f != 0x748]
            _put(obj, 0x4112, Obj(0x15EB, [(0x748, 9, self.source.to_object())]))
        elif self.source._has_changes():
            analyzable = m.get(obj, 0x4112)
            _put(analyzable if isinstance(analyzable, Obj) else obj, 0x748, self.source.to_object())
        for name, value in self.options.items():
            if self._raw is not None and value == self._initial_options.get(name):
                continue
            fid, typ, _ = self.FIELDS[name]
            if name == "loop_mode":
                value = LOOP_MODES.get(value, value)
            old = m.get(obj, fid)
            if old != value:
                _put(obj, fid, value, typ if typ != 3 else None)

    def parameter_object(self):
        return apply_asset(self._parameter or Obj(SAMPLE_PARAM, [(0x2B9, 8, "SAMPLE")]), self)

    @classmethod
    def _import_settings(cls, source, **options):
        obj = Obj(0x210, [(0x2B9, 8, "SAMPLE"), (0x4112, 9, Obj(0x15EB, [(0x748, 9, source.to_object())]))])
        for name, (fid, typ, default) in cls.FIELDS.items():
            value = options.get(name, source.duration if default is None else default)
            if name == "loop_mode":
                if not _valid_enum(value, LOOP_MODES):
                    raise ValueError("unknown multisample loop mode")
                value = LOOP_MODES.get(value, value)
            _put(obj, fid, value, None if typ == 3 else typ)
        return cls.from_object(obj)

    @classmethod
    def from_object(cls, obj):
        if obj.cls != 0x210:
            raise ValueError(f"unsupported single-sample resource {obj.cls:#x}")
        analyzable = m.get(obj, 0x4112)
        source = AudioFile.from_object(m.get(analyzable, 0x748) if isinstance(analyzable, Obj) else m.get(obj, 0x748))
        result = cls.__new__(cls)
        result.source, result.options, result._raw = source, {}, obj
        result.keep_in_memory = None
        for name, (fid, _, default) in cls.FIELDS.items():
            value = m.get(obj, fid, source.duration if default is None else default)
            result.options[name] = next((k for k, v in LOOP_MODES.items() if v == value), value) if name == "loop_mode" else value
        result._initial_options = dict(result.options)
        result._initial_source = source
        return result


class Slice:
    """A slice starting at a time in seconds, with three zone parameters."""

    def __init__(self, start, *, parameter_1=0.0, parameter_2=0.0, parameter_3=0.0, manual=True):
        self.start = start
        self.parameter_1, self.parameter_2, self.parameter_3 = parameter_1, parameter_2, parameter_3
        self.manual, self._raw = manual, None
        self.to_object()

    def to_object(self):
        obj = copy.deepcopy(self._raw) if self._raw is not None else Obj(0x151E, [(m.F_NAME, 8, "SLICE")])
        for name, fid in (("start", 0x3EB8), ("parameter_1", 0x3EB9), ("parameter_2", 0x3EBA), ("parameter_3", 0x3EBB)):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (name == "start" and value < 0)
            ):
                raise ValueError(f"slice {name} must be finite" + (" and nonnegative" if name == "start" else ""))
            _put(obj, fid, float(value), 7)
        if not isinstance(self.manual, bool):
            raise ValueError("slice manual must be boolean")
        _put(obj, 0x3EBC, self.manual, 5)
        return obj

    @classmethod
    def from_object(cls, obj):
        value = cls(
            m.get(obj, 0x3EB8),
            parameter_1=m.get(obj, 0x3EB9, 0.0),
            parameter_2=m.get(obj, 0x3EBA, 0.0),
            parameter_3=m.get(obj, 0x3EBB, 0.0),
            manual=m.get(obj, 0x3EBC, False),
        )
        value._raw = obj
        return value


SLICE_ENUMS = {
    "slicing_mode": {"onsets": 0, "pitch": 1, "beat_division": 2, "equal_division": 3, "manual": 4},
    "duration_mode": {"slice_end": 0, "sample_end": 1, "loop_forward": 2, "loop_ping_pong": 3},
    "assignment": {"key_chromatic": 0, "key_white": 1, "key_black": 2, "select_knob": 3, "velocity": 4},
}


class SlicedSample(Sample):
    """A sliced audio resource; manual markers are authored in seconds."""

    FIELDS = {
        "keytrack": (0x3EA8, 7, 1.0),
        "root_key": (0x3EA9, 3, 60),
        "fine_tune": (0x3EAA, 7, 0.0),
        "gain_db": (0x3EAB, 7, 0.0),
        "slicing_mode": (0x3EAC, 3, "manual"),
        "onset_threshold": (0x3EAD, 7, 1.0),
        "pitch_threshold": (0x4313, 7, 0.7),
        "split_count": (0x3EAE, 3, 8),
        "sample_start": (0x3EB0, 7, 0.0),
        "sample_end": (0x3EB1, 7, None),
        "start_key": (0x3EB2, 3, 36),
        "duration_mode": (0x3EBE, 3, "slice_end"),
        "assignment": (0x3EB5, 3, "key_chromatic"),
        "manual_bpm": (0x3EC1, 7, 60.0),
        "use_analyzed_bpm": (0x40CD, 5, False),
        "use_analyzed_root_f0": (0x40CE, 5, False),
    }

    def __init__(self, source, slices, *, name="Sliced sample", keep_in_memory=None, **options):
        self.slices, self.name = list(slices), name
        super().__init__(source, keep_in_memory=keep_in_memory, **options)

    def _validate(self):
        if self.options.keys() - self.FIELDS.keys():
            raise ValueError("unknown sliced sample option")
        for name, value in self.options.items():
            if self._raw is not None and value == self._initial_options.get(name):
                continue
            if name in SLICE_ENUMS:
                if not _valid_enum(value, SLICE_ENUMS[name]):
                    raise ValueError(f"{name} must be one of {list(SLICE_ENUMS[name])}")
            elif self.FIELDS[name][1] == 5:
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be boolean")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        for key in ("root_key", "start_key"):
            if not isinstance(self.options[key], int) or isinstance(self.options[key], bool) or not 0 <= self.options[key] <= 127:
                raise ValueError(f"{key} must be a MIDI key")
        if not isinstance(self.split_count, int) or isinstance(self.split_count, bool) or self.split_count < 1:
            raise ValueError("split_count must be a positive integer")
        if self.manual_bpm <= 0 or not 0 <= self.sample_start < self.sample_end <= self.source.duration + 1e-6:
            raise ValueError("invalid sliced sample tempo or playback boundaries")
        last = -1.0
        for value in self.slices:
            if not isinstance(value, Slice):
                raise TypeError("slices must contain Slice objects")
            value.to_object()
            if value.start < last or not self.sample_start <= value.start <= self.sample_end:
                raise ValueError("slice starts must be ordered within playback boundaries")
            last = value.start

    def to_object(self):
        self._validate()
        obj = copy.deepcopy(self._raw) if self._raw is not None else Obj(0x151D, [(m.F_NAME, 8, "SAMPLE")])
        if self._raw is None or self.source is not self._initial_source:
            _put(obj, 0x4112, Obj(0x15EB, [(0x748, 9, self.source.to_object())]))
        elif self.source._has_changes():
            analyzer = m.get(obj, 0x4112)
            _detach_item(m.get(analyzer, 0x748))
            _put(analyzer, 0x748, self.source.to_object())
        for name, value in self.options.items():
            if self._raw is not None and value == self._initial_options.get(name):
                continue
            fid, typ, _ = self.FIELDS[name]
            _put(obj, fid, SLICE_ENUMS.get(name, {}).get(value, value), None if typ == 3 else typ)
        _put(obj, 0x3EA2, self.name, 8)
        _put(obj, 0x3EA7, [s.to_object() for s in self.slices], 0x12)
        return obj

    @classmethod
    def from_object(cls, obj):
        source = AudioFile.from_object(m.get(m.get(obj, 0x4112), 0x748))
        result = cls.__new__(cls)
        result.source, result._raw, result.keep_in_memory = source, obj, None
        result.slices = [Slice.from_object(s) for s in m.get(obj, 0x3EA7, [])]
        result.name = m.get(obj, 0x3EA2, "Sliced sample")
        result.options = {}
        for name, (fid, _, default) in cls.FIELDS.items():
            value = m.get(obj, fid, source.duration if default is None else default)
            result.options[name] = next((k for k, v in SLICE_ENUMS[name].items() if v == value), value) if name in SLICE_ENUMS else value
        result._initial_options, result._initial_source = dict(result.options), source
        return result


ZONE_FIELDS = {
    "key_low": (0x76F, 0),
    "key_high": (0x770, 127),
    "velocity_low": (0x771, 1),
    "velocity_high": (0x772, 127),
    "select_low": (0x2696, 0),
    "select_high": (0x2697, 127),
    "group": (0x2693, -1),
    "key_low_fade": (0xFA6, 0),
    "key_high_fade": (0xFA7, 0),
    "velocity_low_fade": (0xFA8, 0),
    "velocity_high_fade": (0xFA9, 0),
    "select_low_fade": (0x2698, 0),
    "select_high_fade": (0x2699, 0),
    "zone_logic": (0x269A, "always_play"),
    "parameter_1": (0x274B, 0.0),
    "parameter_2": (0x274C, 0.0),
    "parameter_3": (0x274D, 0.0),
}
ZONE_LOGIC = {"always_play": 0, "round_robin": 1}


class Zone(_Options):
    FIELDS = ZONE_FIELDS

    def __init__(self, sample, **options):
        if not isinstance(sample, Sample):
            raise TypeError("zone sample must be a Sample")
        self.sample, self.options, self._raw = sample, {k: v[1] for k, v in self.FIELDS.items()}, None
        self._initial_sample = sample
        self.options.update(options)
        self._validate()

    def _validate(self):
        if self.options.keys() - self.FIELDS.keys():
            raise ValueError("unknown zone option")
        for name, value in self.options.items():
            if self._raw is not None and self._initial_options.get(name) == value:
                continue
            if name == "zone_logic":
                if not _valid_enum(value, ZONE_LOGIC):
                    raise ValueError("zone_logic must be always_play or round_robin")
            elif name.startswith("parameter_"):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{name} must be finite")
            elif isinstance(value, bool) or not isinstance(value, int) or not (-1 if name == "group" else 0) <= value <= 127:
                raise ValueError(f"{name} must be an integer 0..127")
        for prefix in ("key", "velocity", "select"):
            if self._raw is not None and all(
                self.options[prefix + "_" + bound] == self._initial_options.get(prefix + "_" + bound) for bound in ("low", "high")
            ):
                continue
            if self.options[prefix + "_low"] > self.options[prefix + "_high"]:
                raise ValueError(f"invalid {prefix} range")

    def to_object(self):
        obj = copy.deepcopy(self._raw) if self._raw else Obj(0x21A, [(0x2B9, 8, "ZONE")])
        self._edit_object(obj)
        resource = m.get(obj, 0x76E)
        analyzer = m.get(resource, 0x4112)
        item = m.get(analyzer if isinstance(analyzer, Obj) else resource, 0x748)
        if isinstance(item, Obj):
            reference = m.get(item, 0x129E)
            if isinstance(reference, Obj):
                _put(reference, 0x129C, [item], 0x12)
        return obj

    def _edit_object(self, obj):
        if self._raw is None or self.options != self._initial_options:
            self._validate()
        sample = m.get(obj, 0x76E)
        if self._raw is not None and sample is not None and self.sample is self._initial_sample:
            self.sample._edit_object(sample)
        else:
            _put(obj, 0x76E, self.sample.to_object())
        for name, value in self.options.items():
            if self._raw is not None and self._initial_options.get(name) == value:
                continue
            if name == "zone_logic":
                value = ZONE_LOGIC.get(value, value)
            _put(obj, self.FIELDS[name][0], float(value) if name.startswith("parameter_") else value)

    @classmethod
    def _import_settings(cls, sample, **options):
        obj = Obj(0x21A, [(0x2B9, 8, "ZONE"), (0x76E, 9, sample.to_object())])
        for name, (fid, default) in cls.FIELDS.items():
            value = options.get(name, default)
            if name == "zone_logic":
                if not _valid_enum(value, ZONE_LOGIC):
                    raise ValueError("unknown multisample zone logic")
                value = ZONE_LOGIC.get(value, value)
            _put(obj, fid, float(value) if name.startswith("parameter_") else value)
        return cls.from_object(obj)

    @classmethod
    def from_object(cls, obj):
        result = cls.__new__(cls)
        result.sample = Sample.from_object(m.get(obj, 0x76E))
        result._initial_sample = result.sample
        result.options = {k: m.get(obj, f, default) for k, (f, default) in cls.FIELDS.items()}
        result.options["zone_logic"] = next(
            (k for k, v in ZONE_LOGIC.items() if v == result.options["zone_logic"]), result.options["zone_logic"]
        )
        result._initial_options = dict(result.options)
        result._raw = obj
        return result


class Multisample(Asset):
    def __init__(
        self, zones, *, name="Custom", category="", creator="bwpreset", description="", groups=(), keywords="", keep_in_memory=None
    ):
        self.zones = list(zones)
        if not self.zones or any(not isinstance(z, Zone) for z in self.zones):
            raise ValueError("a multisample requires at least one Zone")
        self.name, self.category, self.creator, self.description = name, category, creator, description
        self.keep_in_memory = keep_in_memory
        self.groups, self.keywords = list(groups), keywords
        self._initial_groups = copy.deepcopy(self.groups)
        self._raw = None

    def to_object(self):
        if self._raw is None or self.groups != self._initial_groups:
            for name, color in self.groups:
                if (
                    not isinstance(name, str)
                    or len(color) != 4
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in color)
                ):
                    raise ValueError("groups must be (name, RGBA) pairs with finite colors 0..1")
        if any(
            z.group >= len(self.groups)
            and (self.groups != self._initial_groups or z._raw is None or z.group != z._initial_options.get("group"))
            for z in self.zones
        ):
            raise ValueError("zone group index does not exist; use -1 for ungrouped zones")
        obj = copy.deepcopy(self._raw) if self._raw else Obj(0x219, [(0x2B9, 8, "MULTISAMPLE")])
        original_zones = m.get(self._raw, 0x76D, []) if self._raw is not None else []
        if self._raw is not None and len(original_zones) == len(self.zones):
            targets = m.get(obj, 0x76D)
            reordered = []
            used = set()
            for zone in self.zones:
                index = next((i for i, old in enumerate(original_zones) if zone._raw is old), None)
                if index is None or index in used:
                    reordered.append(zone.to_object())
                else:
                    used.add(index)
                    target = targets[index]
                    zone._edit_object(target)
                    reordered.append(target)
            _put(obj, 0x76D, reordered, 0x12)
        else:
            _put(obj, 0x76D, [z.to_object() for z in self.zones], 0x12)
        if self._raw is None or self.groups != self._initial_groups:
            previous = m.get(obj, 0xFAC, [])
            groups = []
            for index, (name, color) in enumerate(self.groups):
                group = previous[index] if index < len(previous) else Obj(0x346, [(0x2B9, 8, "GROUP")])
                _put(group, 0xFAE, name)
                _put(group, 0x2692, color, 0x16)
                groups.append(group)
            _put(obj, 0xFAC, groups, 0x12)
        for fid, value in (
            (0xFB3, self.name),
            (0xFB4, self.category),
            (0xFB5, self.creator),
            (0xFB7, self.description),
            (0xFB6, self.keywords),
        ):
            if self._raw is not None and m.get(self._raw, fid, "") == value:
                continue
            _put(obj, fid, value)
        return obj

    def parameter_object(self):
        return apply_asset(self._parameter or Obj(SAMPLE_PARAM, [(0x2B9, 8, "SAMPLE")]), self)

    @classmethod
    def from_object(cls, obj):
        result = cls(
            [Zone.from_object(z) for z in m.get(obj, 0x76D, [])],
            name=m.get(obj, 0xFB3, ""),
            category=m.get(obj, 0xFB4, ""),
            creator=m.get(obj, 0xFB5, ""),
            description=m.get(obj, 0xFB7, ""),
            groups=[(m.get(g, 0xFAE, ""), m.get(g, 0x2692, [0, 0, 0, 1])) for g in m.get(obj, 0xFAC, [])],
            keywords=m.get(obj, 0xFB6, ""),
        )
        result._raw = obj
        return result

    @classmethod
    def from_file(cls, path, *, portable_path=None):
        path = Path(path)
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("multisample.xml"))
            if root.tag != "multisample":
                raise ValueError("expected multisample.xml")
            zones = []
            groups = [
                (g.get("name", ""), [int(g.get("color", "808080")[i : i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0])
                for g in root.findall("group")
            ]
            entries = [(entry, -1) for entry in root.findall("sample")]
            for layer in root.findall("layer"):
                index = len(groups)
                groups.append((layer.get("name", ""), [0.5, 0.5, 0.5, 1.0]))
                entries.extend((entry, index) for entry in layer.findall("sample"))
            for entry, layer_group in entries:
                subpath = _portable(entry.attrib["file"])
                ref = FileReference(path, portable_path=portable_path, sub_path=subpath, size=path.stat().st_size)
                audio = AudioFile.from_wav_bytes(archive.read(subpath), ref)
                rate = audio.sample_rate
                key = entry.find("key")
                key = {} if key is None else key.attrib
                loop = entry.find("loop")
                loop = {} if loop is None else loop.attrib
                track = key.get("track", "1")
                track = 1.0 if track == "true" else 0.0 if track == "false" else float(track)
                sample = Sample._import_settings(
                    audio,
                    root_key=int(key.get("root", 60)),
                    keytrack=track,
                    fine_tune=float(key.get("tune", entry.get("tune", 0))),
                    gain_db=float(entry.get("gain", 0)),
                    sample_start=float(entry.get("sample-start", 0)) / rate,
                    sample_end=float(entry.get("sample-stop", audio.frames)) / rate,
                    loop_start=float(loop.get("start", entry.get("sample-start", 0))) / rate,
                    loop_end=float(loop.get("stop", entry.get("sample-stop", audio.frames))) / rate,
                    loop_crossfade_length=float(loop.get("fade", 0)) / rate,
                    loop_mode=("loop" if loop.get("mode") == "sustain" else loop.get("mode", "off").replace("-", "_")),
                    reverse=entry.get("reverse", "false") == "true",
                    manual_bpm=float(entry.get("bpm", 60)),
                    use_analyzed_bpm=entry.get("use-auto-bpm", "false").lower() == "true",
                    use_analyzed_root_f0=key.get("use-auto-f0", "false").lower() == "true",
                )
                logic = entry.get("zone-logic", "round-robin" if entry.get("round-robin") == "1" else "always-play").replace("-", "_")
                options = {
                    "key_low": int(key.get("low", 0)),
                    "key_high": int(key.get("high", 127)),
                    "key_low_fade": int(key.get("low-fade", 0)),
                    "key_high_fade": int(key.get("high-fade", 0)),
                    "group": int(entry.get("group", layer_group)),
                    "zone_logic": logic,
                }
                options.update({f"parameter_{i}": float(entry.get(f"parameter-{i}", 0)) for i in (1, 2, 3)})
                for prefix, tag, low in (("velocity", "velocity", 1), ("select", "select", 0)):
                    element = entry.find(tag)
                    attrs = {} if element is None else element.attrib
                    options[prefix + "_low"] = int(attrs.get("low", low))
                    options[prefix + "_high"] = int(attrs.get("high", 127))
                    options[prefix + "_low_fade"] = int(attrs.get("low-fade", 0))
                    options[prefix + "_high_fade"] = int(attrs.get("high-fade", 0))
                zones.append(Zone._import_settings(sample, **options))
            result = cls(
                zones,
                name=root.get("name", path.stem),
                category=root.findtext("category", ""),
                creator=root.findtext("creator", ""),
                description=root.findtext("description", ""),
                groups=groups,
                keywords=" ".join(e.text or "" for e in root.findall("keywords/keyword")),
            )
            return result


PLAYER_FIELDS = {
    "playback_mode": (0x4194, 3, {"repitch": 0, "textures": 1, "cycles": 2, "spectral": 3, "fragments": 4}),
    "envelope_mode": (0x42E0, 3, {"ahdsr": 0, "one_shot": 1}),
    "playhead_freeze": (0x42E1, 5, None),
    "playhead_sync": (0x42E2, 5, None),
    "repitch_mode": (0x4195, 3, {"clean": 0, "analog": 1, "digital": 2}),
    "repitch_digital_varirate": (0x4196, 5, None),
    "repitch_digital_imaging_filter": (0x4197, 5, None),
    "cycles_mode": (0x4198, 3, {"bend": 0, "harmonics": 1, "phase_modulation": 2}),
    "cycles_use_positional_fade": (0x4207, 5, None),
    "fragments_maximum_number_of_grains": (0x4199, 3, None),
    "fragments_playhead_mode": (
        0x419B,
        3,
        {"sample_direction": 0, "playhead_direction": 1, "random_direction": 2, "alternate_direction": 3},
    ),
    "fragments_keep_grains_after_voice_ends": (0x419C, 5, None),
    "fragments_latch_initial_rate": (0x419D, 5, None),
    "fragments_repeat_at_initial_position": (0x419E, 5, None),
    "spectral_mode": (0x419F, 3, {"clean": 0, "bend_harmonics": 1}),
    "spectral_use_zero_latency_note_on": (0x4209, 5, None),
    "spectral_quality_mode": (0x41A0, 3, {"low": 0, "mid": 1, "high": 2, "ultra": 3}),
    "spectral_bend_harmonics_mode": (0x41A1, 3, {"unquantized": 0, "octave_quantized": 1, "scale_quantized": 2}),
    "spectral_formant_processing": (0x41A2, 5, None),
    "spectral_formant_root_coupling": (0x43C4, 5, None),
    "spectral_onset_processing": (0x41A3, 5, None),
    "spectral_onset_processing_threshold": (0x42C8, 7, None),
    "spectral_apply_tonality_limit": (0x43C5, 5, None),
    "module_analysis_output": (0x42FD, 5, None),
}


class SamplePlayer(Asset, _Options):
    FIELDS = PLAYER_FIELDS

    def __init__(self, **options):
        self.options = options
        self._validate()

    def _validate(self):
        for name, value in self.options.items():
            if name in self.__dict__.get("_initial_options", {}) and value == self._initial_options[name]:
                continue
            if name not in self.FIELDS:
                raise ValueError(f"unknown sample-player option {name}")
            _, typ, enum = self.FIELDS[name]
            if enum is not None:
                if not _valid_enum(value, enum):
                    raise ValueError(f"{name} must be one of {list(enum)}")
            elif typ == 5:
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be boolean")
            elif typ == 3:
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
                    raise ValueError(f"{name} must be an integer 1..256")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")

    def parameter_object(self):
        return apply_asset(self._parameter or Obj(SAMPLE_PLAYER_PARAM, [(0x2B9, 8, "SAMPLE_PLAYER")]), self)


WAVETABLE_FIELDS = {
    "disable_index_interpolation": (0x2D2F, 5, None),
    "phase_mode": (0x2D3C, 3, {"diffused": 0, "original": 1, "aligned": 2}),
    "unison_model": (0x2C7C, 3, {"flat": 0, "pyramid": 1, "prime": 2}),
    "enable_unison_phase_spread": (0x2D31, 5, None),
    "remove_dc": (0x3C54, 5, None),
    "remove_f0": (0x3C56, 5, None),
    "enable_discontinuity": (0x36B8, 5, None),
}


class Wavetable(Asset, _Options):
    FIELDS = WAVETABLE_FIELDS

    def __init__(self, frames, *, name="Custom", reference=None, **options):
        self.frames = tuple(tuple(float(v) for v in frame) for frame in frames)
        if not self.frames or not self.frames[0]:
            raise ValueError("a wavetable needs at least one nonempty frame")
        size = len(self.frames[0])
        if size >= 2**31 or len(self.frames) > 32767 or any(len(f) != size for f in self.frames):
            raise ValueError("wavetable frames must have equal sizes; at most 32767 frames")
        if any(not math.isfinite(v) or abs(v) > 3.4028234663852886e38 for f in self.frames for v in f):
            raise ValueError("wavetable samples must be finite float32 values")
        self.name, self.reference, self._raw = name, reference, None
        self.cycle_size, self.table_size = size, len(self.frames)
        self.options = options
        self._initial_name, self._initial_reference = name, reference

    def file_bytes(self):
        if self.frames is None:
            raise ValueError("load table data with Wavetable.from_file before writing it")
        return struct.pack("<4sIHH", b"vawt", self.cycle_size, self.table_size, 0) + struct.pack(
            f"<{self.cycle_size * self.table_size}f", *(v for frame in self.frames for v in frame)
        )

    def write(self, path):
        path = Path(path)
        path.write_bytes(self.file_bytes())
        self.reference = FileReference(path, size=path.stat().st_size)
        self._raw = None
        return path

    @classmethod
    def from_file(cls, path, *, portable_path=None, cycle_size=None, **options):
        path = Path(path)
        data = path.read_bytes()
        if data[:4] == b"vawt":
            if len(data) < 12:
                raise ValueError("truncated wavetable header")
            _, size, count, flags = struct.unpack("<4sIHH", data[:12])
            if flags & ~12:
                raise ValueError("unsupported wavetable flags")
            width, fmt = (2, "h") if flags & 4 else (4, "f")
            if not size or not count or count > 32767 or len(data) != 12 + size * count * width:
                raise ValueError("wavetable dimensions do not match its data")
            values = struct.unpack(f"<{size * count}{fmt}", data[12:])
            if flags & 4:
                values = tuple(v / (32768 if flags & 8 else 16384) for v in values)
        else:
            if cycle_size is None:
                raise ValueError("WAV wavetable import requires an explicit cycle_size")
            _, channels, width, frames, encoding, raw = _wav_data(data)
            if channels != 1:
                raise ValueError("WAV wavetable import requires mono data")
            if encoding == 3:
                values = struct.unpack(f"<{frames}{'f' if width == 4 else 'd'}", raw)
            elif width == 1:
                values = tuple((v - 128) / 128 for v in raw)
            else:
                values = tuple(
                    int.from_bytes(raw[i : i + width], "little", signed=True) / 2 ** (width * 8 - 1) for i in range(0, len(raw), width)
                )
            size = cycle_size
            if not isinstance(size, int) or size <= 0 or len(values) % size:
                raise ValueError("WAV length must be a multiple of cycle_size")
        result = cls(
            [values[i : i + size] for i in range(0, len(values), size)],
            name=path.stem,
            reference=FileReference(path, portable_path=portable_path, size=len(data)),
            **options,
        )
        return result

    def to_object(self):
        if self._raw is not None:
            result = copy.deepcopy(self._raw)
            item = m.get(result, 0x2C33)
            if self.reference is self._initial_reference:
                self.reference._edit_object(m.get(item, 0x129E))
            else:
                _put(item, 0x129E, self.reference.to_object())
                _put(m.get(item, 0x129E), 0x129C, [item], 0x12)
            if self.name != self._initial_name:
                _put(item, 0x129F, self.name)
            if self.reference.sub_path != m.get(item, 0xFB8, ""):
                _put(item, 0xFB8, self.reference.sub_path)
            for fid, value in ((0x2C14, self.cycle_size), (0x2C15, self.table_size)):
                if m.get(item, fid) != value:
                    _put(item, fid, value)
            return result
        if self.reference is None:
            raise ValueError("write the generated wavetable to a .wt file before assigning it")
        item = _file_item(0xF03, self.reference, self.name, [(0x2C14, 3, self.cycle_size), (0x2C15, 3, self.table_size)])
        return Obj(0xF0D, [(0x2C33, 9, item)])

    def parameter_object(self):
        cls = self._parameter.cls if self._parameter is not None else 0xF38
        return apply_asset(self._parameter or Obj(cls, [(0x2B9, 8, "WAVETABLE")]), self)

    @classmethod
    def from_reference(cls, reference, *, cycle_size, table_size, name="Custom", **options):
        if not isinstance(reference, FileReference):
            raise TypeError("reference must be a FileReference")
        if (
            any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (cycle_size, table_size))
            or cycle_size >= 2**31
            or table_size > 32767
        ):
            raise ValueError("wavetable dimensions must fit a positive int32 cycle size and int16 table size")
        result = cls.__new__(cls)
        result.reference, result.name, result.cycle_size, result.table_size = reference, name, cycle_size, table_size
        result.frames, result._raw, result.options = None, None, options
        result._initial_name, result._initial_reference = name, reference
        return result

    @classmethod
    def from_object(cls, obj):
        item = m.get(obj, 0x2C33)
        if not isinstance(item, Obj) or item.cls != 0xF03:
            raise ValueError("unsupported wavetable resource")
        result = cls.__new__(cls)
        result.reference = FileReference.from_object(item)
        result.name = m.get(item, 0x129F)
        result.cycle_size, result.table_size = m.get(item, 0x2C14), m.get(item, 0x2C15)
        result.frames, result._raw = None, obj
        result._initial_name, result._initial_reference = result.name, result.reference
        result.options = {}
        return result


def apply_asset(parameter: Obj, value) -> Obj:
    if parameter.cls not in ASSET_PARAMETER_CLASSES:
        raise ValueError(f"not an asset parameter: {parameter.cls:#x}")
    if value is None:
        result = copy.deepcopy(parameter)
        if parameter.cls == SAMPLE_PLAYER_PARAM:
            raise ValueError("SAMPLE_PLAYER cannot be cleared")
        _put(result, 0x74C if parameter.cls == SAMPLE_PARAM else 0x2C34, None)
        return result
    if isinstance(value, (str, Path)):
        if parameter.cls == SAMPLE_PARAM:
            value = Multisample.from_file(value) if str(value).endswith(".multisample") else Sample(AudioFile.from_file(value))
        elif parameter.cls in WAVETABLE_PARAMS:
            value = Wavetable.from_file(value)
    expected = (
        (Sample, Multisample, PreservedAsset)
        if parameter.cls == SAMPLE_PARAM
        else SamplePlayer
        if parameter.cls == SAMPLE_PLAYER_PARAM
        else (Wavetable, PreservedAsset)
    )
    if not isinstance(value, expected):
        raise TypeError(f"incompatible asset for parameter class {parameter.cls:#x}")
    if isinstance(value, PreservedAsset) and (parameter.cls == SAMPLE_PARAM) != (value._parameter.cls == SAMPLE_PARAM):
        raise TypeError("incompatible preserved asset parameter")
    result = copy.deepcopy(parameter)
    if value.__dict__.get("_restore_parameter") and value._parameter is not None and value._parameter.cls == parameter.cls:
        result = copy.deepcopy(value._parameter)
        known = (
            {0x74C, 0x17CE, 0x26A6}
            if parameter.cls == SAMPLE_PARAM
            else {f for f, _, _ in PLAYER_FIELDS.values()}
            if parameter.cls == SAMPLE_PLAYER_PARAM
            else {0x2C34, 0x2C35, *[f for f, _, _ in WAVETABLE_FIELDS.values()]}
        )
        for fid, typ, v in parameter.fields:
            if fid not in known:
                _put(result, fid, copy.deepcopy(v), typ)
    elif value._parameter is not None and value._parameter.cls == parameter.cls:
        existing = {f for f, _, _ in result.fields}
        memo = {id(value._parameter): result}
        result.fields.extend(copy.deepcopy((f, t, v), memo) for f, t, v in value._parameter.fields if f not in existing)
    if isinstance(value, SamplePlayer):
        value._validate()
        for name, v in value.options.items():
            fid, typ, enum = PLAYER_FIELDS[name]
            if enum is not None:
                v = enum.get(v, v)
            if m.get(result, fid) != v:
                _put(result, fid, v, None if typ == 3 else typ)
    else:
        if result.cls != parameter.cls:
            result.cls = parameter.cls
        _put(result, 0x74C if parameter.cls == SAMPLE_PARAM else 0x2C34, value.to_object())
        if isinstance(value, Wavetable):
            for name, v in value.options.items():
                if name not in WAVETABLE_FIELDS:
                    raise ValueError(f"unknown wavetable option {name}")
                if name == "enable_discontinuity" and parameter.cls != 0x12AD:
                    if (
                        value._parameter is not None
                        and value._parameter.cls != parameter.cls
                        and value.__dict__.get("_initial_options", {}).get(name) == v
                    ):
                        continue
                    raise ValueError("enable_discontinuity belongs to Wavetable LFO")
                if name not in ("disable_index_interpolation", "enable_discontinuity") and parameter.cls != 0xF38:
                    if (
                        value._parameter is not None
                        and value._parameter.cls != parameter.cls
                        and value.__dict__.get("_initial_options", {}).get(name) == v
                    ):
                        continue
                    raise ValueError(f"{name} belongs to the Wavetable oscillator")
                fid, typ, enum = WAVETABLE_FIELDS[name]
                unchanged = (
                    value._parameter is not None
                    and value._parameter.cls == parameter.cls
                    and value.__dict__.get("_initial_options", {}).get(name) == v
                )
                if enum:
                    if not unchanged and not _valid_enum(v, enum):
                        raise ValueError(f"{name} must be one of {list(enum)}")
                    v = enum.get(v, v)
                elif not unchanged and not isinstance(v, bool):
                    raise ValueError(f"{name} must be boolean")
                if m.get(result, fid) != v:
                    _put(result, fid, v)
        if isinstance(value, (Sample, Multisample)) and value.keep_in_memory is not None:
            if not isinstance(value.keep_in_memory, bool):
                raise ValueError("keep_in_memory must be boolean")
            _put(result, 0x26A6, value.keep_in_memory)
    return result


def decode_asset(parameter):
    parameter = copy.deepcopy(parameter)
    if parameter.cls == SAMPLE_PLAYER_PARAM:
        value = SamplePlayer()
        for name, (fid, _, enum) in PLAYER_FIELDS.items():
            if any(f == fid for f, _, _ in parameter.fields):
                v = m.get(parameter, fid)
                value.options[name] = next((k for k, index in enum.items() if v == index), v) if enum else v
    elif parameter.cls == SAMPLE_PARAM:
        obj = m.get(parameter, 0x74C)
        if obj is None:
            return PreservedAsset(parameter) if any(f not in (m.F_NAME, 0x74C) for f, _, _ in parameter.fields) else None
        if obj.cls not in (0x210, 0x219, 0x151D) or (obj.cls == 0x219 and not m.get(obj, 0x76D, [])):
            return PreservedAsset(parameter)
        try:
            value = (
                Multisample.from_object(obj)
                if obj.cls == 0x219
                else SlicedSample.from_object(obj)
                if obj.cls == 0x151D
                else Sample.from_object(obj)
            )
        except ValueError as exc:
            if str(exc) != "asset has no file reference":
                raise
            return PreservedAsset(parameter)
    elif parameter.cls in WAVETABLE_PARAMS:
        obj = m.get(parameter, 0x2C34)
        if obj is None:
            return PreservedAsset(parameter) if any(f not in (m.F_NAME, 0x2C34) for f, _, _ in parameter.fields) else None
        try:
            value = Wavetable.from_object(obj)
        except ValueError:
            return PreservedAsset(parameter)
        for name, (fid, _, enum) in WAVETABLE_FIELDS.items():
            if any(f == fid for f, _, _ in parameter.fields):
                v = m.get(parameter, fid)
                value.options[name] = next((k for k, index in enum.items() if v == index), v) if enum else v
    else:
        raise ValueError(f"not an asset parameter: {parameter.cls:#x}")
    value._parameter = parameter
    if isinstance(value, (Sample, Multisample)):
        value.keep_in_memory = m.get(parameter, 0x26A6)
    if hasattr(value, "options"):
        value._initial_options = dict(value.options)
    return value


def collect_dependencies(root):
    """Collect media references anywhere in an object graph, deduplicated by location."""
    dependencies = {}
    for obj in walk(root):
        if obj.cls != 0x4A6:
            continue
        item = Obj(0x4A8, [(0x129E, 9, obj)])
        ref = FileReference.from_object(item)
        key = ref.source_path, ref.portable_path, ref.package_path
        dependencies.setdefault(key, ref.dependency())
    return list(dependencies.values())


def dependency_metadata(root):
    """Known Bitwig metadata for package dependencies; local paths live in the body."""
    return [("referenced_packaged_file_ids", 0x19, sorted({d.package_path for d in collect_dependencies(root) if d.package_path}))]
