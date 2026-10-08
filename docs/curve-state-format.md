# Curve playback and Grid instance state

These helpers make curve markers, playback and inspector state explicit rather than inheriting them from a learned preset. The mappings below come from Bitwig 6.1.3's installed definitions, Java field registrations and enum constructors. They establish the file representation; live playback acceptance remains separate.

## Curve API

```python
from bwpreset.curves import Curve

curve = Curve(
    [(0, 0), (0.2, 1, -0.5), (0.6, 0.4), (1, 0)],
    kind="envelope",
    sustain=2,
    playback="hold",
    bipolar=False,
    reflect=False,
    anti_alias=True,
)
```

`sustain`, `release_start`, `loop_start` and `loop_end` are zero-based **point indices**, not x coordinates. `sustain` and `release_start` name the same hold marker; if both are supplied, they must agree. `None` clears a marker (stored as `-1`). Repeated x coordinates remain valid, and positions may exceed 1. Bend shapes the outgoing segment.

`kind` accepts `cycle`, `envelope` and `transfer`. `playback` accepts `straight` (also `one_shot`), `hold`, `loop` and `ping_pong`. Use `playback="loop", loop_start=1, loop_end=2` for a forward sustain loop. Kind is curve-file/editor classification; playback is a setting on the containing parameter. Setting one does not set the other. The category string is browser metadata and is retained independently.

`curve.options` retrieves named geometry and explicitly supplied/imported wrapper options. It reports stored playback 0 canonically as `straight`. Unknown kind or playback enum values raise `ValueError`; their raw scalar fields remain preserved by import and export.

```python
data = curve.to_object()  # BWCURVE payload
curve = Curve.from_object(data)
parameter = curve.to_parameter(template)  # copied CURVE parameter
curve = Curve.from_parameter(parameter)  # geometry and wrapper settings
parameter = curve.to_parameter()  # standalone CURVE parameter
```

`to_parameter(template)` keeps the template parameter's identifier and merges imported wrapper fields. It replaces the embedded payload and clears an existing external-resource reference. `from_parameter()` requires embedded geometry; an external-only curve must first be resolved. Scalar/null wrapper fields, including unknown ones and their original widths, are retained. Object-valued wrapper settings are explicitly rejected. `repr(curve)` remains executable Python and includes `wrapper_settings` only when present. `Curve.from_file()` continues to preserve curve-file metadata.

### Curve mappings and evidence

| Owner | Field | Named meaning | Evidence |
| --- | --- | --- | --- |
| Curve `0x127f` | `0x362d` | kind: cycle 0, envelope 1, transfer 2 | `Kar` registration `curve_kind`; `IDB` and `dcr` enum constructors |
| Curve | `0x3788` | sustain/release-start point index | `Kar`: `hold_point_index`, `set_hold_point`; installed `float_core-resources.properties`: “A single point defines the sustain level / release start” |
| Curve | `0x3665` | loop start point index | `Kar`: `loop_start_index`, `set_loop_start(point_index)` |
| Curve | `0x3666` | loop end point index | `Kar`: `loop_end_index`, `set_loop_end(point_index)` |
| Parameter `0x128c` | `0x3842` | straight 0, hold 1, loop 2, ping-pong 3 | `tfX`: `envelope_behavior`; `GR3` and `cVG` enum constructors |
| Parameter | `0x36af` | bipolar | `tfX`: `bipolar`, boolean |
| Parameter | `0x3986` | reflect | `tfX`: `reflect`, boolean |
| Parameter | `0x3843` | anti-alias | `tfX`: `anti_alias`, boolean |
| Parameter | `0x361d` | embedded resource | `tfX`: `resource`, linked curve class |

The installed envelope-behavior descriptions distinguish one-shot playback at note-on, hold at sustain/release-start, forward looping on sustain, and ping-pong looping on sustain. There is no independent release marker in the curve class. The hold marker already defines the release start.

## Instance and POLY API

```python
from bwpreset.state import apply_state, get_state

apply_state(module_obj, enabled=False, color="mint", width=8, height=3)
apply_state(modulator_obj, active=True, per_voice=True, color="purple")
apply_state(poly_obj, voices=8, voice_stacking=4, mono_mode="digi")
settings = get_state(module_obj)
```

`apply_state(obj, **settings)` validates every change before mutation and returns `obj`. `get_state(obj)` returns explicitly stored named settings; it does not fill missing fields with guessed defaults. Both preserve unrelated fields. Active is an input alias for enabled, and `alternate_mono_voices` is a boolean input alias for `mono_mode`. Conflicting aliases are errors.

Modules support enabled/active, color and width/height. Modulators support enabled/active, color and per-voice. POLY supports voices, voice stacking, mono mode, legato glide, steal-same-key, steal-fade milliseconds, retrigger and note priority. Glide remains an ordinary parameter handled by the core builder.

| Owner | Field | Meaning / validation | Evidence |
| --- | --- | --- | --- |
| Module/modulator | `0x00a3` | enabled boolean | Base preset `yA3`: `enabled`; native-preset serialization also writes enabled at 163 |
| Module/modulator | `0x2643` | named accent color | `aEA`: `accent_color`; `sci` / `V1p` display-color enum |
| Module | `0x2651`, `0x2652` | grid width/height, integers 1–99 | `Qll`: `grid_width`, `grid_height`; registrations declare default 1 and bounds 1–99 |
| Modulator | `0x1a19` | per-voice boolean | `VRn`: `is_polyphonic_mode`; `ntr` inspector exposes `per_voice` |
| POLY `0x0d94` | `0x28ff` | voices, 1–64 | `PyL`: `user_requested_voices`, declared bounds |
| POLY | `0x2900` | voice-stack count, 1–16 | `PyL`: `voice_stacking`, declared bounds |
| POLY | `0x2901` | alternate mono voices: false = True Mono, true = Digi Mono | `PyL`: `alternate_mono_voices`; runtime `pmZ` mono enum `CiR` / `SYd`; installed `float_common_atoms-resources.properties` labels/descriptions |
| POLY | `0x2903`, `0x2904` | legato glide, steal same key | `PyL` registrations |
| POLY | `0x2905` | steal fade, milliseconds 0–1000 | `PyL`: `steal_fade_time`, declared bounds |
| POLY | `0x2906` | retrigger: never 0, note-on 1, always 2 | Existing builder mapping; `PyL`: `retrigger_mode` |
| POLY | `0x40d6` | priority: last 0, high 1, low 2 | Existing builder mapping; `PyL`: `note_priority` |

`mono_mode="true"` selects one continuously active mono voice, which supports drones. `mono_mode="digi"` alternates two mono voices, retriggers envelopes from zero and requires note input. The distinction is relevant when `voices=1`. This explains why earlier changes to `0x2901` while voices were greater than one produced no visible inspector change.

Color names and stored IDs are `red=0`, `orange=1`, `yellow=2`, `green=3`, `blue=4`, `purple=5`, `white=6`, `black=7`, `light_grey=8`, `lime=9`, `mint=10`, `turquoise=11`, `dark_grey=12`, `grey=13`, `frequency_rainbow=997`, `inherit=998`, `default=999`, `image=1000`. These are **serialized enum IDs, not Java ordinal positions**. Special colors are enum members; whether a module renders them specially depends on its UI. Unknown color IDs are explicit retrieval errors.

### Voice-stack distribution selector

The Voice Stack / Stack Spread selector is the modulator's `DISTRIBUTION_TYPE` enum parameter, not another field on POLY. Installed enum descriptors give `0 to 1=1`, `-1 to 1=2`, `Index=3`, `Map=4`.

```python
from bwpreset.state import apply_voice_stack_mode, get_voice_stack_mode

apply_voice_stack_mode(distribution_parameter, "bipolar")
mode = get_voice_stack_mode(distribution_parameter)
```

The helper names are `unipolar=1`, `bipolar=2`, `index=3`, `map=4`. Map values remain the modulator's ordinary `VOICE_STACK_MAP_n` parameters. Both helpers check the parameter class and identifier and reject unknown modes. The selector is already discoverable by a general enum-name setter using its UI labels.

## Core integration handoff

Replace the builder's manual curve embedding branch with:

```python
replacement = value.to_parameter(p)
checked.append((name, None, replacement))
```

The decompiler must use `Curve.from_parameter(p)` instead of `Curve.from_object(embedded)` to preserve wrapper flags. Catch `ValueError` and report an unsupported parameter when its payload cannot be represented.

Expose instance settings through a Node method returning self after `apply_state(self.obj, **settings)`. For decompilation, emit the dictionary returned by `get_state(obj)` through that method. For POLY, route named scalar inspector settings through `apply_state(self.poly, **settings)`; `get_state(self.poly)` supplies the corresponding decompiler constructor arguments. Keep the existing glide-parameter path.

For a dedicated voice-stack-mode method, validate against the available `DISTRIBUTION_TYPE` parameter before calling `apply_voice_stack_mode(self._param("DISTRIBUTION_TYPE"), mode)`, so sparse parameter inclusion stays under Node's control. A general named enum setter can instead use the descriptor labels above.

## Verification and remaining acceptance

Synthetic tests exercise named-field encoding, integer-width changes, invalid and conflicting settings, mutation atomicity, unknown-field preservation, wrapper round trips and executable representations. All 170 installed factory curve payloads still serialize identically after import/export, including repeated positions and positions greater than one. An independent adversarial review caught standalone wrapper identity and copied-marker validation defects; both have regression tests.

Checks on 2026-10-07:

```text
uv run pytest tests/test_curve_options.py tests/test_state.py tests/test_curves.py -q
48 passed
uvx ruff check bwpreset/curves.py bwpreset/state.py tests/test_curve_options.py tests/test_state.py
All checks passed
uvx ruff format --check bwpreset/curves.py bwpreset/state.py tests/test_curve_options.py tests/test_state.py
4 files already formatted
```

The corpus check parsed each installed `.bwcurve`, rebuilt it with `Curve.from_object(source).to_object()`, and compared both objects' `write_object()` output: 170 identical payloads, zero failures. No typechecker is configured in the project.

Live sustain timing, ping-pong direction, reflection/anti-alias behavior, per-voice modulation, resizable UI behavior and True/Digi Mono playback still require Bitwig verification. No opaque ID passthrough substitutes for these named APIs. The exact rendering behavior of special color modes is not established by their enum membership.
