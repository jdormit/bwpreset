# External bindings and Grid slots

These helpers let Grid patches select external sources and attach existing devices to their FX and Note FX chains. They operate on the codec graph so the coordinator can integrate them with the builder and decompiler without changing their serialization rules.

## Binding API

`bwpreset.bindings.apply_binding(parameter, value, *, kind=None)` returns a deep-copied selector parameter. Install that returned object in the owner's parameter list. It leaves the original parameter untouched and preserves unknown fields and field order on matching payload/configuration classes. `decode_binding(parameter)` returns a named binding or raises `BindingError` for an unsupported payload. A raw `Obj` cannot be used as a binding value.

| Value | Meaning and ownership |
| --- | --- |
| `NoSource()` | Writes null. This means the selector's native default, which is **context-dependent**: a module may show No Input while a modulator shows Device Input. It does not promise silence. |
| `DeviceSource(path, signal="audio", bus_type="stereo", relative_to="device")` | A Bitwig source path relative to the owning device or, with `relative_to="device_chain"`, its chain. `signal="note"` uses the note-source payload. The owning graph must contain the named source. |
| `TrackSource(track_uuid, signal="audio", bus_type="stereo", pre_fader=False)` | The UUID must come from the destination project. `pre_fader=True` selects the application's pre-fader audio source variant. This is project-local; a track's display name cannot resolve its identity. |
| `HardwarePort(interface, api, machine_id, direction, device_id, channel_ids, client_id="", configuration_id="", client_name="", device_name="")` | Machine-local plug-and-play hardware selection. `interface` is the user-facing bus label; `client_name` and `device_name` are independent backend labels. `direction` is `input` or `output`; `channel_ids` is a tuple of one or two installed port-ID strings. These are backend IDs, not one-based channel numbers. |
| `PreferencesBus(interface, api, machine_id, direction, bus_name, bus_uuid, configuration_id="", bus_type="stereo")` | Legacy preferences-bus hardware selection. The bus name and UUID identify an existing preferences bus; they do not encode physical channel numbers. Shared machine/API/configuration fields remain independent. `bus_uuid=None` preserves the application's nullable default, without promising a usable hardware connection. |
| `HardwareBindings(configurations, direction=None, bus_type="auto")` | An ordered collection of `HardwarePort` and/or `PreferencesBus` profiles. It preserves the whole machine/API configuration list, rather than selecting one profile while decoding. Lists are normalized to immutable tuples. |

`HardwarePort` additionally accepts optional `port_name`, `user_defined_device_name`, `user_defined_port_name`, and `is_default_recording_input`. Stereo profiles can also retain the base `port_id` field separately from their left/right channel IDs; mono port IDs are already represented by `channel_ids`. `None` leaves these optional fields unwritten and clears an existing value when reapplied; empty strings and false booleans are preserved as distinct stored values.

`HardwareBindings` infers a shared direction and, for inputs, a shared source bus mode when the profiles agree. Empty collections require an explicit direction; empty inputs also require explicit `bus_type` (including `None` for an absent mode). Mixed mono/stereo input port profiles require explicit `bus_type="mono"`, `"stereo"`, or `"unsupported"`; the source wrapper's bus mode is separate from each profile's port layout. `bus_type=None` leaves the source mode absent. Output wrappers have no source bus-mode field. `PreferencesBus.bus_type` describes that shared input-wrapper setting, not a channel count: preferences profiles must agree with an explicit collection mode, and absent modes remain `None`. Output preferences profiles normalize the default to `None`. `for_machine(machine_id, api=None)` returns matching profiles in order without guessing which one Bitwig will choose.

Decoding keeps the existing single-profile API: one ordinary plug-and-play profile returns `HardwarePort`, and one legacy profile returns `PreferencesBus`. Empty or multiple profiles return `HardwareBindings`; a single profile also returns the collection when preserving an absent or independent source bus mode requires it. Reapplying an edited collection to its original selector retains unknown fields on matching profiles across append, removal and reordering. Profile matching uses class, machine/API/configuration identity and bus UUID or device ID; exact channel IDs disambiguate same-device profiles before an edited-channel fallback.

The audio bus choices are `mono` (0), `stereo` (1), and `unsupported` (-1). The last is the application's own enum name and occurs in shipped device-relative selectors; it is not a new inferred channel mode. Note sources have no audio bus choice. Hardware sources derive mono/stereo from their channel tuple; outputs use the destination wrapper. HW/CV modules and HW CV In use the same hardware selector representation. Voltage scaling, CV polarity and calibration are separate ordinary parameters.

`parameter_from_definition(atom)` translates these installed definition atoms into null selector parameters:

| Definition atom | Parameter names observed | Preset wrapper |
| --- | --- | --- |
| Audio sidechain, `0x359` | Module `SOURCE`; modulator `SIDECHAIN` | Source `0x37c` |
| Note sidechain, `0x35c` | Pitch Quantize `NOTE_SIDECHAIN`; modulator `SOURCE` | Source `0x37c` |
| Hardware input, `0x35b` | `INPUT` | Source `0x37c` |
| Hardware output, `0x35a` | `OUTPUT` | Destination `0x37b` |

`SelectorKind` defines `AUDIO_SIDECHAIN`, `NOTE_SIDECHAIN`, `HARDWARE_INPUT`, and `HARDWARE_OUTPUT`, with corresponding string values `audio_sidechain`, `note_sidechain`, `hardware_input`, and `hardware_output`. `selector_kind(atom)` returns the matching kind. `parameter_from_definition(atom)` also attaches Python-only `binding_kind` metadata; it survives deep copies but is never serialized into the preset. `apply_binding` uses that metadata automatically, or accepts explicit `kind=`. Audio/note mismatches, non-hardware bindings on hardware selectors, and hardware direction mismatches are rejected before mutation.

Discovery should persist `selector_kind(atom).value` in the parameter schema. The core setter dispatches selector classes to `apply_binding` and must pass that schema kind when using parsed templates or editing a parsed preset, because parsing loses Python-only metadata. The low-level helper always enforces source/destination direction.

Selector identifiers are taken from their definition or existing parameter, not hardcoded. The installed HW/CV definitions use `INPUT`/`OUTPUT`; HW FX uses `IN_ROUTING`/`OUT_ROUTING`; HW CV Out uses `CV_OUT`. The same wrappers also support imported hardware selectors named `SOURCE` or `DEVICE`, with explicit `kind=` when definition metadata is unavailable. No evidence from this installation establishes `SOURCE`/`DEVICE` as alternate names for the current module definitions, so the helpers preserve these identifiers instead of inventing aliases.

`BINDING_CLASSES` and `BINDING_CLASS_NAMES` enumerate all public binding values. Generated decompiler code needs to import the new `PreferencesBus` and `HardwareBindings` classes as well as the original four classes. The setter continues to use the same source/destination parameter classes and `apply_binding` entry point.

Bindings do not create tracks, interfaces or ports. Importing a device deep-copies its selector payloads without changing project UUIDs or hardware IDs. The coordinator must resolve or deliberately retain those external references for the destination. A relative source remains portable only when its referenced graph is imported with it; moving a selector alone can leave a dangling path.

## Slot API

`Device.from_file(path)` reads an existing `.bwpreset`; `Device.from_preset(preset)` and `Device.from_object(device, dependencies=..., descriptors=...)` support already-parsed graphs. A source must be a device preset object with a device UUID. Object graphs are copied with shared-object identity intact within each copy, so references do not retain pointers to the source graph.

`Device.from_definition(path, *, default_settings=None)` creates a native device using its `.bwdevice` UUID, translated scalar defaults and named routing selectors. Descriptors and selector kinds come from the definition. Omitted native state is intended to use Bitwig's installed defaults; that sparse-device behavior still needs live verification. For complete non-scalar/default chain state, pass an existing device object as `default_settings`, or import a saved preset. This helper does not translate arbitrary device internals into authored settings.

`insert_slot(destination, slot, source)` accepts a codec `Preset` or a builder `Patch`. It appends a copy, leaving existing devices, chain state, indices, UUIDs and device-internal routing/remote paths intact. The aliases are `fx` → `POST_FX` and `note_fx` → `PRE_FX`. A named existing slot is also accepted. UUID rewriting is avoided because factory chains use zero sentinels and the meaning of all references to nonzero chain IDs has not been established.

The returned `AttachedDevice` exposes `obj`, `path`, `patch`, `descriptors`, `dependencies`, `param_path(name)`, `descriptor(name)`, `bind(name, value, *, kind=None)`, and `attached[name]`. The latter produces a builder `Port` for modulation and remote assignment. Short names resolve either direct device controls such as `On` or `CONTENTS/MIX`; full relative paths address nested devices, Grid modules and modulators. Module/modulator segments use their stored names, which may differ from list positions. Ambiguous paths are rejected.

`attached.register_target(name, descriptor)` registers a known numeric, enum or boolean descriptor for an existing control and returns its `Port`. On a builder patch it also retains that descriptor in the patch's slot descriptor registry. `register_slot_targets(destination, slot=None, *, descriptors=None)` exposes existing slot controls as a dictionary of full host-relative paths to owned ports. It does not insert devices or change the graph. It retains registered definition descriptors and learns descriptors from both host-level routes/remotes and device-local routes/remotes, with host records taking precedence; explicit overrides are keyed by full target paths. A bare codec `Preset` has no builder descriptor cache, so missing descriptors must be supplied explicitly. Full target paths also disambiguate direct controls from similarly named contents parameters. This lets the coordinator register targets after `Patch.from_preset` without duplicating chains. Numeric targets retain the existing generic-descriptor fallback; other control types require a known descriptor before use.

`attached.bind(...)` edits an imported selector atomically, preserving references to the selector object inside the copied graph. Definition-derived selectors carry kind metadata; selectors imported from saved presets require explicit `kind=` for signal-kind validation. Pass a `Patch` destination to establish the port's patch ownership. When inserting into a bare `Preset`, the returned port has no builder patch owner. Slot ports are modulation/remote targets; core currently rejects them as signal cable outputs or modulation scale sources.

`slot_devices(device, slot)` returns the slot's device objects in order. `slot_targets(device, slot=None)` maps full host-relative paths to parameter objects. `slot_path(slot, index, parameter="", prefix="")` constructs the application path syntax:

```text
CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:CONTENTS/MIX
CONTENTS/PRE_FX/Chain/DEVICE_CHAIN/0:CONTENTS/KEY
CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:CONTENTS/INNER/Chain/DEVICE_CHAIN/0:CONTENTS/MIX
```

Appending avoids reindexing host routes. Device-internal routes remain relative to their own device, so they must **not** receive the host prefix. Descriptors learned from imported remotes/routings take precedence over the builder's numeric placeholder. Non-number targets require a learned or supplied descriptor; arbitrary objects are not treated as modulateable controls.

### Dependencies and attachments

Insertion unions device/module/modulator and packaged-file metadata, preserving unknown metadata. It also merges readable ZIP attachments. An attachment entry collision with different bytes, or an unsupported attachment, fails before the graph changes. Attachment paths and asset references remain unchanged; external sample files must still exist at the referenced location. This workstream preserves those references rather than relocating assets.

Use `Patch.slot(name, source)` for builder integration: it records `attached.dependencies` and reapplies them during finalization. Calling low-level `insert_slot(patch, ...)` directly bypasses that tracking; such callers must use `merge_dependencies(preset, attached.dependencies)` after `Patch.to_preset()`. Inserting into a bare `Preset` unions metadata immediately. File import preserves a source's complete device graph and attachment; definition import relies on installed native defaults.

## Integration example

```python
from pathlib import Path
from bwpreset.bindings import HardwarePort, SelectorKind
from bwpreset.build import Patch
from bwpreset.codec import serialize
from bwpreset.slots import Device

patch = Patch("Poly Grid", "Slotted patch")
effect = patch.slot("fx", Device.from_file("effect.bwpreset"))
lfo = patch.modulator("LFO/LFO", RATE=0.3)
lfo.route(effect["MIX"], 0.25)
patch.remote(effect["MIX"], "Effect mix")

# HW FX's installed definition names its selectors IN_ROUTING and OUT_ROUTING.
hardware = patch.slot("fx", Device.from_file("hardware-effect.bwpreset"))
hardware.bind(
    "IN_ROUTING",
    HardwarePort(
        "Interface input",
        "CoreAudio",
        "destination-machine-id",
        "input",
        "installed-device-id",
        ("installed-left-port-id", "installed-right-port-id"),
    ),
    kind=SelectorKind.HARDWARE_INPUT,
)

result = patch.to_preset()
# The coordinator chooses an explicit output path, outside the user library.
Path("output.bwpreset").write_bytes(serialize(result))
```

Use `OUT_ROUTING` with `HardwarePort(direction="output", ...)` for HW FX's hardware output. Grid HW/CV modules and HW CV In instead use `INPUT`/`OUTPUT` as listed above. Core's current ownership checks recognize `AttachedDevice` targets and reject targets from another patch. Lossless decompilation should retain the copied graph; authored-code decompilation must reconstruct imports before adding routes/remotes. These helpers do not modify core files.

## Evidence and validation

The mapping was checked against Bitwig 6.1.3's 11 installed selector definitions and Java registration classes. `experiments/bindings_probe.py` reproduces these checks without creating files:

```sh
uv run python -m experiments.bindings_probe --library "$BITWIG_LIBRARY" --jar "$BITWIG_JAR"
uv run python -m experiments.bindings_probe --preset example.bwpreset
uv run pytest tests/test_bindings.py tests/test_slots.py
uvx ruff check bwpreset/bindings.py bwpreset/slots.py tests/test_bindings.py tests/test_slots.py experiments/bindings_probe.py
```

The Java classes register source/destination wrappers (`cQL`, `E3I`) and fields `0x10ff`/`0x10fe`; device audio/note paths (`pc2`, `vHb`, `RDT`, `pqC`); track UUIDs (`qoW`, `rYW`); machine-specific input/output configurations (`Rsj`, `TLU`); and hardware interface/port fields (`Qpb`, `EI`, `aV`, `tH1`, `ekV`). `WqN`/`bZ` establish the audio bus enum values. These obfuscated class names are version-specific.

`cQL` declares its source as `EXM` (source-I/O-bus preset); `Rsj` inherits through `xqG` from `EXM`. This grounds hardware input payloads on audio sidechain wrappers in Java's accepted object hierarchy. Their live selector UI behavior remains unverified.

Legacy preferences mappings are registered by `C1w` and its parents `EI`/`Qpb`: class `0x45b`, preferences bus name `0x1d18`, bus UUID `0x7e8`, user label `0x7e6`, machine ID `0x7e7`, API name `0x11a1` and configuration ID `0x11a2`. The UUID registration has a null default. `eTa` compares preferences bus names and UUIDs as separate values; neither field is a channel index.

`Rsj` and `TLU` register ordered configuration lists at `0x1190` and `0x118d`. Their instance implementations (`Gug`/`T3I` and `Icp`/`aGa`) retain list order and support empty lists, indexed insertion and removal. Preset application in `acs.vhT(Gug)` and `par.vhT(Icp)` iterates and copies every configuration. This grounds whole-list preservation rather than a one-machine shortcut. Plug-and-play label and default-recording-input fields are registered by `aV`. Boolean target descriptor class `0xc6` is observed on Tool's installed boolean definition parameters.

A read-only scan of 9,531 installed-pack presets found 7,178 source wrappers, 20 destination wrappers, 234 device-audio sources, 31 device-note sources and six track-note sources. The first observed device-audio source used the application's `unsupported` bus value. Runtime inspection of Grid presets confirmed POST_FX and PRE_FX target paths. No installed-pack hardware payload was found; hardware construction is grounded in Java registration fields rather than a saved hardware fixture.

The scan also found 183 device-chain audio sources (`0x57e`, registered by `jba`) and four pre-fader track audio sources (`0x432`, registered by `Ez`). These inherit their path/UUID fields from the device/track source classes. The named `relative_to` and `pre_fader` options preserve those distinctions; they are not flattened to ordinary device or track sources. Device-chain note sources (`0x60e`, `GOK`) use the analogous Java inheritance.

Named-decoder validation covered all 7,198 selector wrappers in those 9,531 presets: 6,740 native defaults, 448 device/device-chain sources and ten track sources, with zero unsupported selector payloads in that installed-pack corpus. Legacy preferences and multi-machine hardware tests use synthetic graphs with the registered field layouts, including mixed legacy/plug-and-play profiles and preserved unknown fields after reordering. No proprietary hardware fixture is required.

Tests use synthetic graphs and temporary files. They cover selector kinds and direction, independent hardware labels, nested/shared references, sparse node names, preserved chain UUIDs, definition defaults, hardware-slot binding, dependency unions and attachment collisions. A synthetic builder integration check exercises selector setters, `Patch.slot`, modulation/remotes, dependency finalization and foreign-patch rejection. Serialization checks establish graph integrity, not successful audio/CV playback.

## Unresolved meanings and runtime checks

Null selector behavior remains context-dependent. Hardware APIs choose their own client/device/port IDs; numeric channel-to-ID lookup needs an actual installed interface configuration. Legacy preferences buses resolve against machine preferences, so their physical channel mapping cannot be derived from the bus name or UUID alone. Unknown source/destination or configuration classes remain explicit decoder errors; imported untouched graphs preserve them.

Live Bitwig loading, external hardware/CV playback and project-track resolution require coordinator verification. Hardware field identities are grounded in Java registrations, but actual backend IDs and empty optional-label behavior have no saved hardware fixture or live validation yet. Device-definition insertion relies on sparse native defaults; complex devices should use saved presets until that behavior is checked live. No running Bitwig project or user preset is changed by the probe or tests.
