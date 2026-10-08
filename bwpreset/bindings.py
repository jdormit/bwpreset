"""Named source selectors for Grid modules and device modulators."""

import copy
import uuid
from dataclasses import dataclass
from enum import Enum

from . import model as m
from .codec import Obj

SOURCE_PARAMETER = 0x37C
DESTINATION_PARAMETER = 0x37B
F_SOURCE_BINDING = 0x10FF
F_DESTINATION_BINDING = 0x10FE
BUS_TYPES = {"unsupported": -1, "mono": 0, "stereo": 1}


class BindingError(ValueError):
    pass


class SelectorKind(str, Enum):
    AUDIO_SIDECHAIN = "audio_sidechain"
    NOTE_SIDECHAIN = "note_sidechain"
    HARDWARE_INPUT = "hardware_input"
    HARDWARE_OUTPUT = "hardware_output"


DEFINITION_KINDS = {
    0x359: SelectorKind.AUDIO_SIDECHAIN,
    0x35C: SelectorKind.NOTE_SIDECHAIN,
    0x35B: SelectorKind.HARDWARE_INPUT,
    0x35A: SelectorKind.HARDWARE_OUTPUT,
}


def selector_kind(atom: Obj) -> SelectorKind:
    if not isinstance(atom, Obj) or atom.cls not in DEFINITION_KINDS:
        raise BindingError("unsupported routing definition atom")
    return DEFINITION_KINDS[atom.cls]


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or "\x00" in value or (not empty and not value):
        raise BindingError(f"{label} must be {'a' if empty else 'a nonempty'} string")


def _signal(signal, bus_type):
    if signal not in ("audio", "note"):
        raise BindingError("signal must be audio or note")
    if bus_type not in BUS_TYPES:
        raise BindingError("bus_type must be unsupported, mono or stereo")
    if signal == "note" and bus_type != "stereo":
        raise BindingError("note sources have no audio bus_type")


@dataclass(frozen=True)
class NoSource:
    """Use the selector's native default: no input or device input, by context."""

    scope = "portable"


@dataclass(frozen=True)
class DeviceSource:
    path: str
    signal: str = "audio"
    bus_type: str = "stereo"
    relative_to: str = "device"

    @property
    def scope(self):
        return "device-local" if self.relative_to == "device" else "device-chain-local"

    def __post_init__(self):
        _text(self.path, "path")
        _signal(self.signal, self.bus_type)
        if self.relative_to not in ("device", "device_chain"):
            raise BindingError("relative_to must be device or device_chain")
        if self.path.startswith("/") or any(part in (".", "..", "") for part in self.path.replace(":", "/").split("/")):
            raise BindingError("path must be a device-relative Bitwig source path")


@dataclass(frozen=True)
class TrackSource:
    track_uuid: uuid.UUID
    signal: str = "audio"
    bus_type: str = "stereo"
    pre_fader: bool = False

    scope = "project-local"

    def __post_init__(self):
        if not isinstance(self.track_uuid, uuid.UUID) or self.track_uuid.int == 0:
            raise BindingError("track_uuid must be a nonzero UUID from the destination project")
        _signal(self.signal, self.bus_type)
        if not isinstance(self.pre_fader, bool) or (self.signal == "note" and self.pre_fader):
            raise BindingError("pre_fader must be a boolean and is only available for audio tracks")


@dataclass(frozen=True)
class HardwarePort:
    interface: str
    api: str
    machine_id: str
    direction: str
    device_id: str
    channel_ids: tuple[str, ...]
    client_id: str = ""
    configuration_id: str = ""
    client_name: str = ""
    device_name: str = ""
    port_name: str | None = None
    user_defined_device_name: str | None = None
    user_defined_port_name: str | None = None
    is_default_recording_input: bool | None = None
    port_id: str | None = None

    scope = "machine-local"

    @property
    def bus_type(self):
        return "mono" if len(self.channel_ids) == 1 else "stereo"

    def __post_init__(self):
        for label in ("interface", "api", "machine_id", "device_id"):
            _text(getattr(self, label), label)
        for label in ("client_id", "configuration_id", "client_name", "device_name"):
            _text(getattr(self, label), label, empty=True)
        if self.direction not in ("input", "output"):
            raise BindingError("direction must be input or output")
        if not isinstance(self.channel_ids, tuple) or len(self.channel_ids) not in (1, 2):
            raise BindingError("channel_ids must be a mono or stereo tuple of installed port IDs")
        for channel in self.channel_ids:
            _text(channel, "channel ID")
        if len(set(self.channel_ids)) != len(self.channel_ids):
            raise BindingError("stereo channel IDs must be distinct")
        for label in ("port_name", "user_defined_device_name", "user_defined_port_name"):
            if getattr(self, label) is not None:
                _text(getattr(self, label), label, empty=True)
        if self.is_default_recording_input is not None and not isinstance(self.is_default_recording_input, bool):
            raise BindingError("is_default_recording_input must be a boolean or None")
        if self.port_id is not None:
            _text(self.port_id, "port_id", empty=True)
            if len(self.channel_ids) != 2:
                raise BindingError("mono port IDs are supplied through channel_ids; port_id is separate stereo metadata")


@dataclass(frozen=True)
class PreferencesBus:
    interface: str
    api: str
    machine_id: str
    direction: str
    bus_name: str
    bus_uuid: uuid.UUID | None
    configuration_id: str = ""
    bus_type: str | None = "stereo"

    scope = "machine-local"

    def __post_init__(self):
        for label in ("interface", "api", "machine_id", "configuration_id"):
            _text(getattr(self, label), label, empty=True)
        _text(self.bus_name, "bus_name", empty=True)
        if self.bus_uuid is not None and not isinstance(self.bus_uuid, uuid.UUID):
            raise BindingError("bus_uuid must be a UUID from hardware preferences or None")
        if self.direction not in ("input", "output"):
            raise BindingError("direction must be input or output")
        if self.bus_type is not None and self.bus_type not in BUS_TYPES:
            raise BindingError("bus_type must be unsupported, mono, stereo or None")
        if self.direction == "output" and self.bus_type == "stereo":
            object.__setattr__(self, "bus_type", None)
        if self.direction == "output" and self.bus_type is not None:
            raise BindingError("bus_type is a source field; output preferences buses have no bus_type setting")


@dataclass(frozen=True)
class HardwareBindings:
    configurations: tuple[HardwarePort | PreferencesBus, ...]
    direction: str | None = None
    bus_type: str | None = "auto"

    scope = "machine-local"

    def __post_init__(self):
        if not isinstance(self.configurations, (tuple, list)) or not all(
            isinstance(c, (HardwarePort, PreferencesBus)) for c in self.configurations
        ):
            raise BindingError("configurations must contain named HardwarePort or PreferencesBus values")
        if self.direction is not None and self.direction not in ("input", "output"):
            raise BindingError("direction must be input, output or None")
        object.__setattr__(self, "configurations", tuple(self.configurations))
        direction = self.direction or (self.configurations[0].direction if self.configurations else None)
        if direction not in ("input", "output") or any(c.direction != direction for c in self.configurations):
            raise BindingError("configurations must share one input/output direction; empty collections require direction")
        object.__setattr__(self, "direction", direction)
        bus = self.bus_type
        if bus == "auto":
            modes = {c.bus_type for c in self.configurations}
            if direction == "output":
                bus = None
            elif len(modes) == 1:
                bus = modes.pop()
            elif not modes:
                raise BindingError("empty input collections require an explicit bus_type")
            else:
                raise BindingError("mixed channel configurations require an explicit source bus_type")
        if bus is not None and bus not in BUS_TYPES:
            raise BindingError("bus_type must be unsupported, mono, stereo or None")
        if direction == "output" and bus is not None:
            raise BindingError("hardware output wrappers have no bus_type field")
        if direction == "input" and any(isinstance(c, PreferencesBus) and c.bus_type != bus for c in self.configurations):
            raise BindingError("preferences profile bus_type must match the source wrapper bus_type")
        object.__setattr__(self, "bus_type", bus)

    def for_machine(self, machine_id: str, api: str | None = None):
        """Filter by explicit machine/API identity without choosing a preferred configuration."""
        return tuple(c for c in self.configurations if c.machine_id == machine_id and (api is None or c.api == api))


HARDWARE_VALUES = (HardwarePort, PreferencesBus, HardwareBindings)
BINDING_CLASSES = (NoSource, DeviceSource, TrackSource, HardwarePort, PreferencesBus, HardwareBindings)
BINDING_CLASS_NAMES = tuple(cls.__name__ for cls in BINDING_CLASSES)
Binding = NoSource | DeviceSource | TrackSource | HardwarePort | PreferencesBus | HardwareBindings


def _configuration(value: HardwarePort | PreferencesBus) -> Obj:
    fields = [(2022, 8, value.interface), (2023, 8, value.machine_id), (4513, 8, value.api), (4514, 8, value.configuration_id)]
    if isinstance(value, PreferencesBus):
        return Obj(0x45B, fields + [(7448, 8, value.bus_name), (2024, 10 if value.bus_uuid is None else 0x15, value.bus_uuid)])
    stereo = len(value.channel_ids) == 2
    fields += [
        (15876, 8, value.client_name),
        (15877, 8, value.client_id),
        (15878, 8, value.device_name),
        (15879, 8, value.device_id),
        (15882 if stereo else 15881, 8, value.channel_ids[0]),
    ]
    if stereo:
        fields.append((15883, 8, value.channel_ids[1]))
        if value.port_id is not None:
            fields.append((15881, 8, value.port_id))
    for fid, typ, name in (
        (15880, 8, "port_name"),
        (15928, 8, "user_defined_device_name"),
        (15929, 8, "user_defined_port_name"),
        (16252, 5, "is_default_recording_input"),
    ):
        if getattr(value, name) is not None:
            fields.append((fid, typ, getattr(value, name)))
    return Obj(0x14E6 if stereo else 0x14E5, fields)


def _payload(value: Binding) -> Obj | None:
    if isinstance(value, NoSource):
        return None
    if isinstance(value, DeviceSource):
        cls = {("audio", "device"): 0x573, ("audio", "device_chain"): 0x57E, ("note", "device"): 0x611, ("note", "device_chain"): 0x60E}[
            (value.signal, value.relative_to)
        ]
        return Obj(
            cls,
            ([(0x1191, 1, BUS_TYPES[value.bus_type])] if value.signal == "audio" else [])
            + [(0x1446 if value.signal == "audio" else 0x161A, 8, value.path)],
        )
    if isinstance(value, TrackSource):
        return Obj(
            (0x432 if value.pre_fader else 0x431) if value.signal == "audio" else 0x593,
            ([(0x1191, 1, BUS_TYPES[value.bus_type])] if value.signal == "audio" else [])
            + [(0x1193 if value.signal == "audio" else 0x147D, 0x15, value.track_uuid)],
        )
    if isinstance(value, HARDWARE_VALUES):
        configurations = value.configurations if isinstance(value, HardwareBindings) else (value,)
        return Obj(
            0x42A if value.direction == "input" else 0x427,
            ([(4497, 1, BUS_TYPES[value.bus_type])] if value.direction == "input" and value.bus_type is not None else [])
            + [(4496 if value.direction == "input" else 4493, 0x12, [_configuration(c) for c in configurations])],
        )
    raise BindingError("binding requires a named source or hardware binding")


def _field(parameter):
    if not isinstance(parameter, Obj) or parameter.cls not in (SOURCE_PARAMETER, DESTINATION_PARAMETER):
        raise BindingError("unsupported binding parameter class")
    return F_SOURCE_BINDING if parameter.cls == SOURCE_PARAMETER else F_DESTINATION_BINDING


def _preserve_unknown(old, new):
    if isinstance(old, Obj) and isinstance(new, Obj) and old.cls == new.cls:
        replacements = {f: (t, v) for f, t, v in new.fields}
        fields = []
        for f, t, v in old.fields:
            if old.cls == 0x42A and f == 4497 and f not in replacements:
                continue
            if old.cls in (0x14E5, 0x14E6) and f in (15880, 15928, 15929, 16252) and f not in replacements:
                continue
            if old.cls == 0x14E6 and f == 15881 and f not in replacements:
                continue
            if f in replacements:
                t, replacement = replacements.pop(f)
                v = _preserve_unknown(v, replacement)
            fields.append((f, t, v))
        new.fields = fields + [(f, t, v) for f, (t, v) in replacements.items()]
    elif isinstance(old, list) and isinstance(new, list):
        if all(isinstance(c, Obj) and c.cls in (0x45B, 0x14E5, 0x14E6) for c in new):
            remaining = [c for c in old if isinstance(c, Obj) and c.cls in (0x45B, 0x14E5, 0x14E6)]
            matches = {}
            for i, config in enumerate(new):
                match = next(
                    (
                        c
                        for c in remaining
                        if _configuration_key(c) == _configuration_key(config) and _channel_key(c) == _channel_key(config)
                    ),
                    None,
                )
                if match is not None:
                    remaining.remove(match)
                    matches[i] = match
            for i, config in enumerate(new):
                if i not in matches:
                    match = next((c for c in remaining if _configuration_key(c) == _configuration_key(config)), None)
                    if match is not None:
                        remaining.remove(match)
                        matches[i] = match
            new = [_preserve_unknown(matches.get(i), config) for i, config in enumerate(new)]
        elif len(old) == len(new):
            new = [_preserve_unknown(a, b) for a, b in zip(old, new)]
    return new


def _configuration_key(config):
    return (
        config.cls,
        m.get(config, 2023),
        m.get(config, 4513),
        m.get(config, 4514),
        (m.get(config, 2024), m.get(config, 7448)) if config.cls == 0x45B else m.get(config, 15879),
    )


def _channel_key(config):
    return tuple(m.get(config, f) for f in ((15882, 15883) if config.cls == 0x14E6 else (15881,)))


def _validate_kind(parameter, value, kind):
    if kind is None:
        kind = getattr(parameter, "binding_kind", None)
    if kind is None:
        return
    try:
        kind = SelectorKind(kind)
    except (TypeError, ValueError) as error:
        raise BindingError("unsupported selector kind") from error
    if (kind == SelectorKind.HARDWARE_OUTPUT) != (parameter.cls == DESTINATION_PARAMETER):
        raise BindingError("selector kind disagrees with its source/destination wrapper")
    if isinstance(value, NoSource):
        return
    if kind in (SelectorKind.HARDWARE_INPUT, SelectorKind.HARDWARE_OUTPUT):
        if not isinstance(value, HARDWARE_VALUES):
            raise BindingError("hardware selectors require named hardware bindings or NoSource")
    elif kind == SelectorKind.NOTE_SIDECHAIN:
        if isinstance(value, HARDWARE_VALUES) or value.signal != "note":
            raise BindingError("note sidechains require a note source")
    elif not isinstance(value, HARDWARE_VALUES) and value.signal != "audio":
        raise BindingError("audio sidechains require an audio source")


def apply_binding(parameter: Obj, value: Binding, *, kind: SelectorKind | str | None = None) -> Obj:
    """Return an edited copy; callers install it in the owning parameter list."""
    fid = _field(parameter)
    payload = _payload(value)
    _validate_kind(parameter, value, kind)
    if isinstance(value, HARDWARE_VALUES):
        expected = "input" if parameter.cls == SOURCE_PARAMETER else "output"
        if value.direction != expected:
            raise BindingError(f"this selector requires a hardware {expected}")
    elif parameter.cls == DESTINATION_PARAMETER and not isinstance(value, NoSource):
        raise BindingError("hardware output selectors require named hardware bindings or NoSource")
    result = copy.deepcopy(parameter)
    payload = _preserve_unknown(m.get(result, fid), payload)
    replacement = (fid, 10 if payload is None else 9, payload)
    if any(f == fid for f, _, _ in result.fields):
        result.fields = [replacement if f == fid else (f, t, v) for f, t, v in result.fields]
    else:
        result.fields.append(replacement)
    return result


def decode_binding(parameter: Obj) -> Binding:
    """Decode recognized selector meanings; unknown variants raise BindingError."""
    payload = m.get(parameter, _field(parameter))
    if payload is None:
        return NoSource()
    if not isinstance(payload, Obj):
        raise BindingError("unsupported source payload")
    if parameter.cls == DESTINATION_PARAMETER and payload.cls != 0x427:
        raise BindingError("unsupported hardware destination payload")
    if parameter.cls == SOURCE_PARAMETER and payload.cls == 0x427:
        raise BindingError("unsupported hardware output payload in a source selector")
    get = lambda fid, default=None: m.get(payload, fid, default)  # noqa: E731
    bus = {v: k for k, v in BUS_TYPES.items()}.get(get(4497, 1))
    if payload.cls in (0x573, 0x57E, 0x611, 0x60E):
        audio = payload.cls in (0x573, 0x57E)
        return DeviceSource(
            get(5190 if audio else 5658),
            "audio" if audio else "note",
            bus if audio else "stereo",
            "device_chain" if payload.cls in (0x57E, 0x60E) else "device",
        )
    if payload.cls in (0x431, 0x432, 0x593):
        audio = payload.cls in (0x431, 0x432)
        return TrackSource(get(4499 if audio else 5245), "audio" if audio else "note", bus if audio else "stereo", payload.cls == 0x432)
    if payload.cls in (0x42A, 0x427):
        configs = get(4496 if payload.cls == 0x42A else 4493, [])
        if not isinstance(configs, list):
            raise BindingError("hardware configurations must be a list")
        direction = "input" if payload.cls == 0x42A else "output"
        bus = None if get(4497) is None else {v: k for k, v in BUS_TYPES.items()}.get(get(4497))
        if direction == "input" and get(4497) is not None and bus is None:
            raise BindingError("unsupported hardware source bus_type")
        values = tuple(_decode_configuration(config, direction, bus) for config in configs)
        if len(values) == 1 and (direction == "output" or bus == values[0].bus_type):
            return values[0]
        return HardwareBindings(values, direction=direction, bus_type=bus if direction == "input" else None)
    raise BindingError(f"unsupported source payload class {payload.cls:#x}")


def _decode_configuration(config, direction, bus):
    if not isinstance(config, Obj):
        raise BindingError("hardware configuration must be an object")
    get = lambda fid, default="": m.get(config, fid, default)  # noqa: E731
    if config.cls == 0x45B:
        return PreferencesBus(
            get(2022),
            get(4513),
            get(2023),
            direction,
            get(7448),
            get(2024, None),
            configuration_id=get(4514),
            bus_type=bus if direction == "input" else None,
        )
    if config.cls not in (0x14E5, 0x14E6):
        raise BindingError(f"unsupported hardware configuration class {config.cls:#x}")
    channels = (get(15881),) if config.cls == 0x14E5 else (get(15882), get(15883))
    return HardwarePort(
        get(2022),
        get(4513),
        get(2023),
        direction,
        get(15879),
        channels,
        get(15877),
        get(4514),
        get(15876),
        get(15878),
        port_name=get(15880, None),
        user_defined_device_name=get(15928, None),
        user_defined_port_name=get(15929, None),
        is_default_recording_input=get(16252, None),
        port_id=get(15881, None) if config.cls == 0x14E6 else None,
    )


def parameter_from_definition(atom: Obj) -> Obj:
    """Translate the installed routing atom into a sparse selector parameter."""
    kind = selector_kind(atom)
    name = m.get(atom, 0x10F5)
    _text(name, "selector identifier")
    output = atom.cls == 0x35A
    parameter = Obj(
        DESTINATION_PARAMETER if output else SOURCE_PARAMETER,
        [(m.F_NAME, 8, name), (F_DESTINATION_BINDING if output else F_SOURCE_BINDING, 10, None)],
    )
    parameter.binding_kind = kind
    return parameter
