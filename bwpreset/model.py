"""Class and field IDs observed in Bitwig's preset object graph."""

from typing import Any

from .codec import Obj, Tagged

GENERATED_CREATOR = "bwpreset"

# Classes
PRESET = 0x561
DEVICE = 0x40
CONTENTS = 0xD3
GRID = 0x771
MODULE = 0x76F
MODULATOR = 0x6C9
MODULATOR_LIST = 0x75F
FX_SLOT = 0x24E
POLY = 0xD94
REMOTE_CONTROLS = 0x77D
REMOTE_PAGE = 0x77B
REMOTE_CONTROL = 0x77C
MOD_SOURCE = 0x2FC
MOD_ROUTING = 0x2FD
DESCRIPTOR = 0x7B
ENUM_DESCRIPTOR = 0x9B
ENUM_OPTION = 0x102

P_NUMBER = 0x85
P_ENUM = 0xF7
P_INT = 0x189
P_BOOL = 0x7F
P_INPUT = 0x7E2
P_FLAG = 0x6DB
P_TEXT = 0x6D9
P_DATA = 0x74F
PARAM_KINDS = {
    0x128C: "curve",
    P_NUMBER: "number",
    P_ENUM: "enum",
    P_INT: "int",
    P_BOOL: "bool",
    P_FLAG: "bool",
    P_TEXT: "text",
    P_DATA: "data",
    P_INPUT: "input",
    MOD_SOURCE: "mod_source",
}
VALUE_FIELD = {P_NUMBER: 0x136, P_ENUM: 0x273, P_INT: 0x330, P_BOOL: 0x12F, P_FLAG: 0x18F9, P_TEXT: 0x18F6, P_DATA: 0x19D2}

# Fields
F_NAME = 0x2B9
F_PRESET_NAME = 0x12DE
F_TITLE = 0x9A
F_USER_TITLE = 0x1559
F_CREATOR = 0x9B
F_CATEGORY = 0x9C
F_DEVICE_ID = 0x99
F_DEVICE = 0x1421
F_SELECTION = 0x1422
F_CONTENTS = 0xA4
F_PARAMS = 0x20C
F_LIST = 0x1A46
F_TYPE = 0x18C6
F_MODULE_CONTENTS = 0x18C7
F_SOURCE = 0x1C4A
F_X = 0x1A1A
F_Y = 0x1A1B
F_COLOR = 0x2643
F_MODULATORS = 0x18F5
F_REMOTE_CONTROLS = 0x1A85
F_REMOTE_PAGES = 0x1A7E
F_PAGE_NAME = 0x1B69
F_PAGE_CONTROLS = 0x1A7A
F_RC_NAME = 0x1A7B
F_RC_TARGET = 0x1A7C
F_RC_DESCRIPTOR = 0x1A7D
F_RC_INDEX = 0x1A88
F_ROUTINGS = 0xE20
F_ROUTE_TARGET = 0xE3D
F_ROUTE_DESCRIPTOR = 0x1334
F_ROUTE_AMOUNT = 0xE32
F_ROUTE_SCALE_SOURCE = 0x2D2C
F_ROUTE_ENABLED = 0x2D3B
F_ROUTE_MODE = 0x2C4C
F_PAGE_EXTRA = 0x1B79
F_POLY_GLIDE = 0x2902
F_POLY_SPREAD = 0x2907
F_ENUM_OPTIONS = 0x189
F_OPTION_INDEX = 0x28A
F_OPTION_LABEL = 0x28B
F_CHAIN = 0x8E1
F_CHAIN_DEVICES = 0x349
F_CHAIN_LIST = 0x87

DESCRIPTOR_FIELDS = {
    0x124: "min",
    0x125: "max",
    0x37B: "default",
    0x126: "scaling",
    0x127: "display",
    0xBC6: "decimals",
    0x7C4: "step",
    0x128: "unit",
}


def fields(obj: Obj) -> dict[int, Any]:
    return {fid: v for fid, _, v in obj.fields}


def get(obj: Obj, fid: int, default=None):
    if obj is None:
        return default
    for f, _, v in obj.fields:
        if f == fid:
            return v
    return default


def set_field(obj: Obj, fid: int, value):
    for i, (f, t, _) in enumerate(obj.fields):
        if f == fid:
            obj.fields[i] = (f, t, value)
            return
    raise KeyError(f"{fid:#x} not in {obj.cls:#x}")


def put_field(obj: Obj, fid: int, typ: int, value):
    """Set a known field's type/value, including sparse objects without that field."""
    for i, (f, _, _) in enumerate(obj.fields):
        if f == fid:
            obj.fields[i] = (fid, typ, value)
            return
    obj.fields.append((fid, typ, value))


def params(obj: Obj) -> list[Obj]:
    """Parameter objects of a module, modulator or device."""
    contents = get(obj, F_MODULE_CONTENTS) or get(obj, F_CONTENTS)
    result = get(contents, F_PARAMS, [])
    return [] if result is None else result


def param_value(p: Obj):
    return get(p, VALUE_FIELD[p.cls]) if p.cls in VALUE_FIELD else None


def scoped_walk(device: Obj):
    """Objects belonging to this device, excluding devices nested in its FX chains."""
    stack, seen = [device], set()
    while stack:
        v = stack.pop()
        if isinstance(v, (list, tuple)):
            stack.extend(reversed(v))
            continue
        if isinstance(v, dict):
            stack.extend(reversed(list(v.values())))
            continue
        if isinstance(v, Tagged):
            stack.append(v.value)
            continue
        if not isinstance(v, Obj) or id(v) in seen:
            continue
        seen.add(id(v))
        if v.cls == DEVICE and v is not device:
            continue
        yield v
        stack.extend(reversed([fv for _, _, fv in v.fields]))


# How Bitwig displays a stored number, keyed by descriptor (unit, scaling).
# Confirmed against Bitwig 6.1 displays; see experiments/round2.py "12 Units probe".
DISPLAY_HINTS = {
    (3, 5): "seconds = stored³ (0.5 → 125 ms, 1.5 → 3.38 s)",
    (4, 2): "MIDI note number (69 → 440 Hz)",
    (1, 0): "fraction shown as percent (0.25 → 25%)",
    (10, 6): "semitones",
}
