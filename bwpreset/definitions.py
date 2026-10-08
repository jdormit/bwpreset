"""Translate installed module definitions into the sparse preset representation."""

import copy

from . import model as m
from .codec import Obj
from .bindings import DEFINITION_KINDS, parameter_from_definition
from .curves import Curve
from .assets import PLAYER_FIELDS


def module_definition(preset, base: Obj | None = None, *, kind="module"):
    root = preset.body
    obj = (
        copy.deepcopy(base)
        if base is not None
        else Obj(
            m.MODULE if kind == "module" else m.MODULATOR,
            [
                (m.F_NAME, 8, "0"),
                (m.F_PRESET_NAME, 8, ""),
                (m.F_TITLE, 8, ""),
                (m.F_USER_TITLE, 8, ""),
                (m.F_CREATOR, 8, "Bitwig"),
                (m.F_CATEGORY, 8, ""),
                (m.F_TYPE, 0x15, m.get(root, 0x18E1)),
                (m.F_MODULE_CONTENTS, 9, Obj(m.CONTENTS, [(m.F_NAME, 8, "CONTENTS"), (m.F_PARAMS, 0x12, [])])),
                (m.F_X, 1, 0),
                (m.F_Y, 1, 0),
                (m.F_COLOR, 1, 0),
            ],
        )
    )
    for fid, value in (
        (m.F_TYPE, m.get(root, 0x18E1)),
        (m.F_TITLE, m.get(root, 0x18E2)),
        (m.F_CATEGORY, m.get(root, 0x18E7)),
        (m.F_USER_TITLE, ""),
    ):
        m.set_field(obj, fid, value)
    # Let Bitwig choose the current module's dimensions rather than copying the base's.
    obj.fields = [(f, t, v) for f, t, v in obj.fields if f not in (0x2651, 0x2652)]
    params, descriptors, outputs = {}, {}, set()
    for atom in m.get(root, 0xAD, []):
        name, desc = m.get(atom, 0x2BD), m.get(atom, 0x2BE)
        cls, fid, t, value = None, None, None, None
        if atom.cls in (0x121, 0x6E3, 0x7F5) and name:
            cls, fid, t, value = m.P_NUMBER, 0x136, 7, m.get(atom, 0x2C8, 0.0)
        elif atom.cls in (0x57, 0xD34) and name:
            cls, fid, t, value = m.P_BOOL, 0x12F, 5, m.get(atom, 0xD2, False)
        elif atom.cls == 0xB4 and name:
            cls, fid, t, value = m.P_ENUM, 0x273, 3, m.get(atom, 0x1C9, 0)
        elif atom.cls == 0x18A and name:
            cls, fid, t, value = m.P_INT, 0x330, 3, m.get(atom, 0x33C, 0)
        elif atom.cls == 0x7D2:
            name = m.get(atom, 0x1BD4)
            cls, fid, t, value = m.P_INPUT, m.F_SOURCE, 8, ""
        elif atom.cls in (0x2FE, 0x789):
            name = m.get(atom, 0xE38)
            cls, fid, t, value = m.MOD_SOURCE, m.F_ROUTINGS, 0x12, []
        elif atom.cls == 0x6DA:
            name = m.get(atom, 0x18F3)
            cls, fid, t, value = m.P_TEXT, 0x18F6, 8, m.get(atom, 0x18F7, "")
        elif atom.cls == 0x7D0:
            outputs.add(m.get(atom, 0xE38))
        elif atom.cls in DEFINITION_KINDS:
            param = parameter_from_definition(atom)
            params[m.get(param, m.F_NAME)] = param
        elif atom.cls == 0x6D7:
            name = m.get(atom, 0x18F3)
            cls, fid, t, value = m.P_FLAG, 0x18F9, 5, m.get(atom, 0x18F2, False)
        elif atom.cls == 0x74E:
            name = m.get(atom, 0x19D6)
            cls, fid, t, value = m.P_DATA, 0x19D2, 0x17, m.get(atom, 0x19D1, [])
        elif atom.cls == 0x127D:
            name = m.get(atom, 0x35F4)
            data = m.get(atom, 0x35F2)
            if data is not None:
                params[name] = Curve.from_object(data).to_parameter()
                m.set_field(params[name], m.F_NAME, name)
        elif atom.cls == 0x211:
            name = m.get(atom, 0x74A)
            params[name] = Obj(0x212, [(m.F_NAME, 8, name), (0x74C, 10, None)])
        elif atom.cls == 0x14A:
            name = m.get(atom, 0x4160)
            fids = {fid for fid, _, _ in PLAYER_FIELDS.values()}
            params[name] = Obj(0x1622, [(m.F_NAME, 8, name)] + [(f, t, v) for f, t, v in atom.fields if f in fids])
        elif atom.cls in (0xF1B, 0x12AC):
            name = m.get(atom, 0x2B9B)
            params[name] = Obj(0xF38 if atom.cls == 0xF1B else 0x12AD, [(m.F_NAME, 8, name), (0x2C34, 10, None)])
        if cls is not None and name:
            param = Obj(cls, [(m.F_NAME, 8, name), (fid, t, value)])
            if cls == m.P_ENUM:
                param.fields.append((0x7D3, 5, False))
            params[name] = param
            if desc is not None:
                descriptors[name] = desc
    m.set_field(m.get(obj, m.F_MODULE_CONTENTS), m.F_PARAMS, list(params.values()))
    return obj, descriptors, outputs - {None, ""}
