"""Reference rewrites for editable Grid graphs."""

import copy
import re
from dataclasses import dataclass

from . import model as m
from .codec import Obj, Tagged, walk

NODE_PATH = re.compile(r"^(CONTENTS/MODULES/|MODULATORS/)([^/]+)/CONTENTS/(.+)$")
REFERENCE_FIELDS = {m.P_INPUT: (m.F_SOURCE,), m.MOD_ROUTING: (m.F_ROUTE_TARGET, m.F_ROUTE_SCALE_SOURCE), m.REMOTE_CONTROL: (m.F_RC_TARGET,)}


def node_prefix(node):
    return f"{node.path_prefix}{node.index}/CONTENTS/"


def references(root):
    for obj in objects(root):
        for fid in REFERENCE_FIELDS.get(obj.cls, ()):
            path = m.get(obj, fid)
            if isinstance(path, str) and path:
                yield obj, fid, path


def objects(root):
    return m.scoped_walk(root) if root.cls == m.DEVICE else walk(root)


def device_bindings(root):
    from .bindings import SOURCE_PARAMETER, BindingError, DeviceSource, decode_binding

    for parameter in objects(root):
        if parameter.cls == SOURCE_PARAMETER:
            try:
                value = decode_binding(parameter)
            except BindingError:
                continue
            if isinstance(value, DeviceSource):
                yield parameter, value


def copy_boundaries(roots, parameter):
    """Objects outside parameter ownership stay linked during transactional copies."""
    parameters = set()
    for obj in walk(roots):
        if obj.cls in (m.DEVICE, m.MODULE, m.MODULATOR):
            parameters.update(m.params(obj) or [])
    seen, result = set(), []

    def visit(value):
        if isinstance(value, Obj):
            if value in seen:
                return
            seen.add(value)
            if value is not parameter:
                result.append(value)
            if value in parameters:
                return
            for _, _, child in value.fields:
                visit(child)
        elif isinstance(value, Tagged):
            visit(value.value)
        elif isinstance(value, (list, dict)):
            for child in value.values() if isinstance(value, dict) else value:
                visit(child)

    visit(roots)
    return result


def rewrite_path(path, mapping):
    match = NODE_PATH.match(path)
    if match:
        prefix = f"{match[1]}{match[2]}/CONTENTS/"
        if prefix in mapping:
            return mapping[prefix] + match[3] if mapping[prefix] is not None else None
    return path


def rewrite(root, mapping):
    from dataclasses import replace
    from .bindings import NoSource, apply_binding

    changes = []
    for parameter, value in device_bindings(root):
        path = rewrite_path(value.path, mapping)
        if path != value.path:
            updated = apply_binding(parameter, NoSource() if path is None else replace(value, path=path))
            changes.append((parameter, updated.fields))
    for parameter, fields in changes:
        parameter.fields = fields
    removed = set()
    for obj, fid, path in references(root):
        changed = rewrite_path(path, mapping)
        if changed is None and (obj.cls == m.REMOTE_CONTROL or (obj.cls == m.MOD_ROUTING and fid == m.F_ROUTE_TARGET)):
            removed.add(id(obj))
        else:
            m.set_field(obj, fid, changed or "")
    for obj in objects(root):
        for _, _, value in obj.fields:
            if isinstance(value, list):
                value[:] = [item for item in value if id(item) not in removed]


@dataclass(frozen=True)
class Fragment:
    nodes: tuple
    objects: tuple[Obj, ...]
    attachment: bytes = b""
    dependencies: tuple = ()

    @classmethod
    def capture(cls, nodes):
        from .assets import collect_dependencies, dependency_metadata

        preset = nodes[0].patch.preset if nodes else None
        snapshots = tuple(copy.deepcopy([n.obj for n in nodes]))
        dependencies = tuple((key, tuple(value)) for key, _, value in dependency_metadata(list(snapshots)) if value)
        attachment = preset.attachment if preset is not None and collect_dependencies(list(snapshots)) else b""
        return cls(tuple(nodes), snapshots, attachment, dependencies)
