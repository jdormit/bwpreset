"""Learn module, modulator and device schemas from the presets installed on this machine."""

import collections
import copy
import functools
import hashlib
import json
import os
import pickle
import plistlib
import re
import sys
import uuid
from pathlib import Path

from . import model as m
from .codec import Obj, parse, walk
from .config import installation, library_root

CORPUS_DIRS = [
    Path.home() / "Library/Application Support/Bitwig/Bitwig Studio/installed-packages",
    Path.home() / "Documents/Bitwig Studio/Library",
    Path.home() / "Documents/Bitwig Studio/Projects",
    library_root(),
]
if "BWGRID_CORPUS_PATHS" in os.environ:
    CORPUS_DIRS = [Path(p).expanduser() for p in os.environ["BWGRID_CORPUS_PATHS"].split(os.pathsep) if p] + [library_root()]
CACHE_DIR = Path(os.environ.get("BWGRID_CACHE_HOME", str(Path.home() / ".cache/bwpreset"))).expanduser()
CACHE = CACHE_DIR / "schema.pkl"
BITWIG = installation()
LIBRARY = library_root(BITWIG)
LOCALIZATION = LIBRARY.parent / "localization"
GRID_DEVICES = {"Poly Grid", "FX Grid", "Note Grid"}
MODULE_PATH = re.compile(r"^CONTENTS/MODULES/(\d+)/CONTENTS/(\w+)$")
MODULATOR_PATH = re.compile(r"^MODULATORS/(\d+)/CONTENTS/(\w+)$")
DEVICE_PATH = re.compile(r"^CONTENTS/(\w+|POLY/GLIDE_TIME)$")


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in v.split(".") if x.isdigit()) if v != "unnamed" else (0,)


def corpus_files():
    for d in CORPUS_DIRS:
        for root, _, files in os.walk(d):
            for f in files:
                if f.endswith((".bwpreset", ".bwclip", ".bwproject", ".bwmodule", ".bwmodulator", ".bwdevice")):
                    yield os.path.join(root, f)


def is_generated(preset) -> bool:
    return any(k == "creator" and v == m.GENERATED_CREATOR for k, _, v in preset.meta)


def fingerprint() -> tuple:
    """Changes when Bitwig is updated or presets are added, removed or edited."""
    try:
        version = plistlib.loads((BITWIG / "Info.plist").read_bytes()).get("CFBundleShortVersionString")
    except OSError:
        version = None
    digest = hashlib.sha256()
    for path in sorted(corpus_files()):
        stat = os.stat(path)
        digest.update(f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return (5, str(LIBRARY), version, digest.hexdigest())


def describe_descriptor(d: Obj) -> dict:
    if d.cls == m.ENUM_DESCRIPTOR:
        return {"options": {o_f[m.F_OPTION_INDEX]: o_f[m.F_OPTION_LABEL] for o_f in map(m.fields, m.get(d, m.F_ENUM_OPTIONS))}}
    if d.cls == 0x8F:
        return {name: m.get(d, fid) for fid, name in {0x159: "min", 0x15A: "max", 0x1B2C: "default"}.items() if m.get(d, fid) is not None}
    return {name: m.get(d, fid) for fid, name in m.DESCRIPTOR_FIELDS.items() if m.get(d, fid) is not None}


class Learner:
    def __init__(self):
        self.types: dict[uuid.UUID, dict] = {}
        self.devices: dict[str, tuple] = {}

    def entry(self, obj: Obj, kind: str, version: tuple) -> dict:
        f = m.fields(obj)
        e = self.types.setdefault(
            f[m.F_TYPE],
            {
                "kind": kind,
                "id": f[m.F_TYPE],
                "titles": collections.Counter(),
                "user_titles": collections.Counter(),
                "title_versions": {},
                "param_objs": {},
                "category": f[m.F_CATEGORY],
                "count": 0,
                "outputs": set(),
                "params": {},
                "template": None,
                "template_key": None,
            },
        )
        e["count"] += 1
        if f.get(m.F_USER_TITLE):
            e["user_titles"][f[m.F_TITLE]] += 1
        else:
            e["titles"][f[m.F_TITLE]] += 1
            e["title_versions"][f[m.F_TITLE]] = max(version, e["title_versions"].get(f[m.F_TITLE], ()))
        key = (version, len(m.params(obj)))
        if e["template_key"] is None or key > e["template_key"]:
            e["template"], e["template_key"] = obj, key
        for p in m.params(obj):
            pf = m.fields(p)
            known = e["param_objs"].get(pf[m.F_NAME])
            if known is None or version > known[0]:
                e["param_objs"][pf[m.F_NAME]] = (version, p)
            rec = e["params"].setdefault(
                pf[m.F_NAME],
                {"kind": m.PARAM_KINDS.get(p.cls, f"{p.cls:#x}"), "values": collections.Counter(), "descriptors": collections.Counter()},
            )
            v = m.param_value(p)
            if v is not None and not isinstance(v, list):
                rec["values"][v] += 1
        return e

    def add_descriptor(self, type_id, param, descriptor: Obj):
        rec = self.types.get(type_id, {}).get("params", {}).get(param)
        if rec is not None and isinstance(descriptor, Obj):
            key = json.dumps(describe_descriptor(descriptor), sort_keys=True)
            rec["descriptors"][key] += 1
            rec.setdefault("descriptor_objs", {}).setdefault(key, descriptor)

    def learn(self, path: str):
        preset = parse(Path(path).read_bytes())
        if is_generated(preset):
            return
        meta = {k: v for k, _, v in preset.meta}
        version = version_key(meta.get("application_version_name", "unnamed"))
        if path.endswith((".bwmodule", ".bwmodulator", ".bwdevice")):
            return
        if meta.get("device_name") in GRID_DEVICES and len(preset.body.fields) and m.get(preset.body, m.F_DEVICE):
            key = ("Empty" in Path(path).name, version, -os.path.getsize(path))
            name = meta["device_name"]
            if name not in self.devices or key > self.devices[name][0]:
                self.devices[name] = (key, preset)
        for device in (o for o in walk(preset.body) if o.cls == m.DEVICE):
            self.learn_device(device, version)

    def learn_device(self, device: Obj, version: tuple):
        own = list(m.scoped_walk(device))
        grid = next((o for o in own if o.cls == m.GRID), None)
        modules = {m.get(o, m.F_NAME): o for o in (m.get(grid, m.F_LIST) if grid else [])}
        modulators = {m.get(o, m.F_NAME): o for o in m.get(m.get(device, m.F_MODULATORS), m.F_LIST) or []}
        for o in modules.values():
            self.entry(o, "module", version)
        for o in modulators.values():
            self.entry(o, "modulator", version)
        for o in modules.values():
            for p in m.params(o):
                src = m.get(p, m.F_SOURCE) if p.cls == m.P_INPUT else None
                if src and (mm := MODULE_PATH.match(src)) and mm[1] in modules:
                    self.types[m.get(modules[mm[1]], m.F_TYPE)]["outputs"].add(mm[2])
        dev_type = ("device", m.get(device, m.F_TITLE))
        for o in own:
            if o.cls == m.MOD_ROUTING:
                target, desc = m.get(o, m.F_ROUTE_TARGET), m.get(o, m.F_ROUTE_DESCRIPTOR)
            elif o.cls == m.REMOTE_CONTROL:
                target, desc = m.get(o, m.F_RC_TARGET), m.get(o, m.F_RC_DESCRIPTOR)
            else:
                continue
            if (mm := MODULE_PATH.match(target)) and mm[1] in modules:
                self.add_descriptor(m.get(modules[mm[1]], m.F_TYPE), mm[2], desc)
            elif (mm := MODULATOR_PATH.match(target)) and mm[1] in modulators:
                self.add_descriptor(m.get(modulators[mm[1]], m.F_TYPE), mm[2], desc)
            elif (mm := DEVICE_PATH.match(target)) and m.get(device, m.F_TITLE) in GRID_DEVICES:
                self.types.setdefault(dev_type, {"params": {}})["params"].setdefault(
                    mm[1], {"kind": "number", "values": collections.Counter(), "descriptors": collections.Counter()}
                )
                self.add_descriptor(dev_type, mm[1], desc)


TITLE_OVERRIDES = {uuid.UUID("b3a19e19-da13-491d-b569-16ff5cbb109b"): "Label"}


def type_title(e: dict) -> str:
    if e["id"] in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[e["id"]]
    if e["titles"]:
        return max(e["titles"], key=lambda t: (e["title_versions"][t], e["titles"][t]))
    return e["user_titles"].most_common(1)[0][0]


def load_properties(name: str) -> dict[str, str]:
    path = LOCALIZATION / f"{name}-resources.properties"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().replace("\\n", "\n")
    return out


def loc_key(title: str) -> str:
    return title.lower().replace(" ", "_")


def finalize(learner: Learner) -> dict:
    module_desc = load_properties("Module-descriptions") | load_properties("Modulator-descriptions")
    param_desc = load_properties("Modules") | load_properties("Modulators")
    out = {"modules": {}, "modulators": {}, "devices": {}, "aliases": {"modules": {}, "modulators": {}}}
    for type_id, e in learner.types.items():
        if isinstance(type_id, tuple):
            out["devices"][type_id[1]] = {
                "params": {
                    k: {"descriptor": json.loads(v["descriptors"].most_common(1)[0][0])} for k, v in e["params"].items() if v["descriptors"]
                }
            }
            continue
        title = type_title(e)
        key = loc_key(title)
        prefix = "module" if e["kind"] == "module" else "modulator"
        params = {}
        for name, rec in e["params"].items():
            p = {"kind": rec["kind"]}
            parameter = e["param_objs"][name][1]
            if getattr(parameter, "binding_kind", None) is not None:
                p["selector_kind"] = parameter.binding_kind.value
            if rec["descriptors"]:
                p["descriptor"] = json.loads(rec["descriptors"].most_common(1)[0][0])
            if rec["values"]:
                p["common_values"] = [v for v, _ in rec["values"].most_common(4)]
            if d := param_desc.get(f"{key}.{name.lower()}.desc"):
                p["description"] = d
            params[name] = p
        entry = {
            "id": str(type_id),
            "name": title,
            "category": e["category"],
            "description": module_desc.get(f"{prefix}.{key}.desc", ""),
            "count": e["count"],
            "inputs": [k for k, v in params.items() if v["kind"] == "input"],
            "outputs": sorted(e["outputs"]),
            "mod_sources": [k for k, v in params.items() if v["kind"] == "mod_source"],
            "params": params,
        }
        out[f"{prefix}s"][f"{e['category']}/{title}"] = entry
        for alias in e.get("aliases", []):
            if alias != f"{e['category']}/{title}":
                out["aliases"][f"{prefix}s"][alias] = f"{e['category']}/{title}"
    return out


@functools.cache
def load() -> tuple[dict, dict[uuid.UUID, Obj], dict[str, object]]:
    """Return (schema, templates, device skeleton presets by name).

    templates maps a type UUID to a module/modulator object, and (type UUID or
    device name, param name) to that parameter's descriptor object.
    """
    fp = fingerprint()
    if CACHE.exists():
        cached_fp, data = pickle.loads(CACHE.read_bytes())
        if cached_fp == fp:
            return data
    learner = Learner()
    for path in corpus_files():
        if path.endswith((".bwmodule", ".bwmodulator", ".bwdevice")):
            continue
        try:
            learner.learn(path)
        except Exception as e:
            print(f"skipping {path}: {e}", file=sys.stderr)
    from .definitions import module_definition

    definition_paths = list((LIBRARY / "modules").glob("*.bwmodule")) + list((LIBRARY / "modulators").glob("*.bwmodulator"))
    for path in definition_paths:
        definition = parse(path.read_bytes())
        kind = "modulator" if path.suffix == ".bwmodulator" else "module"
        module, descriptors, outputs = module_definition(definition, kind=kind)
        version = version_key(dict((k, v) for k, _, v in definition.meta)["application_version_name"])
        prior = learner.types.get(m.get(module, m.F_TYPE))
        aliases = [f"{prior['category']}/{name}" for name in prior["titles"] | prior["user_titles"]] if prior else []
        e = learner.entry(module, kind, version)
        e["aliases"] = aliases
        e["category"] = m.get(module, m.F_CATEGORY)
        e["template"] = module
        for param in m.params(module):
            pname = m.get(param, m.F_NAME)
            e["param_objs"][pname] = (version, param)
            e["params"][pname]["kind"] = m.PARAM_KINDS.get(param.cls, f"{param.cls:#x}")
        e["outputs"].update(outputs)
        # Definition names and ranges are authoritative, unlike names customized in presets.
        e["titles"] = collections.Counter({m.get(module, m.F_TITLE): 1})
        e["title_versions"] = {m.get(module, m.F_TITLE): version}
        for name, descriptor in descriptors.items():
            learner.add_descriptor(m.get(module, m.F_TYPE), name, descriptor)
            key = json.dumps(describe_descriptor(descriptor), sort_keys=True)
            e["params"][name]["descriptors"] = collections.Counter({key: 1})
    for name in sorted(GRID_DEVICES):
        definition_path = LIBRARY / "devices" / f"{name}.bwdevice"
        if not definition_path.exists():
            continue
        definition = parse(definition_path.read_bytes())
        meta = {k: v for k, _, v in definition.meta}
        defaults = LIBRARY / "device-settings" / str(meta["device_uuid"]) / "Default.bwpreset"
        if defaults.exists():
            preset = parse(defaults.read_bytes())
            learner.devices[name] = ((True, version_key(meta["application_version_name"]), 0), preset)
            learner.learn_device(m.get(preset.body, m.F_DEVICE), version_key(meta["application_version_name"]))
        dev_type = ("device", name)
        learner.types.setdefault(dev_type, {"params": {}})
        if name in learner.devices:
            device = m.get(learner.devices[name][1].body, m.F_DEVICE)
            if any(o.cls == m.POLY for o in walk(device)):
                pname = "POLY/GLIDE_TIME"
                learner.types[dev_type]["params"].setdefault(
                    pname, {"kind": "number", "values": collections.Counter(), "descriptors": collections.Counter()}
                )
                desc = Obj(m.DESCRIPTOR, [(0x124, 7, 0.0), (0x125, 7, 1.0), (0x37B, 7, 0.0), (0x126, 1, 5), (0x128, 1, 3)])
                learner.add_descriptor(dev_type, pname, desc)
        for atom in m.get(definition.body, 0xAD, []):
            pname, desc = m.get(atom, 0x2BD), m.get(atom, 0x2BE)
            if pname and desc is not None:
                learner.types[dev_type]["params"].setdefault(
                    pname, {"kind": "number", "values": collections.Counter(), "descriptors": collections.Counter()}
                )
                learner.add_descriptor(dev_type, pname, desc)
    templates = {}
    for type_id, e in learner.types.items():
        owner = type_id[1] if isinstance(type_id, tuple) else type_id
        for pname, rec in e["params"].items():
            if rec["descriptors"]:
                key = rec["descriptors"].most_common(1)[0][0]
                templates[(owner, pname)] = rec["descriptor_objs"][key]
        if not isinstance(type_id, tuple):
            t = copy.deepcopy(e["template"])
            union = [m.get(p, m.F_NAME) for p in m.params(t)]
            union += [n for n in e["params"] if n not in union]
            m.set_field(m.get(t, m.F_MODULE_CONTENTS), m.F_PARAMS, [copy.deepcopy(e["param_objs"][n][1]) for n in union])
            m.set_field(t, m.F_TITLE, type_title(e))
            m.set_field(t, m.F_USER_TITLE, "")
            templates[type_id] = t
    data = (finalize(learner), templates, {k: v[1] for k, v in learner.devices.items()})
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(pickle.dumps((fp, data)))
    (CACHE_DIR / "schema.json").write_text(json.dumps(data[0], indent=1, ensure_ascii=False, default=str))
    return data


def refresh():
    CACHE.unlink(missing_ok=True)
    load.cache_clear()
    return load()[0]


if __name__ == "__main__":
    s = refresh()
    print({k: len(v) for k, v in s.items()})
