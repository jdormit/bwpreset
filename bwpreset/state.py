"""Named instance and polyphonic inspector state, independent of parameters."""

from . import model as m
from .codec import Obj

COLORS = {
    "red": 0,
    "orange": 1,
    "yellow": 2,
    "green": 3,
    "blue": 4,
    "purple": 5,
    "white": 6,
    "black": 7,
    "light_grey": 8,
    "lime": 9,
    "mint": 10,
    "turquoise": 11,
    "dark_grey": 12,
    "grey": 13,
    "frequency_rainbow": 997,
    "inherit": 998,
    "default": 999,
    "image": 1000,
}
MONO_MODES = {"true": False, "digi": True}
VOICE_STACK_MODES = {"unipolar": 1, "bipolar": 2, "index": 3, "map": 4}
POLY_CHOICES = {
    "retrigger": {"never": 0, "note_on": 1, "always": 2},
    "note_priority": {"last": 0, "high": 1, "low": 2},
    "mono_mode": MONO_MODES,
}
INSTANCE_FIELDS = {"enabled": 0xA3, "color": 0x2643}
MODULE_FIELDS = {**INSTANCE_FIELDS, "width": 0x2651, "height": 0x2652}
MODULATOR_FIELDS = {**INSTANCE_FIELDS, "per_voice": 0x1A19}
POLY_FIELDS = {
    "voices": 0x28FF,
    "voice_stacking": 0x2900,
    "mono_mode": 0x2901,
    "legato_glide": 0x2903,
    "steal_same_key": 0x2904,
    "steal_fade_ms": 0x2905,
    "retrigger": 0x2906,
    "note_priority": 0x40D6,
}
STATE_FIELDS = {m.MODULE: MODULE_FIELDS, m.MODULATOR: MODULATOR_FIELDS, m.POLY: POLY_FIELDS}


def _int_type(value):
    return 1 if -128 <= value <= 127 else 2 if -32768 <= value <= 32767 else 3


def _choice(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {list(choices)}")
    return choices[value]


def apply_state(obj: Obj, **settings) -> Obj:
    """Apply named settings atomically; active aliases enabled on instances."""
    if obj.cls not in STATE_FIELDS:
        raise ValueError(f"unsupported state object {obj.cls:#x}")
    settings = dict(settings)
    if "enabled" in settings and not isinstance(settings["enabled"], bool):
        raise ValueError("enabled must be a bool")
    aliases = {"active": "enabled", "alternate_mono_voices": "mono_mode"}
    for alias, name in aliases.items():
        if alias not in settings:
            continue
        value = settings.pop(alias)
        if not isinstance(value, bool):
            raise ValueError(f"{alias} must be a bool")
        value = ("digi" if value else "true") if name == "mono_mode" else value
        if name in settings and settings[name] != value:
            raise ValueError(f"{alias} and {name} must agree")
        settings[name] = value
    fields = STATE_FIELDS[obj.cls]
    changes = {}
    for name, value in settings.items():
        if name not in fields:
            raise ValueError(f"unknown state {name!r}; choices are {list(fields)}")
        fid = fields[name]
        if name == "color":
            value = _choice(value, COLORS, name)
        elif name in POLY_CHOICES:
            value = _choice(value, POLY_CHOICES[name], name)
        elif name in ("enabled", "per_voice", "legato_glide", "steal_same_key"):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a bool")
        else:
            lo, hi = {"voices": (1, 64), "voice_stacking": (1, 16), "steal_fade_ms": (0, 1000)}.get(name, (1, 99))
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{name} must be an integer in {lo}..{hi}")
        changes[fid] = (5 if isinstance(value, bool) else _int_type(value), value)
    updated = [(f, *changes.pop(f)) if f in changes else (f, t, v) for f, t, v in obj.fields]
    updated.extend((f, t, v) for f, (t, v) in changes.items())
    obj.fields = updated
    return obj


def get_state(obj: Obj) -> dict:
    """Retrieve explicitly stored state. Unknown enum values raise ValueError."""
    if obj.cls not in STATE_FIELDS:
        raise ValueError(f"unsupported state object {obj.cls:#x}")
    result = {}
    for name, fid in STATE_FIELDS[obj.cls].items():
        if not any(f == fid for f, _, _ in obj.fields):
            continue
        value = m.get(obj, fid)
        choices = COLORS if name == "color" else POLY_CHOICES.get(name)
        if choices:
            reverse = {v: k for k, v in choices.items()}
            if value not in reverse:
                raise ValueError(f"unknown {name} value {value!r}")
            value = reverse[value]
        result[name] = value
    return result


def apply_voice_stack_mode(parameter: Obj, mode: str) -> Obj:
    """Set the Stack Spread/Voice Stack DISTRIBUTION_TYPE parameter."""
    if parameter.cls != m.P_ENUM or m.get(parameter, m.F_NAME) != "DISTRIBUTION_TYPE":
        raise ValueError("expected a voice-stack DISTRIBUTION_TYPE enum parameter")
    value = _choice(mode, VOICE_STACK_MODES, "voice stack mode")
    fid = m.VALUE_FIELD[m.P_ENUM]
    updated = [(f, 1, value) if f == fid else (f, t, v) for f, t, v in parameter.fields]
    if not any(f == fid for f, _, _ in updated):
        updated.append((fid, 1, value))
    parameter.fields = updated
    return parameter


def get_voice_stack_mode(parameter: Obj) -> str:
    """Return the named stack distribution; unknown values are errors."""
    if parameter.cls != m.P_ENUM or m.get(parameter, m.F_NAME) != "DISTRIBUTION_TYPE":
        raise ValueError("expected a voice-stack DISTRIBUTION_TYPE enum parameter")
    value = m.param_value(parameter)
    reverse = {v: k for k, v in VOICE_STACK_MODES.items()}
    if value not in reverse:
        raise ValueError(f"unknown voice stack mode {value!r}")
    return reverse[value]
