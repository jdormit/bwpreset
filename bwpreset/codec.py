import struct
import uuid
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"BtWg"
LIST_END = 3
_copy_references = ContextVar("bwpreset_copy_references", default=frozenset())


@contextmanager
def preserve_copy_references(objects):
    token = _copy_references.set(frozenset(id(obj) for obj in objects))
    try:
        yield
    finally:
        _copy_references.reset(token)


class ParseError(Exception):
    pass


@dataclass(eq=False)
class Obj:
    cls: int
    fields: list[tuple[int, int, Any]] = field(default_factory=list)

    def __deepcopy__(self, memo):
        if id(self) in _copy_references.get():
            return self
        result = Obj(self.cls)
        memo[id(self)] = result
        result.__dict__.update(copy.deepcopy(self.__dict__, memo))
        return result


@dataclass
class Ref:
    index: int


@dataclass
class Tagged:
    """An object paired with a string, e.g. a device and the name it's stored under."""

    value: Any
    tag: str


@dataclass
class Preset:
    header: str
    meta: list[tuple[str, int, Any]]
    body: Obj
    meta_padding: int
    attachment: bytes = b""


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos
        self.objects: list[Obj] = []

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ParseError(f"read past end at {self.pos:#x}")
        b = self.data[self.pos : self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def string(self) -> str:
        n = self.u32()
        if n & 0x80000000:
            return self.take((n & 0x7FFFFFFF) * 2).decode("utf-16-be")
        return self.take(n).decode("utf-8")


def read_value(r: Reader, t: int) -> Any:
    match t:
        case 0x01:
            return struct.unpack(">b", r.take(1))[0]
        case 0x02:
            return struct.unpack(">h", r.take(2))[0]
        case 0x03:
            return r.i32()
        case 0x04:
            return struct.unpack(">q", r.take(8))[0]
        case 0x05:
            return r.u8() != 0
        case 0x06:
            return struct.unpack(">f", r.take(4))[0]
        case 0x07:
            return struct.unpack(">d", r.take(8))[0]
        case 0x08:
            return r.string()
        case 0x09:
            return read_object(r)
        case 0x0A:
            return None
        case 0x0B:
            return Ref(r.i32())
        case 0x0D:
            return r.take(r.u32())
        case 0x0F:
            n = r.u32()
            return list(struct.unpack(f">{n}i", r.take(4 * n)))
        case 0x14:
            entries = {}
            while r.u8():
                key = r.string()
                entries[key] = read_nested(r)
            return entries
        case 0x1A:
            return Tagged(read_nested(r), r.string())
        case 0x12:
            items = []
            while True:
                items.append(read_nested(r))
                if items[-1] is LIST_END_MARK:
                    items.pop()
                    return items
        case 0x15:
            return uuid.UUID(bytes=r.take(16))
        case 0x16:
            return list(struct.unpack(">4f", r.take(16)))
        case 0x17:
            n = r.u32()
            return list(struct.unpack(f">{n}f", r.take(4 * n)))
        case 0x18:
            n = r.u32()
            return list(struct.unpack(f">{n}d", r.take(8 * n)))
        case 0x19:
            return [r.string() for _ in range(r.u32())]
        case _:
            raise ParseError(f"unknown type {t:#x} at {r.pos - 1:#x}")


LIST_END_MARK = object()


def read_nested(r: Reader) -> Any:
    cls = r.i32()
    if cls == LIST_END:
        return LIST_END_MARK
    if cls == 1:
        return Ref(r.i32())
    return read_object_body(r, cls)


def read_object(r: Reader) -> Obj:
    return read_object_body(r, r.i32())


def read_object_body(r: Reader, cls: int) -> Obj:
    obj = Obj(cls)
    r.objects.append(obj)
    while True:
        start = r.pos
        fid = r.i32()
        if fid == 0:
            return obj
        t = r.u8()
        try:
            obj.fields.append((fid, t, read_value(r, t)))
        except ParseError as e:
            raise ParseError(f"{e} (field {fid:#x} of class {cls:#x} at {start:#x})") from e


def read_meta(r: Reader) -> list[tuple[str, int, Any]]:
    entries = []
    while r.i32() == 1:
        key = r.string()
        t = r.u8()
        entries.append((key, t, read_value(r, t)))
    return entries


def parse(data: bytes) -> Preset:
    if data[:4] != MAGIC:
        raise ParseError("bad magic")
    header = data[4:42].decode("ascii")
    body_offset = int(header[12:20], 16)
    if header[4:8] == "0004":
        from .encoding import decode_stream

        attachment_offset = int(header[28:36], 16) or len(data)
        meta_bytes = decode_stream(data[42:body_offset])
        # Metadata padding is outside the encoded stream, so only read its object.
        r = Reader(meta_bytes)
        if r.i32() != 4 or r.string() != "meta":
            raise ParseError("expected encoded meta section")
        meta = read_meta(r)
        body_bytes = decode_stream(data[body_offset:attachment_offset])
        r = Reader(body_bytes)
        body = read_object(r)
        if r.pos != len(body_bytes):
            raise ParseError("trailing encoded body data")
        resolve_refs(r.objects)
        plain_header = header[:4] + "0002" + header[8:]
        return Preset(plain_header, meta, body, 1, data[attachment_offset:])
    r = Reader(data, 42)
    if r.i32() != 4 or r.string() != "meta":
        raise ParseError("expected meta section")
    meta = read_meta(r)
    padding = body_offset - r.pos
    r = Reader(data, body_offset)
    body = read_object(r)
    attachment_offset = int(header[28:36], 16)
    if r.pos != len(data) and (not attachment_offset or r.pos != attachment_offset):
        raise ParseError(f"trailing data at {r.pos:#x} of {len(data):#x}")
    resolve_refs(r.objects)
    return Preset(header, meta, body, padding, data[r.pos :])


def resolve_refs(objects: list[Obj]):
    def resolve(v):
        if isinstance(v, Ref):
            if not 1 <= v.index <= len(objects):
                raise ParseError(f"dangling reference {v.index}")
            return objects[v.index - 1]
        if isinstance(v, list) and any(isinstance(x, Ref) for x in v):
            return [resolve(x) for x in v]
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        if isinstance(v, Tagged):
            return Tagged(resolve(v.value), v.tag)
        return v

    for obj in objects:
        obj.fields = [(f, 0x09 if t == 0x0B else t, resolve(v)) for f, t, v in obj.fields]


def walk(v, seen=None):
    """Yield every object reachable from v once, in serialization (pre-)order."""
    seen = set() if seen is None else seen
    if isinstance(v, Obj):
        if id(v) in seen:
            return
        seen.add(id(v))
        yield v
        for _, _, fv in v.fields:
            yield from walk(fv, seen)
    elif isinstance(v, Tagged):
        yield from walk(v.value, seen)
    elif isinstance(v, (list, dict)):
        for x in v.values() if isinstance(v, dict) else v:
            yield from walk(x, seen)


class Writer:
    def __init__(self):
        self.buf = bytearray()
        self.written: dict[int, int] = {}

    def u8(self, v: int):
        self.buf.append(v)

    def i32(self, v: int):
        self.buf += struct.pack(">i", v)

    def u32(self, v: int):
        self.buf += struct.pack(">I", v)

    def string(self, s: str):
        try:
            b = s.encode("ascii")
            self.u32(len(b))
        except UnicodeEncodeError:
            b = s.encode("utf-16-be")
            self.u32(0x80000000 | (len(b) // 2))
        self.buf += b


def write_value(w: Writer, t: int, v: Any):
    match t:
        case 0x01:
            w.buf += struct.pack(">b", v)
        case 0x02:
            w.buf += struct.pack(">h", v)
        case 0x03:
            w.i32(v)
        case 0x04:
            w.buf += struct.pack(">q", v)
        case 0x05:
            w.u8(1 if v else 0)
        case 0x06:
            w.buf += struct.pack(">f", v)
        case 0x07:
            w.buf += struct.pack(">d", v)
        case 0x08:
            w.string(v)
        case 0x09:
            write_object(w, v)
        case 0x0A:
            pass
        case 0x0B:
            w.i32(w.written[id(v)])
        case 0x0D:
            w.u32(len(v))
            w.buf += v
        case 0x0F:
            w.u32(len(v))
            w.buf += struct.pack(f">{len(v)}i", *v)
        case 0x14:
            for key, item in v.items():
                w.u8(1)
                w.string(key)
                write_nested(w, item)
            w.u8(0)
        case 0x1A:
            write_nested(w, v.value)
            w.string(v.tag)
        case 0x12:
            for item in v:
                write_nested(w, item)
            w.i32(LIST_END)
        case 0x15:
            w.buf += v.bytes
        case 0x16:
            w.buf += struct.pack(">4f", *v)
        case 0x17:
            w.u32(len(v))
            w.buf += struct.pack(f">{len(v)}f", *v)
        case 0x18:
            w.u32(len(v))
            w.buf += struct.pack(f">{len(v)}d", *v)
        case 0x19:
            w.u32(len(v))
            for s in v:
                w.string(s)
        case _:
            raise ValueError(f"unknown type {t:#x}")


def write_nested(w: Writer, v: Obj):
    if id(v) in w.written:
        w.i32(1)
        w.i32(w.written[id(v)])
    else:
        write_object(w, v)


def write_object(w: Writer, obj: Obj):
    w.written[id(obj)] = len(w.written) + 1
    w.i32(obj.cls)
    for fid, t, v in obj.fields:
        if t == 0x09 and id(v) in w.written:
            t = 0x0B
        w.i32(fid)
        w.u8(t)
        write_value(w, t, v)
    w.i32(0)


def serialize(p: Preset) -> bytes:
    w = Writer()
    w.i32(4)
    w.string("meta")
    for key, t, v in p.meta:
        w.i32(1)
        w.string(key)
        w.u8(t)
        write_value(w, t, v)
    w.i32(0)
    body_offset = 42 + len(w.buf) + p.meta_padding
    w.buf += b" " * (p.meta_padding - 1) + b"\n"
    head = p.header[:12] + f"{body_offset:08x}" + p.header[20:]
    out = MAGIC + head.encode("ascii") + bytes(w.buf)
    w2 = Writer()
    write_object(w2, p.body)
    if p.attachment:
        end = len(out) + len(w2.buf)
        head = head[:28] + f"{end:08x}" + head[36:]
        out = MAGIC + head.encode("ascii") + bytes(w.buf)
    return out + bytes(w2.buf) + p.attachment
