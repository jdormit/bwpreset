"""Build Grid presets in code.

    p = Patch("Poly Grid", "Sine Pluck", voices=8)
    osc = p.add("Oscillator/Sine", 0, 0)
    env = p.add("Envelope/ADSR", 3, 0, DECAY=0.4, SUSTAIN=0.0)
    out = p.add("I/O/Audio Out", 7, 0)
    env.connect(IN=osc)
    out.connect(IN=env)
    lfo = p.modulator("LFO/LFO", RATE=0.3)
    lfo.route(env["DECAY"], 0.3)
    p.remote(env["DECAY"], "Decay")
    p.save()

Only parameters you set are written; Bitwig fills in its own defaults for the rest.
"""

import copy
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from . import model as m
from .codec import Obj, Preset, preserve_copy_references, serialize, walk
from .curves import CURVE_PARAM, Curve
from .bindings import SOURCE_PARAMETER, DESTINATION_PARAMETER, apply_binding
from .assets import ASSET_PARAMETER_CLASSES, apply_asset, collect_dependencies, dependency_metadata
from .schema import load
from . import graph, units
from .state import POLY_CHOICES as POLY_CHOICES
from .state import POLY_FIELDS, apply_state

USER_PRESETS = Path.home() / "Documents/Bitwig Studio/Library/Presets"
META_DROP = {"orig_file_checksum", "packaged_file_id", "revision_id", "revision_no"}
MODULATOR_ROWS = 3
KNOBS_PER_PAGE = 8
STEP_EDITORS = {"Data/Steps", "Data/Gates", "Data/Pitches", "Data/Accents", "Data/Probabilities"}


class PatchError(Exception):
    pass


INT_TYPES = {0x01: (-(2**7), 2**7 - 1), 0x02: (-(2**15), 2**15 - 1), 0x03: (-(2**31), 2**31 - 1)}


def int_type(v: int) -> int:
    """Bitwig always writes integers in the smallest of its three widths that fits."""
    return next(t for t, (lo, hi) in INT_TYPES.items() if lo <= v <= hi)


def set_value(obj: Obj, fid: int, value):
    t = field_type(obj, fid)
    if t in INT_TYPES:
        t = int_type(value)
    m.put_field(obj, fid, t, value)


def coerce(value, t: int, what: str):
    """Check value against a field's serialized type and convert it."""
    is_int = lambda v: isinstance(v, int) and not isinstance(v, bool) and INT_TYPES[0x03][0] <= v <= INT_TYPES[0x03][1]  # noqa: E731
    ok = {
        0x01: is_int,
        0x02: is_int,
        0x03: is_int,
        0x05: lambda v: isinstance(v, bool),
        0x07: units.finite,
        0x08: lambda v: isinstance(v, str),
        0x17: lambda v: isinstance(v, (list, tuple)) and all(units.finite(x) and abs(x) <= 3.4028234663852886e38 for x in v),
    }.get(t)
    if ok is None or not ok(value):
        raise PatchError(f"{what} can't be set to {value!r}")
    return float(value) if t == 0x07 else [float(x) for x in value] if t == 0x17 else value


def field_type(obj: Obj, fid: int) -> int:
    found = next((t for f, t, _ in obj.fields if f == fid), None)
    if found is not None:
        return found
    if fid == m.VALUE_FIELD.get(obj.cls):
        return {m.P_NUMBER: 7, m.P_INT: 3, m.P_ENUM: 3, m.P_BOOL: 5, m.P_FLAG: 5, m.P_TEXT: 8, m.P_DATA: 0x17}[obj.cls]
    raise PatchError(f"unknown field type {fid:#x} in {obj.cls:#x}")


@dataclass(frozen=True)
class Port:
    """A named parameter or port on a module, modulator or the device itself."""

    owner: "Node | Patch"
    name: str

    @property
    def path(self) -> str:
        return self.owner.param_path(self.name)

    def descriptor(self) -> Obj:
        return self.owner.descriptor(self.name)


class Node:
    """A module or modulator: an object with a type UUID and a parameter list."""

    path_prefix = ""
    section = ""

    def __init__(self, patch: "Patch", index: int, kind: str, template: Obj, title: str | None):
        self.patch = patch
        self._deleted = False
        self.index = index
        self.kind = kind
        self.obj = copy.deepcopy(template)
        self.type_id: uuid.UUID = m.get(self.obj, m.F_TYPE)
        self.schema = patch.lookup(kind, self.section)
        self.available = {m.get(p, m.F_NAME): p for p in m.params(self.obj)}
        self._binding_kinds = {
            name: self.schema["params"].get(name, {}).get("selector_kind", getattr(p, "binding_kind", None))
            for name, p in self.available.items()
        }
        self.order = list(self.available)
        self.used: dict[str, Obj] = {}
        self._stored_descriptors = {}
        m.set_field(self.obj, m.F_NAME, str(index))
        m.set_field(self.obj, m.F_PRESET_NAME, "")
        if title is not None:
            title = coerce(title, 0x08, "title")
            m.set_field(self.obj, m.F_USER_TITLE, title)
            m.set_field(self.obj, m.F_TITLE, title)
        self._sync()

    def __getitem__(self, name: str) -> Port:
        self._template_param(name)
        return Port(self, name)

    def param_path(self, name: str) -> str:
        self.patch._check_node(self)
        return f"{self.path_prefix}{self.index}/CONTENTS/{name}"

    def descriptor(self, name: str) -> Obj:
        d = self._value_descriptor(name, {})
        if d is None:
            d = self._stored_descriptors.get(name)
        if d is not None:
            return copy.deepcopy(d)
        if name in self.available and self.available[name].cls == m.P_NUMBER:
            return generic_descriptor()
        raise PatchError(f"no known descriptor for {self.kind}.{name}; it can't be modulated or remote-controlled yet")

    def _value_descriptor(self, name, values):
        descriptor = load()[1].get((self.type_id, name)) or self._stored_descriptors.get(name)
        flag = "BIPOLAR" if name == "VALUE" else f"B{name[1:]}" if name.startswith("V") and name[1:].isdigit() else None
        if flag in self.available and values.get(flag, m.param_value(self.used[flag]) if flag in self.used else False) is True:
            if descriptor is not None and m.get(descriptor, 0x124) == 0 and m.get(descriptor, 0x125) == 1:
                descriptor = copy.deepcopy(descriptor)
                m.set_field(descriptor, 0x124, -1.0)
        return descriptor

    def _template_param(self, name: str, classes: set[int] | None = None, expected: str = "") -> Obj:
        if self._deleted:
            raise PatchError("node has been deleted")
        if name in getattr(self, "_ambiguous_params", set()):
            raise PatchError(f"{self.kind} has duplicate parameter {name!r}; the target is ambiguous")
        p = self.available.get(name)
        if p is None:
            raise PatchError(f"{self.kind} has no parameter {name!r}; it has {self.order}")
        if classes is not None and p.cls not in classes:
            raise PatchError(f"{self.kind}.{name} is a {m.PARAM_KINDS.get(p.cls, hex(p.cls))}, not {expected}")
        return self.used.get(name, p)

    def _param(self, name: str) -> Obj:
        """The written copy of a parameter, added on first use. Validate before calling."""
        if name not in self.used:
            p = copy.deepcopy(self.available[name])
            if p.cls == m.P_INPUT:
                m.put_field(p, m.F_SOURCE, 8, "")
            if p.cls == m.MOD_SOURCE:
                m.put_field(p, m.F_ROUTINGS, 0x12, [])
            self.used[name] = p
            self._sync()
        return self.used[name]

    def _sync(self):
        contents = m.get(self.obj, m.F_MODULE_CONTENTS)
        if hasattr(self, "_original_params"):
            original = self._original_primary
            parameters = [
                self.used.get(m.get(p, m.F_NAME), p) if original.get(m.get(p, m.F_NAME)) is p else p for p in self._original_params
            ]
            parameters += [self.used[n] for n in self.order if n in self.used and n not in original]
        else:
            parameters = [self.used[n] for n in self.order if n in self.used]
        m.set_field(contents, m.F_PARAMS, parameters)

    def set(self, **values):
        checked = []
        for name, value in values.items():
            p = self._template_param(
                name,
                set(m.VALUE_FIELD) | {CURVE_PARAM, SOURCE_PARAMETER, DESTINATION_PARAMETER} | ASSET_PARAMETER_CLASSES,
                "a settable value",
            )
            if p.cls in ASSET_PARAMETER_CLASSES:
                try:
                    with preserve_copy_references(
                        graph.copy_boundaries([self.patch.device, *[n.obj for n in self.patch.modules + self.patch.modulators]], p)
                    ):
                        checked.append((name, None, apply_asset(p, value)))
                except (ValueError, TypeError, OSError) as exc:
                    raise PatchError(f"{self.kind}.{name}: {exc}") from exc
                continue
            if p.cls in (SOURCE_PARAMETER, DESTINATION_PARAMETER):
                try:
                    with preserve_copy_references(
                        graph.copy_boundaries([self.patch.device, *[n.obj for n in self.patch.modules + self.patch.modulators]], p)
                    ):
                        checked.append((name, None, apply_binding(p, value, kind=self._binding_kinds.get(name))))
                except ValueError as exc:
                    raise PatchError(f"{self.kind}.{name}: {exc}") from exc
                continue
            if p.cls == CURVE_PARAM:
                if not isinstance(value, Curve):
                    raise PatchError(f"{self.kind}.{name} requires a Curve")
                replacement = value.to_parameter(p)
                m.set_field(replacement, m.F_NAME, name)
                checked.append((name, None, replacement))
                continue
            fid = m.VALUE_FIELD[p.cls]
            descriptor = self._value_descriptor(name, values)
            try:
                if p.cls == m.P_ENUM:
                    value = units.enum_value(value, descriptor)
                timebase = None
                if name == "RATE" and "TIMEBASE" in self.available and isinstance(value, units.UNIT_VALUES):
                    base = self.used.get("TIMEBASE")
                    timebase = values.get("TIMEBASE", m.param_value(base) if base is not None else 0)
                    timebase = units.enum_value(timebase, load()[1].get((self.type_id, "TIMEBASE")))
                    legacy = values.get(
                        "USE_LEGACY_RATE", m.param_value(self.used["USE_LEGACY_RATE"]) if "USE_LEGACY_RATE" in self.used else False
                    )
                    if legacy:
                        raise ValueError("physical rate conversion for USE_LEGACY_RATE is not mapped")
                if isinstance(value, units.UNIT_VALUES):
                    if descriptor is None:
                        raise ValueError("no authoritative descriptor for physical conversion")
                    value = units.to_stored(value, descriptor, timebase=timebase)
                value = coerce(value, field_type(p, fid), f"{self.kind}.{name}")
                if p.cls in (m.P_NUMBER, m.P_INT):
                    units.validate_range(value, descriptor)
                if self.kind in STEP_EDITORS and name == "STEPS" and not 1 <= value <= 64:
                    raise ValueError("STEPS must be in [1, 64]")
                if self.kind in STEP_EDITORS and p.cls == m.P_DATA:
                    if not 1 <= len(value) <= 64:
                        raise ValueError("step DATA must contain 1..64 values (inactive buffer cells may be retained)")
                    if self.kind in ("Data/Steps", "Data/Accents", "Data/Probabilities", "Data/Gates") and any(
                        not -1 <= v <= 1 for v in value
                    ):
                        raise ValueError("stored step values must be in [-1, 1]")
            except (ValueError, OverflowError) as exc:
                raise PatchError(f"{self.kind}.{name}: {exc}") from exc
            checked.append((name, fid, value))
        if (
            isinstance(values.get("RATE"), units.UNIT_VALUES)
            and "TIMEBASE" in self.available
            and "TIMEBASE" not in values
            and "TIMEBASE" not in self.used
        ):
            base = self._template_param("TIMEBASE", {m.P_ENUM}, "an enum timebase")
            checked.append(("TIMEBASE", m.VALUE_FIELD[m.P_ENUM], coerce(0, field_type(base, m.VALUE_FIELD[m.P_ENUM]), "timebase")))
        for name, fid, value in checked:
            if fid is None:
                if name in self.used:
                    self.used[name].fields = value.fields
                else:
                    self.used[name] = value
                self._sync()
            else:
                set_value(self._param(name), fid, value)
        if checked:
            self.patch._dirty = True
        return self

    def state(self, **settings):
        """Set instance presentation/enable state, and modulator per-voice mode."""
        self.patch._check_node(self)
        try:
            apply_state(self.obj, **settings)
        except ValueError as exc:
            raise PatchError(str(exc)) from exc
        if settings:
            self.patch._dirty = True
        return self

    def mod_sources(self) -> list[str]:
        return [n for n in self.order if self.available[n].cls == m.MOD_SOURCE]

    def route(
        self,
        target: Port,
        amount: float,
        source: str | None = None,
        scale: Port | None = None,
        mode: int | str = 0,
        *,
        enabled: bool = True,
        normalized: bool = False,
    ):
        """Modulate target from one of this node's modulation sources.

        amount is in the target parameter's stored units (see `bwgrid info`), e.g. semitones
        for a filter cutoff, so 0.3 there is inaudible while 24 is two octaves.
        scale is another modulation source that scales the amount. mode is stored
        verbatim; its meaning is not yet known.
        """
        sources = self.mod_sources()
        if source is None:
            if len(sources) != 1:
                raise PatchError(f"{self.kind} has modulation sources {sources}; pass source=")
            source = sources[0]
        elif source not in sources:
            raise PatchError(f"{self.kind} has no modulation source {source!r}; it has {sources}")
        self.patch._check_node(self)
        self.patch._check_port(target, "target")
        if scale is not None:
            self.patch._check_port(scale, "scale")
        routing = make_routing(target, amount, scale, mode, enabled=enabled, normalized=normalized)
        old_routes = m.get(self.used.get(source), m.F_ROUTINGS, []) or []
        if not isinstance(old_routes, list):
            raise PatchError("modulation source has malformed routings")
        p = self._param(source)
        m.put_field(p, m.F_ROUTINGS, 0x12, old_routes + [routing])
        self.patch._dirty = True
        return self


def generic_descriptor() -> Obj:
    """Placeholder range for number parameters never seen modulated; Bitwig replaces it with the real one on load."""
    return Obj(
        m.DESCRIPTOR,
        [(0x124, 0x07, 0.0), (0x125, 0x07, 1.0), (0x37B, 0x07, 0.0), (0x126, 0x01, 0), (0x127, 0x01, 0)]
        + [(0xBC6, 0x01, 0), (0x7C4, 0x07, -1.0), (0x128, 0x01, 1), (0x1151, 0x05, True), (0x1152, 0x05, True)],
    )


ROUTING_MODES = {"linear": 0, "abs_linear": 2, "inv_abs_linear": 3, "cubic": 4, "inv_cubic": 5, "rectify_pos": 6, "rectify_neg": 7}


def make_routing(
    target: Port, amount: float, scale: Port | None, mode: int | str, *, enabled: bool = True, normalized: bool = False
) -> Obj:
    descriptor = target.descriptor()
    span = m.get(descriptor, 0x125, 1.0) - m.get(descriptor, 0x124, 0.0)
    amount = coerce(amount, 0x07, "modulation amount")
    coerce(normalized, 0x05, "normalized")
    coerce(enabled, 0x05, "routing enabled")
    if normalized:
        owner = target.owner
        known = not isinstance(owner, Node) or (owner.type_id, target.name) in load()[1] or target.name in owner._stored_descriptors
        if hasattr(owner, "descriptors"):
            relative = owner.param_path(target.name)[len(owner.path) :]
            known = relative in owner.descriptors
        lo, hi = m.get(descriptor, 0x124), m.get(descriptor, 0x125)
        if not known or descriptor.cls != m.DESCRIPTOR or not units.finite(lo) or not units.finite(hi) or hi <= lo or not -1 <= amount <= 1:
            raise PatchError("normalized depth requires a numeric descriptor and depth in [-1, 1]")
        amount *= span
    if isinstance(mode, str):
        if mode not in ROUTING_MODES:
            raise PatchError(f"modulation mode must be one of {list(ROUTING_MODES)}")
        mode = ROUTING_MODES[mode]
    mode = coerce(mode, 0x01, "modulation mode")
    if mode not in ROUTING_MODES.values():
        raise PatchError(f"unknown modulation mode {mode}")
    if amount and span and abs(amount) < 0.02 * span:
        warnings.warn(
            f"modulation amount {amount} is under 2% of {target.name}'s range of {span:g}; amounts are in the parameter's own units",
            stacklevel=3,
        )
    return Obj(
        m.MOD_ROUTING,
        [
            (m.F_NAME, 0x08, ""),
            (m.F_ROUTE_TARGET, 0x08, target.path),
            (m.F_ROUTE_DESCRIPTOR, 0x09, descriptor),
            (m.F_ROUTE_AMOUNT, 0x07, coerce(amount, 0x07, "modulation amount")),
            (m.F_ROUTE_ENABLED, 0x05, enabled),
            (m.F_ROUTE_MODE, int_type(mode), coerce(mode, 0x01, "modulation mode")),
            (m.F_ROUTE_SCALE_SOURCE, 0x08, scale.path if scale else ""),
        ],
    )


class Module(Node):
    path_prefix = "CONTENTS/MODULES/"
    section = "modules"

    def __init__(self, patch, index, kind, template, x: int, y: int, title: str | None):
        super().__init__(patch, index, kind, template, title)
        set_value(self.obj, m.F_X, coerce(x, 0x01, "x"))
        set_value(self.obj, m.F_Y, coerce(y, 0x01, "y"))

    def out(self, port: str = "OUT") -> Port:
        self.patch._check_node(self)
        outputs = self.schema["outputs"]
        if port not in outputs:
            raise PatchError(f"{self.kind} has no known output {port!r}; known outputs are {outputs}")
        return Port(self, port)

    def connect(self, **ports: "Port | Module | None"):
        checked = []
        for name, src in ports.items():
            self._template_param(name, {m.P_INPUT}, f"an input; inputs are {self.schema['inputs']}")
            if isinstance(src, Module):
                src = src.out()
            if src is not None and not (isinstance(src, Port) and isinstance(src.owner, Module)):
                raise PatchError("cables can only come from module outputs")
            if src is not None:
                self.patch._check_port(src, "output")
            checked.append((name, src.path if src else ""))
        for name, path in checked:
            m.put_field(self._param(name), m.F_SOURCE, 8, path)
        if checked:
            self.patch._dirty = True
        return self


class Modulator(Node):
    path_prefix = "MODULATORS/"
    section = "modulators"

    def __init__(self, patch, index, kind, template, title: str | None, x: int | None, y: int | None):
        super().__init__(patch, index, kind, template, title)
        set_value(self.obj, m.F_X, index // MODULATOR_ROWS if x is None else coerce(x, 0x01, "x"))
        set_value(self.obj, m.F_Y, index % MODULATOR_ROWS if y is None else coerce(y, 0x01, "y"))


class Patch:
    def __init__(self, device: str, name: str, **settings):
        schema, _, skeletons = load()
        if device not in skeletons:
            raise PatchError(f"unknown device {device!r}; known: {list(skeletons)}")
        self.schema = schema
        self.device_name = device
        self.name = name
        self.preset: Preset = copy.deepcopy(skeletons[device])
        self.device = m.get(self.preset.body, m.F_DEVICE)
        m.set_field(self.device, m.F_PRESET_NAME, coerce(name, 0x08, "name"))
        m.set_field(self.preset.body, m.F_SELECTION, [])
        for slot in [o for o in walk(self.device) if o.cls == m.FX_SLOT]:
            m.set_field(m.get(m.get(slot, m.F_CHAIN), m.F_CHAIN_DEVICES), m.F_CHAIN_LIST, [])
        self.grid = next(o for o in walk(self.device) if o.cls == m.GRID)
        self.poly = next((o for o in walk(self.device) if o.cls == m.POLY), None)
        if self.poly is not None:
            m.set_field(m.get(self.poly, m.F_POLY_SPREAD), m.F_ROUTINGS, [])
        self.device_params = {m.get(p, m.F_NAME): p for p in m.params(self.device)}
        self.modules: list[Module] = []
        self.modulators: list[Modulator] = []
        self.remote_pages: list[tuple[str, dict[int, tuple[Port, str]]]] = []
        self._imported = False
        self._original_remote_pages = []
        self._slot_dependencies = []
        self._structure_changed = False
        self._dirty = False
        self._stored_descriptors = {}
        self._slot_descriptors = {}
        self.configure(**settings)

    @classmethod
    def from_preset(cls, preset: Preset | bytes | str | Path) -> "Patch":
        """Edit a private copy, retaining unknown fields, nested devices and attachments."""
        from .codec import parse

        if isinstance(preset, (str, Path)):
            preset = Path(preset).read_bytes()
        if isinstance(preset, bytes):
            preset = parse(preset)
        if not isinstance(preset, Preset):
            raise PatchError("from_preset requires a Preset, bytes or path")
        self = cls.__new__(cls)
        self.schema = load()[0]
        self.preset = copy.deepcopy(preset)
        self.device_name = dict((k, v) for k, _, v in preset.meta).get("device_name", "")
        self.device = m.get(self.preset.body, m.F_DEVICE)
        if self.device is None:
            raise PatchError("preset has no root device")
        own = list(m.scoped_walk(self.device))
        self.grid = next((o for o in own if o.cls == m.GRID), None)
        if self.grid is None:
            raise PatchError("preset has no root Grid")
        self.name = m.get(self.device, m.F_PRESET_NAME) or "Untitled"
        self.poly = next((o for o in own if o.cls == m.POLY), None)
        self.device_params = {m.get(p, m.F_NAME): p for p in m.params(self.device)}
        self.modules = [self._adopt(o, "modules") for o in m.get(self.grid, m.F_LIST, []) or []]
        self.modulators = [self._adopt(o, "modulators") for o in m.get(m.get(self.device, m.F_MODULATORS), m.F_LIST, []) or []]
        self.remote_pages = []
        self._original_remote_pages = m.get(m.get(self.device, m.F_REMOTE_CONTROLS), m.F_REMOTE_PAGES, []) or []
        self._imported = True
        self._slot_dependencies = []
        self._structure_changed = False
        self._dirty = False
        self._stored_descriptors = {}
        self._slot_descriptors = {}
        nodes = {graph.node_prefix(n): n for n in self.modules + self.modulators}
        for obj in own:
            if obj.cls not in (m.MOD_ROUTING, m.REMOTE_CONTROL):
                continue
            path = m.get(obj, m.F_ROUTE_TARGET if obj.cls == m.MOD_ROUTING else m.F_RC_TARGET)
            descriptor = m.get(obj, m.F_ROUTE_DESCRIPTOR if obj.cls == m.MOD_ROUTING else m.F_RC_DESCRIPTOR)
            if not isinstance(path, str) or not isinstance(descriptor, Obj):
                continue
            match = graph.NODE_PATH.match(path)
            if match:
                owner = nodes.get(f"{match[1]}{match[2]}/CONTENTS/")
                if owner is not None:
                    owner._stored_descriptors[match[3]] = copy.deepcopy(descriptor)
            elif path.startswith("CONTENTS/"):
                self._stored_descriptors[path[len("CONTENTS/") :]] = copy.deepcopy(descriptor)
        self._original_bytes = serialize(self.preset)
        return self

    def _adopt(self, obj, section):
        node = (Module if section == "modules" else Modulator).__new__(Module if section == "modules" else Modulator)
        node.patch, node.obj, node._deleted = self, obj, False
        name = m.get(obj, m.F_NAME)
        if not isinstance(name, str) or not name:
            raise PatchError(f"{section[:-1]} has no valid stored name")
        node.index = int(name) if name.isdecimal() and str(int(name)) == name else name
        node.type_id = m.get(obj, m.F_TYPE)
        node.kind = next((k for k, e in self.schema[section].items() if e["id"] == str(node.type_id)), str(node.type_id))
        node.schema = self.schema[section].get(node.kind, {"outputs": [], "inputs": [], "params": {}})
        template = load()[1].get(node.type_id)
        node.available = {m.get(p, m.F_NAME): copy.deepcopy(p) for p in m.params(template)} if template is not None else {}
        node._binding_kinds = {
            name: node.schema["params"].get(name, {}).get("selector_kind", getattr(p, "binding_kind", None))
            for name, p in node.available.items()
        }
        node.used = {m.get(p, m.F_NAME): p for p in m.params(obj)}
        node._original_params = tuple(m.params(obj))
        node._original_primary = dict(node.used)
        names = [m.get(p, m.F_NAME) for p in node._original_params]
        node._ambiguous_params = {n for n in names if names.count(n) > 1}
        node._stored_descriptors = {}
        node.order = list(node.used) + [n for n in node.available if n not in node.used]
        node.available.update(node.used)
        return node

    def _check_node(self, node):
        if not isinstance(node, Node) or node.patch is not self or node._deleted or node not in self.modules + self.modulators:
            raise PatchError("reference belongs to another patch or a deleted node")

    def _check_port(self, port, role):
        if not isinstance(port, Port):
            raise PatchError(f"{role} must be a Port")
        owner = port.owner
        if isinstance(owner, Node):
            self._check_node(owner)
        elif owner is not self:
            from .slots import AttachedDevice

            if not isinstance(owner, AttachedDevice) or owner.patch is not self or not any(o is owner.obj for o in walk(self.device)):
                raise PatchError("reference belongs to another patch")
            if role != "target":
                raise PatchError("slot device parameters can only be modulation/remote targets")
            owner.param_path(port.name)
            port.descriptor()
            return
        if role == "output":
            if not isinstance(owner, Module) or port.name not in owner.schema["outputs"]:
                raise PatchError("cables require a known signal output")
        elif role == "scale":
            if not isinstance(owner, Node) or owner._template_param(port.name).cls != m.MOD_SOURCE:
                raise PatchError("scale must be a modulation source")
        else:
            if isinstance(owner, Node):
                owner._template_param(port.name, {m.P_NUMBER, m.P_INT, m.P_ENUM, m.P_BOOL, m.P_FLAG}, "a controllable parameter")
            else:
                self[port.name]
                if port.name != "POLY/GLIDE_TIME" and self.device_params[port.name].cls not in (
                    m.P_NUMBER,
                    m.P_INT,
                    m.P_ENUM,
                    m.P_BOOL,
                    m.P_FLAG,
                ):
                    raise PatchError("target must be a controllable parameter")
            port.descriptor()

    def _next_index(self, section):
        used = {str(n.index) for n in getattr(self, section)}
        return max((int(i) for i in used if i.isdecimal()), default=-1) + 1

    def slot(self, name, source):
        """Attach an existing slots.Device to a Grid-owned device chain."""
        from .slots import insert_slot

        try:
            attached = insert_slot(self, name, source)
        except ValueError as exc:
            raise PatchError(str(exc)) from exc
        self._slot_dependencies.append(attached.dependencies)
        self._slot_descriptors[attached.path] = copy.deepcopy(attached.descriptors)
        self._dirty = True
        return attached

    def slot_device(self, name, index=0):
        """Address an existing Grid-owned slot device, including imported presets."""
        from .slots import AttachedDevice, slot_devices, slot_path

        index = coerce(index, 0x03, "slot device index")
        try:
            children = slot_devices(self.device, name)
            if not 0 <= index < len(children):
                raise ValueError("slot device index is out of range")
            path = slot_path(name, index)
        except ValueError as exc:
            raise PatchError(str(exc)) from exc
        child = children[index]
        descriptors = copy.deepcopy(self._slot_descriptors.get(path, {}))
        for root, prefix in ((child, ""), (self.device, path)):
            for obj in m.scoped_walk(root):
                if obj.cls not in (m.MOD_ROUTING, m.REMOTE_CONTROL):
                    continue
                target = m.get(obj, m.F_ROUTE_TARGET if obj.cls == m.MOD_ROUTING else m.F_RC_TARGET)
                descriptor = m.get(obj, m.F_ROUTE_DESCRIPTOR if obj.cls == m.MOD_ROUTING else m.F_RC_DESCRIPTOR)
                if isinstance(target, str) and target.startswith(prefix) and isinstance(descriptor, Obj):
                    descriptors[target[len(prefix) :]] = copy.deepcopy(descriptor)
        return AttachedDevice(child, path, self, descriptors, {})

    def dependencies(self):
        """Media dependencies, including assets in Grid-owned slot devices."""
        return collect_dependencies(self.to_preset().body)

    def fragment(self, *nodes):
        """Capture modules/modulators; external references require explicit import bindings."""
        if len(set(nodes)) != len(nodes):
            raise PatchError("fragment contains duplicate nodes")
        for node in nodes:
            self._check_node(node)
        return graph.Fragment.capture(nodes)

    def import_fragment(self, fragment: graph.Fragment, *, x=0, y=0, bindings=None):
        """Return an original-node → imported-node map. Offsets apply to module positions."""
        if not isinstance(fragment, graph.Fragment):
            raise PatchError("import_fragment requires a Fragment")
        if fragment.attachment and self.preset.attachment and fragment.attachment != self.preset.attachment:
            raise PatchError("fragment attachment differs from the destination; merge its resources before import")
        x, y = coerce(x, 0x03, "x offset"), coerce(y, 0x03, "y offset")
        bindings = bindings or {}
        objects = copy.deepcopy(fragment.objects)
        result, mapping, counts = {}, {}, {"modules": self._next_index("modules"), "modulators": self._next_index("modulators")}
        for old, obj in zip(fragment.nodes, objects, strict=True):
            old_prefix = f"{old.path_prefix}{m.get(obj, m.F_NAME)}/CONTENTS/"
            node = self._adopt(obj, old.section)
            node.index = counts[old.section]
            counts[old.section] += 1
            m.set_field(obj, m.F_NAME, str(node.index))
            if isinstance(node, Module):
                set_value(obj, m.F_X, coerce(m.get(obj, m.F_X) + x, 0x03, "x"))
                set_value(obj, m.F_Y, coerce(m.get(obj, m.F_Y) + y, 0x03, "y"))
            mapping[old_prefix] = graph.node_prefix(node)
            result[old] = node
        for obj in objects:
            for ref, fid, path in graph.references(obj):
                rewritten = graph.rewrite_path(path, mapping)
                if rewritten == path and not any(path.startswith(prefix) for prefix in mapping):
                    if path not in bindings:
                        raise PatchError(f"fragment has external reference {path!r}; supply bindings")
                    port = bindings[path]
                    role = "output" if ref.cls == m.P_INPUT else "scale" if fid == m.F_ROUTE_SCALE_SOURCE else "target"
                    self._check_port(port, role)
                    rewritten = port.path
                    if ref.cls == m.MOD_ROUTING and fid == m.F_ROUTE_TARGET:
                        m.set_field(ref, m.F_ROUTE_DESCRIPTOR, port.descriptor())
                m.set_field(ref, fid, rewritten)
            for parameter, binding in graph.device_bindings(obj):
                from dataclasses import replace
                from .bindings import BINDING_CLASSES

                path = binding.path
                rewritten = graph.rewrite_path(path, mapping)
                if rewritten == path and not any(path.startswith(prefix) for prefix in mapping):
                    value = bindings.get(path)
                    if not isinstance(value, BINDING_CLASSES):
                        raise PatchError(f"fragment has external device selector {path!r}; supply a named binding")
                else:
                    value = replace(binding, path=rewritten)
                replacement = apply_binding(parameter, value)
                parameter.fields = replacement.fields
        for node in result.values():
            getattr(self, node.section).append(node)
        if fragment.attachment:
            self.preset.attachment = fragment.attachment
        if fragment.dependencies:
            self._slot_dependencies.append({key: list(values) for key, values in fragment.dependencies})
        self._structure_changed = True
        self._dirty = True
        return result

    def reindex(self):
        mapping = {}
        for section in (self.modules, self.modulators):
            for index, node in enumerate(section):
                mapping[graph.node_prefix(node)] = f"{node.path_prefix}{index}/CONTENTS/"
        self._rewrite_references(mapping)
        if any(old != new for old, new in mapping.items()):
            self._dirty = True
        for section in (self.modules, self.modulators):
            for index, node in enumerate(section):
                node.index = index
                m.set_field(node.obj, m.F_NAME, str(index))
        return self

    def _rewrite_references(self, mapping):
        self._sync_nodes()
        graph.rewrite(self.device, mapping)

    def _sync_nodes(self):
        if self.modules or m.get(self.grid, m.F_LIST) is not None:
            m.put_field(self.grid, m.F_LIST, 0x12, [n.obj for n in self.modules])
        container = m.get(self.device, m.F_MODULATORS)
        if container is None and self.modulators:
            container = Obj(m.MODULATOR_LIST, [(m.F_NAME, 8, "MODULATORS"), (m.F_LIST, 0x12, [])])
            m.put_field(self.device, m.F_MODULATORS, 9, container)
        if container is not None and (self.modulators or m.get(container, m.F_LIST) is not None):
            m.put_field(container, m.F_LIST, 0x12, [n.obj for n in self.modulators])

    def delete(self, node):
        """Disconnect cables, drop affected routings/remotes, then reindex surviving nodes."""
        self._check_node(node)
        self._rewrite_references({graph.node_prefix(node): None})
        getattr(self, node.section).remove(node)
        node._deleted = True
        self._structure_changed = True
        self._dirty = True
        self._sync_nodes()
        removed_ids = {id(o) for o in walk(node.obj)} - {id(o) for o in walk(self.device)}
        selection = m.get(self.preset.body, m.F_SELECTION)
        if isinstance(selection, list):
            selection[:] = [o for o in selection if id(o) not in removed_ids]
        for _, controls in self.remote_pages:
            for slot, (port, _) in list(controls.items()):
                if port.owner is node:
                    del controls[slot]
        return self.reindex()

    def lookup(self, kind: str, section: str) -> dict:
        if kind not in self.schema[section]:
            kind = self.schema.get("aliases", {}).get(section, {}).get(kind, kind)
        if kind not in self.schema[section]:
            raise PatchError(f"unknown {section[:-1]} {kind!r}")
        return self.schema[section][kind]

    def template(self, kind: str, section: str) -> Obj:
        return load()[1][uuid.UUID(self.lookup(kind, section)["id"])]

    def param_path(self, name: str) -> str:
        return f"CONTENTS/{name}"

    def descriptor(self, name: str) -> Obj:
        d = load()[1].get((self.device_name, name))
        if d is None:
            d = self._stored_descriptors.get(name)
        if d is None:
            raise PatchError(f"no known descriptor for device parameter {name}")
        return copy.deepcopy(d)

    def __getitem__(self, name: str) -> Port:
        if name == "POLY/GLIDE_TIME" and self.poly is not None:
            return Port(self, name)
        if name not in self.device_params:
            raise PatchError(f"{self.device_name} has no parameter {name!r}; it has {list(self.device_params)}")
        return Port(self, name)

    def configure(self, glide: float | None = None, **settings):
        """Set device inspector settings (voices, glide, retrigger, ...) and device parameters.

        glide is stored as the cube root of seconds: 0.2 displays as 8 ms.
        """
        changes = []
        poly_settings = {k: v for k, v in settings.items() if k in POLY_FIELDS or k == "alternate_mono_voices"}
        if poly_settings:
            try:
                apply_state(copy.deepcopy(self._poly("polyphonic state")), **poly_settings)
            except ValueError as exc:
                raise PatchError(str(exc)) from exc
        if glide is not None:
            poly = self._poly("glide")
            glide_parameter = m.get(poly, m.F_POLY_GLIDE)
            if glide_parameter is not None and (not isinstance(glide_parameter, Obj) or glide_parameter.cls != m.P_NUMBER):
                raise PatchError("this preset has an unknown glide representation")
            if isinstance(glide, units.Seconds):
                try:
                    glide = units.to_stored(glide, self.descriptor("POLY/GLIDE_TIME"))
                except ValueError as exc:
                    raise PatchError(str(exc)) from exc
            descriptor = load()[1].get((self.device_name, "POLY/GLIDE_TIME"))
            if not units.finite(glide):
                raise PatchError("glide must be finite")
            try:
                if descriptor is not None:
                    units.validate_range(glide, descriptor)
                elif not 0 <= glide <= 1:
                    raise ValueError("glide must be in stored range [0, 1]")
            except ValueError as exc:
                raise PatchError(str(exc)) from exc
            changes.append((poly, None, coerce(glide, 0x07, "glide")))
        for key, value in settings.items():
            if key in poly_settings:
                continue
            elif key in self.device_params and self.device_params[key].cls in m.VALUE_FIELD:
                p = self.device_params[key]
                fid = m.VALUE_FIELD[p.cls]
                descriptor = load()[1].get((self.device_name, key))
                try:
                    if p.cls == m.P_ENUM:
                        value = units.enum_value(value, descriptor)
                    if isinstance(value, units.UNIT_VALUES):
                        if descriptor is None:
                            raise ValueError("no authoritative descriptor for physical conversion")
                        value = units.to_stored(value, descriptor)
                    value = coerce(value, field_type(p, fid), key)
                    if p.cls in (m.P_NUMBER, m.P_INT):
                        units.validate_range(value, descriptor)
                except (ValueError, OverflowError) as exc:
                    raise PatchError(f"{key}: {exc}") from exc
                changes.append((p, fid, value))
            else:
                raise PatchError(f"unknown setting {key!r}; settings are {['glide', *POLY_FIELDS, *self.device_params]}")
        for obj, fid, value in changes:
            if fid is None:
                parameter = m.get(obj, m.F_POLY_GLIDE)
                if parameter is None:
                    parameter = Obj(m.P_NUMBER, [(m.F_NAME, 8, "GLIDE_TIME")])
                    m.put_field(obj, m.F_POLY_GLIDE, 9, parameter)
                m.put_field(parameter, m.VALUE_FIELD[m.P_NUMBER], 7, value)
                continue
            set_value(obj, fid, value)
        if poly_settings:
            apply_state(self.poly, **poly_settings)
        if changes or poly_settings:
            self._dirty = True
        return self

    def _poly(self, setting: str) -> Obj:
        if self.poly is None:
            raise PatchError(f"{self.device_name} has no {setting} setting")
        return self.poly

    def add(self, kind: str, x: int, y: int, title: str | None = None, **values) -> Module:
        mod = Module(self, self._next_index("modules"), kind, self.template(kind, "modules"), x, y, title)
        mod.set(**values)
        self.modules.append(mod)
        self._structure_changed = True
        self._dirty = True
        return mod

    def modulator(self, kind: str, title: str | None = None, x: int | None = None, y: int | None = None, **values) -> Modulator:
        mod = Modulator(self, self._next_index("modulators"), kind, self.template(kind, "modulators"), title, x, y)
        mod.set(**values)
        self.modulators.append(mod)
        self._structure_changed = True
        self._dirty = True
        return mod

    def route(
        self, target: Port, amount: float, scale: Port | None = None, mode: int | str = 0, *, enabled: bool = True, normalized: bool = False
    ):
        """Modulate target from the per-voice stack spread (Voice Stack Spread in the inspector)."""
        if self.poly is None:
            raise PatchError(f"{self.device_name} has no voice stack spread")
        self._check_port(target, "target")
        if scale is not None:
            self._check_port(scale, "scale")
        routing = make_routing(target, amount, scale, mode, enabled=enabled, normalized=normalized)
        spread = m.get(self.poly, m.F_POLY_SPREAD)
        old_routes = m.get(spread, m.F_ROUTINGS, []) or []
        if not isinstance(old_routes, list):
            raise PatchError("voice stack spread has malformed routings")
        if spread is None:
            spread = Obj(m.MOD_SOURCE, [(m.F_NAME, 8, "STACK_SPREAD")])
            m.put_field(self.poly, m.F_POLY_SPREAD, 9, spread)
        m.put_field(spread, m.F_ROUTINGS, 0x12, old_routes + [routing])
        self._dirty = True
        return self

    def page(self, name: str = ""):
        """Start a new remote control page."""
        self.remote_pages.append((coerce(name, 0x08, "page name"), {}))
        self._dirty = True
        return self

    def remote(self, target: Port, label: str = "", slot: int | None = None):
        """Assign target to a remote control knob: the given slot (0-7) or the next free one."""
        self._check_port(target, "target")
        coerce(label, 0x08, "remote label")
        if slot is not None and (isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < KNOBS_PER_PAGE):
            raise PatchError(f"remote control slot {slot} is out of range")
        if not self.remote_pages or (slot is None and len(self.remote_pages[-1][1]) == KNOBS_PER_PAGE):
            self.page(self.remote_pages[-1][0] if self.remote_pages else "")
        controls = self.remote_pages[-1][1]
        if slot is None:
            slot = next(i for i in range(KNOBS_PER_PAGE) if i not in controls)
        if not 0 <= slot < KNOBS_PER_PAGE or slot in controls:
            raise PatchError(f"remote control slot {slot} is out of range or already used")
        controls[slot] = (target, label)
        self._dirty = True
        return self

    def _remote_controls(self) -> list[Obj]:
        pages = []
        for name, controls in self.remote_pages:
            items = [
                Obj(
                    m.REMOTE_CONTROL,
                    [
                        (m.F_RC_NAME, 0x08, label),
                        (m.F_RC_TARGET, 0x08, target.path),
                        (m.F_RC_DESCRIPTOR, 0x09, target.descriptor()),
                        (m.F_RC_INDEX, 0x01, slot),
                    ],
                )
                for slot, (target, label) in sorted(controls.items())
            ]
            pages.append(Obj(m.REMOTE_PAGE, [(m.F_PAGE_NAME, 0x08, name), (m.F_PAGE_EXTRA, 0x08, ""), (m.F_PAGE_CONTROLS, 0x12, items)]))
        return pages

    def to_preset(self) -> Preset:
        self._sync_nodes()
        controls = m.get(self.device, m.F_REMOTE_CONTROLS)
        pages = self._original_remote_pages + self._remote_controls()
        if controls is None and pages:
            controls = Obj(m.REMOTE_CONTROLS, [(m.F_NAME, 8, "REMOTE_CONTROLS")])
            m.put_field(self.device, m.F_REMOTE_CONTROLS, 9, controls)
        if controls is not None and (pages or m.get(controls, m.F_REMOTE_PAGES) is not None):
            m.put_field(controls, m.F_REMOTE_PAGES, 0x12, pages)
        if self._imported:
            if not self._dirty and serialize(self.preset) != self._original_bytes:
                self._dirty = True
            if self._dirty:
                self.preset.meta = [(k, t, v) for k, t, v in self.preset.meta if k not in META_DROP]
            self._merge_dependencies()
            return self.preset
        meta = []
        for key, t, v in self.preset.meta:
            if key in META_DROP:
                continue
            v = {
                "referenced_module_ids": unique_types(self.modules),
                "referenced_modulator_ids": unique_types(self.modulators),
                "referenced_packaged_file_ids": [],
                "referenced_device_ids": [str(m.get(self.device, m.F_DEVICE_ID))],
                "comment": "",
                "tags": "",
                "preset_category": "",
                "creator": m.GENERATED_CREATOR,
            }.get(key, v)
            meta.append((key, t, v))
        self.preset.meta = meta
        self._merge_dependencies()
        return self.preset

    def _merge_dependencies(self):
        from .slots import merge_dependencies

        for dependencies in self._slot_dependencies:
            merge_dependencies(self.preset, dependencies)
        if not self._imported or self._dirty:
            merge_dependencies(self.preset, {key: values for key, _, values in dependency_metadata(self.preset.body)})
        if self._imported and self._structure_changed:
            updates = {
                key: list(dict.fromkeys(str(m.get(o, m.F_TYPE)) for o in walk(self.device) if o.cls == cls))
                for key, cls in (("referenced_module_ids", m.MODULE), ("referenced_modulator_ids", m.MODULATOR))
            }
            self.preset.meta = [(key, t, updates.get(key, value)) for key, t, value in self.preset.meta]
            merge_dependencies(self.preset, updates)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else USER_PRESETS / "bwpreset" / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(serialize(self.to_preset()))
        return path

    @property
    def filename(self):
        name = self.name.replace("/", "_").replace("\\", "_").replace("\x00", "_") or "Untitled"
        return f"{name}.bwpreset"


def unique_types(nodes: list[Node]) -> list[str]:
    return list(dict.fromkeys(str(n.type_id) for n in nodes))
