"""Read format-4 streams using the encoding definition in the installed Bitwig jar."""

import functools
import struct
import zipfile
from .config import bitwig_jar


@functools.cache
def installed_key() -> bytes:
    jar = bitwig_jar()
    with zipfile.ZipFile(jar) as archive:
        data = archive.read("com/bitwig/base/serial/file/sYG.class")
    method = _method_code(data, "HEY", "()LFnp;")
    values = []
    pos, literal = 0, None
    while pos < len(method):
        opcode = method[pos]
        pos += 1
        if 2 <= opcode <= 8:
            literal = opcode - 3
        elif opcode == 0x10:
            literal = struct.unpack("b", method[pos : pos + 1])[0]
            pos += 1
        elif opcode == 0x11:
            literal = struct.unpack(">h", method[pos : pos + 2])[0]
            pos += 2
        elif opcode == 0x54:
            if literal is None:
                raise ValueError("cannot read Bitwig encoding definition")
            values.append(literal & 255)
            literal = None
        elif opcode == 0xBC:
            pos += 1
        elif opcode in (0xBB, 0xB7):
            pos += 2
        elif opcode not in (0x59, 0x4C, 0x2B, 0xB0):
            raise ValueError(f"unsupported Bitwig encoding definition instruction {opcode:#x}")
    if len(values) != 128:
        raise ValueError("unexpected Bitwig encoding key length")
    return bytes(values)


def _method_code(data: bytes, name: str, descriptor: str) -> bytes:
    """Read one Code attribute from a JVM class file; no Java runtime needed."""
    pos = 8

    def take(n):
        nonlocal pos
        value = data[pos : pos + n]
        pos += n
        if len(value) != n:
            raise ValueError("truncated Bitwig class file")
        return value

    def u2():
        return int.from_bytes(take(2), "big")

    def u4():
        return int.from_bytes(take(4), "big")

    pool = {}
    count, i = u2(), 1
    while i < count:
        tag = take(1)[0]
        if tag == 1:
            pool[i] = take(u2()).decode("utf-8", errors="replace")
        else:
            sizes = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4, 11: 4, 12: 4, 15: 3, 16: 2, 17: 4, 18: 4, 19: 2, 20: 2}
            take(sizes[tag])
            if tag in (5, 6):
                i += 1
        i += 1
    take(6)
    take(2 * u2())
    for section in ("fields", "methods"):
        for _ in range(u2()):
            take(2)
            item_name, item_desc = pool[u2()], pool[u2()]
            for _ in range(u2()):
                attr_name, length = pool[u2()], u4()
                attr = take(length)
                if section == "methods" and item_name == name and item_desc == descriptor and attr_name == "Code":
                    size = int.from_bytes(attr[4:8], "big")
                    return attr[8 : 8 + size]
    raise ValueError("cannot locate Bitwig encoding definition")


def decode_stream(data: bytes, key: bytes | None = None) -> bytes:
    if len(data) < 17 or data[0] != 0:
        raise ValueError("unsupported Bitwig stream encoding")
    key = installed_key() if key is None else key
    salt, payload = data[1:17], data[17:]
    rotating, fixed = salt + key[:16], key[16:]
    result = bytearray(len(payload))
    for i, v in enumerate(payload):
        shift = (i // len(fixed)) & 7
        b = rotating[i % len(rotating)]
        rotated = ((b >> shift) | (b << (8 - shift))) & 255
        result[i] = v ^ fixed[i % len(fixed)] ^ rotated
    return bytes(result)
