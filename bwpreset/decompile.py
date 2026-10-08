"""Turn a Grid preset into Python code that rebuilds it with bwpreset.build."""

import builtins
import base64
import keyword
import re
import sys

from . import model as m
from .build import POLY_CHOICES, POLY_FIELDS
from .codec import Preset, parse, serialize, walk
from .curves import CURVE_PARAM, F_EMBEDDED, Curve
from .schema import load
from .assets import ASSET_PARAMETER_CLASSES, decode_asset
from .bindings import SOURCE_PARAMETER, DESTINATION_PARAMETER, BINDING_CLASS_NAMES, decode_binding
from .state import get_state

PATH = re.compile(r"^(?:CONTENTS/MODULES/(\d+)/CONTENTS/(\w+)|MODULATORS/(\d+)/CONTENTS/(\w+)|CONTENTS/(\w+|POLY/GLIDE_TIME))$")
SKIP_KINDS = {"input", "mod_source"}
RESERVED = {"p", "m", "Patch"} | set(dir(builtins))


def var_name(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "module"
    if base[0].isdigit():
        base = f"m{base}"
    if keyword.iskeyword(base) or base in RESERVED:
        base = f"{base}_"
    name, i = base, 2
    while name in taken:
        name, i = f"{base}{i}", i + 1
    taken.add(name)
    return name


def fmt(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    return repr(v)


def kwargs(d: dict) -> str:
    plain = [f"{k}={fmt(v)}" for k, v in d.items() if k.isidentifier() and not keyword.iskeyword(k)]
    odd = {k: v for k, v in d.items() if not k.isidentifier() or keyword.iskeyword(k)}
    if odd:
        plain.append("**{" + ", ".join(f"{k!r}: {fmt(v)}" for k, v in odd.items()) + "}")
    return ", ".join(plain)


class DecompileError(Exception):
    pass


class Decompiler:
    def __init__(self, preset: Preset):
        self.schema, _, _ = load()
        self.kinds = {s: {e["id"]: k for k, e in self.schema[s].items()} for s in ("modules", "modulators")}
        self.preset = preset
        self.device = m.get(preset.body, m.F_DEVICE)
        self.own = list(m.scoped_walk(self.device))
        self.lines: list[str] = []
        self.unsupported: list[str] = []
        self.stale: list[str] = []
        self.taken: set[str] = set()
        self.module_vars: dict[str, str] = {}
        self.modulator_vars: dict[str, str] = {}
        self.var_params: dict[str, set[str]] = {}
        self.requires_lossless = False

    def is_stale(self, path: str) -> bool:
        """True if path points at a module or parameter that no longer exists in this preset."""
        mm = PATH.match(path)
        if not mm or mm[5] is not None:
            return False
        idx, name, section = (mm[1], mm[2], "modules") if mm[1] is not None else (mm[3], mm[4], "modulators")
        obj = (self.module_objs if section == "modules" else self.modulator_objs).get(idx)
        if obj is None:
            return True
        kind = self.kinds[section].get(str(m.get(obj, m.F_TYPE)))
        known = self.schema[section][kind]["params"] if kind else {m.get(p, m.F_NAME) for p in m.params(obj)}
        return name not in known

    def ref(self, path: str) -> str | None:
        mm = PATH.match(path)
        if not mm:
            return None
        if mm[1] is not None:
            v, name = self.module_vars.get(mm[1]), mm[2]
        elif mm[3] is not None:
            v, name = self.modulator_vars.get(mm[3]), mm[4]
        else:
            names = {m.get(p, m.F_NAME) for p in m.params(self.device)} | {"POLY/GLIDE_TIME"}
            return f'p["{mm[5]}"]' if mm[5] in names else None
        return f'{v}["{name}"]' if v and name in self.var_params[v] else None

    def check_descriptor(self, path):
        match = PATH.match(path)
        if match is None:
            return
        if match[5] is not None:
            key = (dict((k, v) for k, _, v in self.preset.meta).get("device_name"), match[5])
            known = key in load()[1]
        else:
            obj, name = (
                (self.module_objs.get(match[1]), match[2]) if match[1] is not None else (self.modulator_objs.get(match[3]), match[4])
            )
            if obj is None:
                return
            key = (m.get(obj, m.F_TYPE), name)
            known = key in load()[1] or any(m.get(p, m.F_NAME) == name and p.cls == m.P_NUMBER for p in m.params(obj))
        if not known:
            self.requires_lossless = True
            self.unsupported.append(f"descriptor for {path}; preserved through lossless import")

    def values(self, obj, kind: str, section: str) -> tuple[dict, list]:
        known = self.schema[section][kind]["params"]
        template = load()[1].get(m.get(obj, m.F_TYPE))
        current = {m.get(p, m.F_NAME): p for p in m.params(template)} if template is not None else {}
        values, other = {}, []
        for p in m.params(obj):
            name = m.get(p, m.F_NAME)
            if p.cls in m.VALUE_FIELD and any(f not in (m.F_NAME, m.VALUE_FIELD[p.cls], 0x7D3) for f, _, _ in p.fields):
                self.unsupported.append(f"{kind}.{name} wrapper state; retained by lossless import")
                self.requires_lossless = True
            if name not in current or current[name].cls != p.cls:
                self.requires_lossless = True
            if p.cls in ASSET_PARAMETER_CLASSES or p.cls in (SOURCE_PARAMETER, DESTINATION_PARAMETER):
                try:
                    value = decode_asset(p) if p.cls in ASSET_PARAMETER_CLASSES else decode_binding(p)
                    repr(value)
                    values[name] = value
                except (ValueError, TypeError, KeyError, AttributeError):
                    other.append(name)
                continue
            if p.cls == CURVE_PARAM:
                data = m.get(p, F_EMBEDDED)
                if data is None:
                    other.append(name)
                else:
                    try:
                        values[name] = Curve.from_parameter(p)
                    except ValueError:
                        other.append(name)
                continue
            pkind = m.PARAM_KINDS.get(p.cls)
            if pkind in SKIP_KINDS:
                continue
            if pkind is None or name not in known:
                other.append(name)
                continue
            values[name] = m.param_value(p)
        return values, other

    def node(self, obj, section: str, var_map: dict) -> tuple[str, str] | None:
        known = {
            m.F_NAME,
            m.F_TYPE,
            m.F_MODULE_CONTENTS,
            m.F_PRESET_NAME,
            m.F_TITLE,
            m.F_USER_TITLE,
            m.F_X,
            m.F_Y,
            m.F_COLOR,
            0xA3,
            0x2651,
            0x2652,
            0x1A19,
            0x9B,
            0x9C,
            0x9E,
            0x9F,
            0xA1,
            0xA2,
            0x3550,
        }
        if any(f not in known for f, _, _ in obj.fields):
            self.requires_lossless = True
            self.unsupported.append(f"{section[:-1]} instance fields; retained by lossless import")
        type_id = str(m.get(obj, m.F_TYPE))
        kind = self.kinds[section].get(type_id)
        if kind is None:
            self.unsupported.append(f"{section[:-1]} type {type_id} ({m.get(obj, m.F_TITLE)})")
            return None
        title = m.get(obj, m.F_USER_TITLE) or ""
        v = var_name(title or kind.split("/")[-1], self.taken)
        var_map[m.get(obj, m.F_NAME)] = v
        self.var_params[v] = set(self.schema[section][kind]["params"])
        values, other = self.values(obj, kind, section)
        for name in other:
            self.unsupported.append(f"{kind}.{name}")
        return v, kind, title, values, section

    def run(self) -> str:
        meta = {k: v for k, _, v in self.preset.meta}
        settings = {}
        poly = next((o for o in self.own if o.cls == m.POLY), None)
        if poly is not None:
            for key, fid in POLY_FIELDS.items():
                value = m.get(poly, fid)
                if key in POLY_CHOICES:
                    named = {v: k for k, v in POLY_CHOICES[key].items()}.get(value)
                    if value is not None and named is None:
                        self.unsupported.append(f"device setting {key}={value!r}; preserved through lossless import")
                        self.requires_lossless = True
                    value = named
                if value is not None:
                    settings[key] = value
            glide = m.get(poly, m.F_POLY_GLIDE)
            if glide is not None:
                settings["glide"] = m.param_value(glide)
        skeleton = load()[2].get(meta["device_name"])
        if skeleton is None:
            raise DecompileError(f"{meta['device_name']} presets aren't supported; only {list(load()[2])}")
        skeleton_params = {m.get(p, m.F_NAME) for p in m.params(m.get(skeleton.body, m.F_DEVICE))}
        for p in m.params(self.device):
            if p.cls in m.VALUE_FIELD:
                if m.get(p, m.F_NAME) in skeleton_params:
                    settings[m.get(p, m.F_NAME)] = m.param_value(p)
                else:
                    self.unsupported.append(f"device setting {m.get(p, m.F_NAME)}")
        name = m.get(self.device, m.F_PRESET_NAME) or "Untitled"
        out = [
            "from bwpreset.build import Curve, Patch",
            "from bwpreset.assets import Asset",
            f"from bwpreset.bindings import {', '.join(BINDING_CLASS_NAMES)}",
            "from uuid import UUID",
            "",
            f"p = Patch({meta['device_name']!r}, {name!r}, {kwargs(settings)})",
        ]

        grid = next(o for o in self.own if o.cls == m.GRID)
        self.module_objs = {m.get(o, m.F_NAME): o for o in m.get(grid, m.F_LIST)}
        self.modulator_objs = {m.get(o, m.F_NAME): o for o in m.get(m.get(self.device, m.F_MODULATORS), m.F_LIST, []) or []}
        modules = [(o, self.node(o, "modules", self.module_vars)) for o in m.get(grid, m.F_LIST)]
        for o, info in modules:
            if info:
                v, kind, title, values, _ = info
                args = [repr(kind), str(m.get(o, m.F_X)), str(m.get(o, m.F_Y))]
                if title:
                    args.append(f"title={title!r}")
                if values:
                    args.append(kwargs(values))
                out.append(f"{v} = p.add({', '.join(args)})")
                self.instance_state(o, v, out)
        modulators = [
            (o, self.node(o, "modulators", self.modulator_vars)) for o in m.get(m.get(self.device, m.F_MODULATORS), m.F_LIST, []) or []
        ]
        for o, info in modulators:
            if info:
                v, kind, title, values, _ = info
                args = [repr(kind)] + ([f"title={title!r}"] if title else [])
                args += [f"x={m.get(o, m.F_X)}", f"y={m.get(o, m.F_Y)}"] + ([kwargs(values)] if values else [])
                out.append(f"{v} = p.modulator({', '.join(args)})")
                self.instance_state(o, v, out)

        for o, info in modules:
            if not info:
                continue
            cables = {}
            for p in m.params(o):
                src = m.get(p, m.F_SOURCE) if p.cls == m.P_INPUT else ""
                if not src:
                    continue
                mm = PATH.match(src)
                if mm and mm[1] in self.module_vars:
                    port = mm[2]
                    cables[m.get(p, m.F_NAME)] = self.module_vars[mm[1]] + ("" if port == "OUT" else f'.out("{port}")')
                else:
                    self.unsupported.append(f"cable from {src}")
            if cables:
                out.append(f"{info[0]}.connect({', '.join(f'{k}={v}' for k, v in cables.items())})")

        poly = next((o for o in self.own if o.cls == m.POLY), None)
        device_source = [(poly, ("p",))] if poly is not None else []
        for o, info in modules + modulators + device_source:
            if not info:
                continue
            if o is poly:
                sources, named = [m.get(o, m.F_POLY_SPREAD)], False
            else:
                sources = [p for p in m.params(o) if p.cls == m.MOD_SOURCE]
                named = len(self.schema[info[4]][info[1]]["mod_sources"]) > 1
            for src in sources:
                for r in m.get(src, m.F_ROUTINGS, []) or []:
                    target = self.ref(m.get(r, m.F_ROUTE_TARGET))
                    scale_path = m.get(r, m.F_ROUTE_SCALE_SOURCE)
                    scale = self.ref(scale_path) if scale_path else None
                    if self.is_stale(m.get(r, m.F_ROUTE_TARGET)):
                        self.stale.append(f"routing to {m.get(r, m.F_ROUTE_TARGET)}")
                        continue
                    if target is None or (scale_path and scale is None):
                        self.unsupported.append(f"routing to {m.get(r, m.F_ROUTE_TARGET)}")
                        continue
                    self.check_descriptor(m.get(r, m.F_ROUTE_TARGET))
                    extra = f", source={m.get(src, m.F_NAME)!r}" if named else ""
                    extra += f", scale={scale}" if scale else ""
                    mode = m.get(r, m.F_ROUTE_MODE, 0)
                    from .build import ROUTING_MODES

                    if mode not in ROUTING_MODES.values():
                        self.unsupported.append(f"routing transfer function {mode}; use lossless=True")
                        continue
                    extra += f", mode={mode}" if mode else ""
                    extra += ", enabled=False" if m.get(r, m.F_ROUTE_ENABLED) is False else ""
                    out.append(f"{info[0]}.route({target}, {fmt(m.get(r, m.F_ROUTE_AMOUNT))}{extra})")

        for page in m.get(m.get(self.device, m.F_REMOTE_CONTROLS), m.F_REMOTE_PAGES, []) or []:
            page_name = m.get(page, m.F_PAGE_NAME)
            out.append(f"p.page({page_name!r})" if page_name else "p.page()")
            for rc in m.get(page, m.F_PAGE_CONTROLS):
                target = self.ref(m.get(rc, m.F_RC_TARGET))
                if self.is_stale(m.get(rc, m.F_RC_TARGET)):
                    self.stale.append(f"remote control for {m.get(rc, m.F_RC_TARGET)}")
                    continue
                if target is None:
                    self.unsupported.append(f"remote control for {m.get(rc, m.F_RC_TARGET)}")
                    continue
                self.check_descriptor(m.get(rc, m.F_RC_TARGET))
                out.append(f"p.remote({target}, {m.get(rc, m.F_RC_NAME)!r}, slot={m.get(rc, m.F_RC_INDEX)})")

        for slot in (o for o in self.own if o.cls == m.FX_SLOT):
            children = m.get(m.get(m.get(slot, m.F_CHAIN), m.F_CHAIN_DEVICES), m.F_CHAIN_LIST, []) or []
            if children:
                self.unsupported.append(f"{m.get(slot, m.F_NAME)} device chain")
                if any(o.cls == m.GRID for child in children for o in walk(child)):
                    self.requires_lossless = True
        if self.stale:
            out[1:1] = ["", "# Dropped stale references to deleted modules or parameters:"] + [
                f"#   {u}" for u in dict.fromkeys(self.stale)
            ]
        if self.unsupported:
            out[1:1] = ["", "# Not reproduced:"] + [f"#   {u}" for u in dict.fromkeys(self.unsupported)]
        return "\n".join(out) + "\n"

    def instance_state(self, obj, var, out):
        try:
            settings = get_state(obj)
        except ValueError as exc:
            self.unsupported.append(f"{var} instance state: {exc}")
            return
        if settings:
            out.append(f"{var}.state({kwargs(settings)})")


def decompile(preset: Preset, *, lossless: bool = False) -> tuple[str, list[str]]:
    """Return editable Python and fidelity issues. Lossless imports retain all state."""
    if lossless:
        return lossless_code(preset), []
    d = Decompiler(preset)
    code = d.run()
    if d.requires_lossless:
        return lossless_code(preset), d.unsupported
    return code, d.unsupported + [f"stale {s}" for s in d.stale]


def lossless_code(preset):
    from .build import Patch

    patch = Patch.from_preset(preset)
    data = base64.b85encode(serialize(preset)).decode("ascii")
    lines = ["from base64 import b85decode", "from bwpreset.build import Patch", "", "_preset = ("]
    lines += [f"    {data[i : i + 100]!r}" for i in range(0, len(data), 100)]
    lines += [")", "p = Patch.from_preset(b85decode(_preset))", ""]
    taken = {"_preset", "b85decode"}
    for section in ("modules", "modulators"):
        for index, node in enumerate(getattr(patch, section)):
            title = m.get(node.obj, m.F_USER_TITLE) or node.kind.split("/")[-1]
            var = var_name(title, taken)
            lines.append(f"{var} = p.{section}[{index}]  # {node.kind}")
            values = {n: m.param_value(p) for n, p in node.used.items() if isinstance(n, str) and p.cls in m.VALUE_FIELD}
            if values:
                lines.append(f"# {var}.set({kwargs(values)})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(decompile(parse(open(sys.argv[1], "rb").read()))[0], end="")
