"""Import existing devices into Grid-owned chains and address their parameters."""

import copy
import io
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import model as m
from .codec import Obj, Preset, parse, walk

SLOT_NAMES = {"fx": "POST_FX", "note_fx": "PRE_FX", "POST_FX": "POST_FX", "PRE_FX": "PRE_FX"}
DEPENDENCIES = ("referenced_device_ids", "referenced_module_ids", "referenced_modulator_ids", "referenced_packaged_file_ids")


class SlotError(ValueError):
    pass


def _slot_name(name):
    name = SLOT_NAMES.get(name, name)
    if not isinstance(name, str) or not name or any(c in name for c in "/:\x00"):
        raise SlotError("slot must be a named device slot")
    return name


def slot_path(slot: str, index: int, parameter: str = "", *, prefix: str = "") -> str:
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise SlotError("device index must be a nonnegative integer")
    if not isinstance(parameter, str) or parameter.startswith("/") or "\x00" in parameter:
        raise SlotError("parameter must be a device-relative path")
    return f"{prefix}CONTENTS/{_slot_name(slot)}/Chain/DEVICE_CHAIN/{index}:{parameter}"


def _slot(device: Obj, name: str) -> Obj:
    if not isinstance(device, Obj) or device.cls != m.DEVICE:
        raise SlotError("destination must contain a device preset object")
    contents = m.get(device, m.F_CONTENTS)
    if not isinstance(contents, Obj):
        raise SlotError("destination device has no contents")
    slots = [p for p in m.get(contents, m.F_PARAMS, []) if p.cls == m.FX_SLOT and m.get(p, m.F_NAME) == _slot_name(name)]
    if len(slots) != 1:
        raise SlotError(f"device has no unique slot {name!r}")
    return slots[0]


def _chain_list(slot: Obj) -> Obj:
    chain = m.get(slot, m.F_CHAIN)
    container = m.get(chain, m.F_CHAIN_DEVICES) if isinstance(chain, Obj) else None
    if not isinstance(container, Obj) or not isinstance(m.get(container, m.F_CHAIN_LIST), list):
        raise SlotError("malformed slot chain")
    return container


def slot_devices(device: Obj, slot: str) -> list[Obj]:
    return list(m.get(_chain_list(_slot(device, slot)), m.F_CHAIN_LIST))


def _targets(device, prefix="", ancestors=None):
    ancestors = set() if ancestors is None else ancestors
    if id(device) in ancestors:
        raise SlotError("cyclic device ownership")
    ancestors = ancestors | {id(device)}

    def contents_targets(contents, base):
        if not isinstance(contents, Obj):
            raise SlotError("missing node contents")
        for param in m.get(contents, m.F_PARAMS, []):
            name = m.get(param, m.F_NAME)
            if not isinstance(name, str) or not name:
                continue
            path = base + name
            if param.cls == m.FX_SLOT:
                for i, child in enumerate(m.get(_chain_list(param), m.F_CHAIN_LIST)):
                    yield from _targets(child, f"{path}/Chain/DEVICE_CHAIN/{i}:", ancestors)
            elif param.cls == m.GRID:
                for module in m.get(param, m.F_LIST, []):
                    yield from contents_targets(m.get(module, m.F_MODULE_CONTENTS), f"{path}/{_node_name(module)}/CONTENTS/")
            elif isinstance(m.get(param, m.F_PARAMS), list):
                yield from contents_targets(param, path + "/")
            else:
                yield path, param

    contents = m.get(device, m.F_CONTENTS)
    if isinstance(contents, Obj):
        yield from contents_targets(contents, prefix + "CONTENTS/")
    modulators = m.get(device, m.F_MODULATORS)
    if isinstance(modulators, Obj):
        for modulator in m.get(modulators, m.F_LIST, []):
            yield from contents_targets(m.get(modulator, m.F_MODULE_CONTENTS), f"{prefix}MODULATORS/{_node_name(modulator)}/CONTENTS/")
    parameter_ids = {id(p) for p in m.get(contents, m.F_PARAMS, [])} if isinstance(contents, Obj) else set()
    for fid, _, obj in device.fields:
        if fid not in (m.F_CONTENTS, m.F_MODULATORS) and isinstance(obj, Obj) and obj.cls in m.VALUE_FIELD:
            name = m.get(obj, m.F_NAME)
            if name and id(obj) not in parameter_ids:
                yield prefix + name, obj


def _node_name(node):
    name = m.get(node, m.F_NAME)
    if not isinstance(name, str) or not name or any(c in name for c in "/:\x00"):
        raise SlotError("module/modulator has no valid stored path name")
    return name


def _target_map(device, prefix=""):
    result = {}
    for path, parameter in _targets(device, prefix):
        if path in result:
            raise SlotError(f"ambiguous parameter path {path}")
        result[path] = parameter
    return result


def slot_targets(device: Obj, slot: str | None = None) -> dict[str, Obj]:
    """Return full host-relative target paths, including nested chains and modulators."""
    names = [_slot_name(slot)] if slot is not None else [m.get(p, m.F_NAME) for p in m.params(device) if p.cls == m.FX_SLOT]
    result = {}
    for name in names:
        for i, child in enumerate(slot_devices(device, name)):
            for path, parameter in _target_map(child, slot_path(name, i)).items():
                if path in result:
                    raise SlotError(f"ambiguous parameter path {path}")
                result[path] = parameter
    return result


def _device_paths(device, prefix="", ancestors=None):
    ancestors = set() if ancestors is None else ancestors
    if id(device) in ancestors:
        raise SlotError("cyclic device ownership")
    yield prefix, device
    contents = m.get(device, m.F_CONTENTS)
    if not isinstance(contents, Obj):
        return
    for param in m.get(contents, m.F_PARAMS, []):
        if param.cls == m.FX_SLOT:
            for i, child in enumerate(m.get(_chain_list(param), m.F_CHAIN_LIST)):
                yield from _device_paths(child, slot_path(m.get(param, m.F_NAME), i, prefix=prefix), ancestors | {id(device)})


def _learn_descriptors(device):
    descriptors = {}
    for prefix, child in reversed(list(_device_paths(device))):
        for obj in m.scoped_walk(child):
            if obj.cls in (m.MOD_ROUTING, m.REMOTE_CONTROL):
                target = m.get(obj, m.F_ROUTE_TARGET if obj.cls == m.MOD_ROUTING else m.F_RC_TARGET)
                desc = m.get(obj, m.F_ROUTE_DESCRIPTOR if obj.cls == m.MOD_ROUTING else m.F_RC_DESCRIPTOR)
                if isinstance(target, str) and isinstance(desc, Obj):
                    descriptors[prefix + target] = copy.deepcopy(desc)
    return descriptors


@dataclass
class Device:
    obj: Obj
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    attachment: bytes = b""
    descriptors: dict[str, Obj] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.obj, Obj) or self.obj.cls != m.DEVICE or not isinstance(m.get(self.obj, m.F_DEVICE_ID), uuid.UUID):
            raise SlotError("source must be a device preset object with a device UUID")
        if any(o.cls == m.PRESET for o in walk(self.obj)):
            raise SlotError("device contains a reference to an outside preset owner")

    @classmethod
    def from_object(cls, device: Obj, *, dependencies=None, descriptors=None):
        return cls(copy.deepcopy(device), copy.deepcopy(dependencies or {}), descriptors=copy.deepcopy(descriptors or {}))

    @classmethod
    def from_preset(cls, preset: Preset):
        device = m.get(preset.body, m.F_DEVICE)
        dependencies = {k: copy.deepcopy(v) for k, _, v in preset.meta if k in DEPENDENCIES}
        descriptors = _learn_descriptors(device) if isinstance(device, Obj) else {}
        return cls(copy.deepcopy(device), dependencies, preset.attachment, descriptors)

    @classmethod
    def from_file(cls, path: str | Path):
        return cls.from_preset(parse(Path(path).read_bytes()))

    @classmethod
    def from_definition(cls, path: str | Path, *, default_settings: Obj | None = None):
        """Instantiate a native definition's defaults, or supplied complete device settings."""
        from .definitions import module_definition
        from .bindings import DEFINITION_KINDS, parameter_from_definition

        definition = parse(Path(path).read_bytes())
        root = copy.deepcopy(definition.body)
        type_id, title = m.get(root, 0x181), m.get(root, 0x182)
        if not isinstance(type_id, uuid.UUID) or not isinstance(title, str):
            raise SlotError("not a native device definition")
        root.fields += [(0x18E1, 0x15, type_id), (0x18E2, 8, title), (0x18E7, 8, m.get(root, 0x186, ""))]
        module, descriptors, _ = module_definition(definition.__class__(definition.header, [], root, 1))
        if default_settings is not None:
            result = cls.from_object(default_settings)
            if m.get(result.obj, m.F_DEVICE_ID) != type_id:
                raise SlotError("default settings belong to a different device")
        else:
            contents = m.get(module, m.F_MODULE_CONTENTS)
            result = cls(
                Obj(
                    m.DEVICE,
                    [
                        (m.F_NAME, 8, ""),
                        (m.F_TITLE, 8, title),
                        (m.F_DEVICE_ID, 0x15, type_id),
                        (m.F_CONTENTS, 9, contents),
                        (m.F_MODULATORS, 9, Obj(m.MODULATOR_LIST, [(m.F_LIST, 0x12, [])])),
                        (m.F_REMOTE_CONTROLS, 9, Obj(m.REMOTE_CONTROLS, [(m.F_REMOTE_PAGES, 0x12, [])])),
                    ],
                )
            )
        result.descriptors = {f"CONTENTS/{name}": copy.deepcopy(d) for name, d in descriptors.items()}
        contents = m.get(result.obj, m.F_CONTENTS)
        if contents is None:
            contents = Obj(m.CONTENTS, [(m.F_NAME, 8, "CONTENTS"), (m.F_PARAMS, 0x12, [])])
            result.obj.fields = [(f, t, v) for f, t, v in result.obj.fields if f != m.F_CONTENTS] + [(m.F_CONTENTS, 9, contents)]
        elif not isinstance(contents, Obj):
            raise SlotError("default settings contain invalid device contents")
        if not any(f == m.F_PARAMS for f, _, _ in contents.fields):
            contents.fields.append((m.F_PARAMS, 0x12, []))
        if not isinstance(m.get(contents, m.F_PARAMS), list):
            raise SlotError("default settings contain an invalid parameter list")
        params = m.params(result.obj)
        by_name = {m.get(p, m.F_NAME): p for p in params}
        for atom in walk(m.get(root, 0xAD, [])):
            if atom.cls in DEFINITION_KINDS:
                selector = parameter_from_definition(atom)
                name = m.get(selector, m.F_NAME)
                if name in by_name:
                    by_name[name].binding_kind = selector.binding_kind
                else:
                    params.append(selector)
                    by_name[name] = selector
        return result


@dataclass
class AttachedDevice:
    obj: Obj
    path: str
    patch: object
    descriptors: dict[str, Obj]
    dependencies: dict[str, list[str]]

    @property
    def kind(self):
        return m.get(self.obj, m.F_TITLE, "Slot device")

    def param_path(self, name):
        if not isinstance(name, str):
            raise SlotError("target name must be a string")
        targets = _target_map(self.obj)
        if name.startswith("./"):
            relative = name[2:]
            if relative not in targets:
                raise SlotError(f"device has no target {name!r}")
            return self.path + relative
        if name in targets and "CONTENTS/" + name in targets:
            raise SlotError(f"ambiguous short target name {name!r}; use a qualified contents path")
        relative = name if name in targets else "CONTENTS/" + name
        if relative not in targets:
            raise SlotError(f"device has no target {name!r}")
        return self.path + relative

    def descriptor(self, name):
        from .build import generic_descriptor

        if self.patch is not None:
            self.descriptors.update(copy.deepcopy(getattr(self.patch, "_slot_descriptors", {}).get(self.path, {})))
        relative = self.param_path(name)[len(self.path) :]
        param = _target_map(self.obj)[relative]
        if param.cls not in (m.P_NUMBER, m.P_INT, m.P_ENUM, m.P_BOOL, m.P_FLAG):
            raise SlotError(f"{relative} is not a controllable parameter")
        if relative in self.descriptors:
            return copy.deepcopy(self.descriptors[relative])
        if param.cls == m.P_NUMBER:
            return generic_descriptor()
        raise SlotError(f"no known descriptor for {relative}; supply a device definition or learned descriptor")

    def __getitem__(self, name):
        from .build import Port

        self.param_path(name)
        return Port(self, name)

    def bind(self, name, value, *, kind=None):
        """Set a named selector inside this imported device without replacing its references."""
        from .bindings import apply_binding

        relative = self.param_path(name)[len(self.path) :]
        parameter = _target_map(self.obj)[relative]
        replacement = apply_binding(parameter, value, kind=kind)
        parameter.fields = replacement.fields
        return self

    def register_target(self, name, descriptor):
        """Register a descriptor for an existing controllable parameter and return its Port."""
        relative = self.param_path(name)[len(self.path) :]
        parameter = _target_map(self.obj)[relative]
        _validate_target_descriptor(parameter, descriptor, relative)
        self.descriptors[relative] = copy.deepcopy(descriptor)
        if self.patch is not None and hasattr(self.patch, "_slot_descriptors"):
            self.patch._slot_descriptors.setdefault(self.path, {})[relative] = copy.deepcopy(descriptor)
        return self[name]


def _validate_target_descriptor(parameter, descriptor, relative):
    if parameter.cls not in (m.P_NUMBER, m.P_INT, m.P_ENUM, m.P_BOOL, m.P_FLAG):
        raise SlotError(f"{relative} is not a controllable parameter")
    if not isinstance(descriptor, Obj) or descriptor.cls not in (m.DESCRIPTOR, m.ENUM_DESCRIPTOR, 0xC6):
        raise SlotError("target registration requires a numeric, enum or boolean parameter descriptor")
    if descriptor.cls == 0xC6 and parameter.cls not in (m.P_BOOL, m.P_FLAG):
        raise SlotError("boolean descriptors require a boolean target")
    if (descriptor.cls == m.ENUM_DESCRIPTOR) != (parameter.cls == m.P_ENUM):
        raise SlotError("enum targets require enum descriptors, which cannot describe other parameter kinds")


def register_slot_targets(destination, slot=None, *, descriptors=None):
    """Expose existing slot-device controls as owned Ports without inserting or replacing devices."""
    preset = destination if isinstance(destination, Preset) else destination.preset
    host = m.get(preset.body, m.F_DEVICE)
    names = [_slot_name(slot)] if slot is not None else [m.get(p, m.F_NAME) for p in m.params(host) if p.cls == m.FX_SLOT]
    learned = _learn_descriptors(host)
    targets = {}
    from .build import Port

    for name in names:
        for i, child in enumerate(slot_devices(host, name)):
            prefix = slot_path(name, i)
            known = {path[len(prefix) :]: descriptor for path, descriptor in learned.items() if path.startswith(prefix)}
            known.update(copy.deepcopy(getattr(destination, "_slot_descriptors", {}).get(prefix, {})))
            attached = AttachedDevice(
                child,
                prefix,
                None if isinstance(destination, Preset) else destination,
                known,
                {},
            )
            for relative, parameter in _target_map(child).items():
                if parameter.cls in (m.P_NUMBER, m.P_INT, m.P_ENUM, m.P_BOOL, m.P_FLAG):
                    targets[attached.path + relative] = Port(attached, relative if "/" in relative else "./" + relative)
    descriptors = {} if descriptors is None else descriptors
    if not isinstance(descriptors, dict) or any(path not in targets for path in descriptors):
        raise SlotError("descriptor registration contains an unknown slot target path")
    for path, descriptor in descriptors.items():
        port = targets[path]
        relative = port.owner.param_path(port.name)[len(port.owner.path) :]
        _validate_target_descriptor(_target_map(port.owner.obj)[relative], descriptor, relative)
    for path, descriptor in descriptors.items():
        port = targets[path]
        port.owner.register_target(port.name, descriptor)
    return targets


def _merge_attachment(first: bytes, second: bytes) -> bytes:
    if not second:
        return first
    try:
        with zipfile.ZipFile(io.BytesIO(second)) as archive:
            incoming = [(info, archive.read(info)) for info in archive.infolist()]
        existing = []
        if first:
            with zipfile.ZipFile(io.BytesIO(first)) as archive:
                existing = [(info, archive.read(info)) for info in archive.infolist()]
        names = {info.filename: data for info, data in existing}
        if any(info.filename in names and names[info.filename] != data for info, data in incoming):
            raise SlotError("conflicting attachment entries")
        if not first:
            return second
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for info, data in existing + [(info, data) for info, data in incoming if info.filename not in names]:
                archive.writestr(info, data)
        return out.getvalue()
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, zlib.error) as error:
        raise SlotError("unsupported attachment; expected a readable ZIP archive") from error


def merge_dependencies(preset: Preset, dependencies: dict[str, list[str]]) -> None:
    """Union dependency metadata after the coordinator finalizes builder metadata."""
    updates = {}
    for key, values in dependencies.items():
        if key not in DEPENDENCIES or not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise SlotError(f"unsupported dependency metadata {key}")
        existing = next((v for k, _, v in preset.meta if k == key), [])
        if not isinstance(existing, list) or not all(isinstance(v, str) for v in existing):
            raise SlotError(f"invalid existing dependency metadata {key}")
        updates[key] = list(dict.fromkeys(existing + values))
    present = {k for k, _, _ in preset.meta}
    preset.meta = [(k, t, updates.get(k, v)) for k, t, v in preset.meta] + [(k, 0x19, v) for k, v in updates.items() if k not in present]


def insert_slot(destination, slot: str, source: Device) -> AttachedDevice:
    """Append a deep-copied device. Existing chain positions and relative paths stay stable."""
    if not isinstance(source, Device):
        raise SlotError("source must be a Device")
    preset = destination if isinstance(destination, Preset) else destination.preset
    host = m.get(preset.body, m.F_DEVICE)
    container = _chain_list(_slot(host, slot))
    devices = m.get(container, m.F_CHAIN_LIST)
    attachment = _merge_attachment(preset.attachment, source.attachment)
    dependencies = copy.deepcopy(source.dependencies)
    for key, cls, fid in (
        ("referenced_device_ids", m.DEVICE, m.F_DEVICE_ID),
        ("referenced_module_ids", m.MODULE, m.F_TYPE),
        ("referenced_modulator_ids", m.MODULATOR, m.F_TYPE),
    ):
        dependencies.setdefault(key, [])
        dependencies[key] += [str(m.get(o, fid)) for o in walk(source.obj) if o.cls == cls and m.get(o, fid) is not None]
    metadata = Preset(preset.header, copy.deepcopy(preset.meta), preset.body, preset.meta_padding, preset.attachment)
    merge_dependencies(metadata, dependencies)
    child = copy.deepcopy(source.obj)
    result = AttachedDevice(
        child,
        slot_path(slot, len(devices)),
        None if isinstance(destination, Preset) else destination,
        copy.deepcopy(source.descriptors),
        dependencies,
    )
    m.set_field(container, m.F_CHAIN_LIST, devices + [child])
    preset.meta, preset.attachment = metadata.meta, attachment
    return result
