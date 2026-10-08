---
name: bitwig-grid
description: Author, inspect and edit Bitwig Studio Poly Grid, FX Grid and Note Grid patches using bwpreset. Use when asked to create Grid presets, wire modules, assign samples or wavetables, draw curves, configure modulation or convert existing Grid patches to code.
license: MIT
compatibility: Python 3.11+, uv and an installed Bitwig Studio; development targets Bitwig 6.1.3.
---

# Bitwig Grid authoring

Use `bwpreset` to write native `.bwpreset` files. Discover the local schema before choosing module or parameter names. A saved file or passing parser round trip is not proof of correct sound or timing.

## Setup and discovery

```sh
uv tool install git+https://github.com/jdormit/bwpreset
bwgrid --help
bwgrid list filter
bwgrid info "Envelope/Segments"
bwgrid info "LFO/LFO" --modulator
```

The package reads Bitwig's installed definitions; it does not bundle them. If discovery fails, set `BITWIG_HOME` to the application directory (or macOS app bundle), and optionally `BITWIG_LIBRARY_ROOT` to its `Library` resource directory. `BWGRID_CACHE_HOME` overrides the cache. `bwgrid refresh` rescans the installation. `BWGRID_CORPUS_PATHS` is an OS-path-separated list of extra library paths; an empty value restricts learning to app resources.

## Author a patch

Write a Python file defining a `Patch`, then run it through `bwgrid build` so imports use the installed tool's Python environment:

```python
from bwpreset.build import Curve, Patch
from bwpreset.units import Hz, Seconds

p = Patch("Poly Grid", "Moving pluck", voices=8, glide=Seconds(0))
osc = p.add("Oscillator/Sine", 0, 0)
filt = p.add("Filter/Low-pass", 4, 0, CUTOFF=Hz(1000))
env = p.add("Envelope/ADSR", 7, 0, ATTACK=Seconds(0.005), DECAY=Seconds(0.4), SUSTAIN=0.0)
out = p.add("I/O/Audio Out", 11, 0)
filt.connect(IN=osc)
env.connect(IN=filt)
out.connect(IN=env)
lfo = p.modulator("LFO/LFO", RATE=Hz(1))
lfo.state(per_voice=True, color="mint")
lfo.route(filt["CUTOFF"], 24.0)
p.remote(filt["CUTOFF"], "Cutoff")
```

```sh
bwgrid build patch.py --out ./presets
```

Drag the result into Bitwig, or give the user the exact output path. Do not overwrite an existing patch unless requested. Modules use Bitwig defaults for omitted parameters. Leave room between large modules; coordinates are Grid cells.

## Settings, curves and modulation

- `node.set(...)` changes parameters. Discover enum labels and input/output names with `bwgrid info`; avoid guessing numeric enum indices.
- `node.state(enabled=..., per_voice=..., color=..., width=..., height=...)` edits supported instance settings. Per-voice applies to modulators; dimensions apply to modules.
- `patch.configure(...)` changes voice count/stacking, mono mode, retrigger, note priority, glide and device parameters. True/Digi Mono differ in behavior when `voices=1`.
- Use `Seconds`, `Hz` and `Decibels` for physical values, or `Rate`/`Beats` for rate/timebase-aware authoring. A plain number means the stored value. Filter cutoff uses note-number units, so a modulation amount of `0.3` is only 0.3 semitones; `24` is two octaves. `normalized=True` uses a fraction of an authoritative numeric range.
- `node.route(target["PARAM"], amount, source=..., scale=..., mode=..., enabled=...)` creates modulation. If several modulation sources exist, specify `source`. `patch.route(...)` uses voice-stack spread.
- `Curve([(position, value[, outgoing_bend]), ...], sustain=..., loop_start=..., loop_end=..., playback=...)` authors curve data. Markers are point indices, repeated positions create vertical steps, and positions may exceed 1. Values are 0..1 and bends -1..1. `Curve.from_file(path)` imports a `.bwcurve`.

## Assets, sources and slots

Use the public helpers in [references/api.md](references/api.md). Write generated media into an explicit asset directory before assigning it. Portable-path metadata does not copy files; ship the referenced media with the preset.

Sidechain track UUIDs are project-specific; hardware IDs are machine-specific. Obtain them from the target environment or an existing correctly configured preset. Do not invent identifiers or assume a named port exists. `NoSource()` selects the native default and does not universally mean silence.

## Edit existing patches

```sh
bwgrid decompile existing.bwpreset --lossless
bwgrid dump existing.bwpreset
```

Use `Patch.from_preset(path)` for edits that must retain unknown fields, imported media state, device chains and attachments. Modules and modulators are available through `p.modules` and `p.modulators`. `delete`, `reindex`, `fragment` and `import_fragment` handle graph edits. External fragment references require explicit bindings; source selectors must not silently point at unrelated destination devices.

Lossless decompile output embeds the original preset and can contain private/proprietary data. Keep it local unless publication is requested and the contents are suitable for sharing.

## Verify and report

Generate small tests covering the authored behavior. Verify loading, geometry/inspector settings, and actual audio or downstream note output separately. For feedback, per-voice routing, looping and hardware, nonzero audio or a screenshot alone is insufficient.

The repository's `tools/live_verify.py` measures exported audio/notes and records blocked prerequisites. Its live runner requires controlled project fixtures and an adapter; it is not a ready-made DAW remote-control server. If UI access or hardware is unavailable, report that check as pending and give the user a concrete load/export check. Never claim live verification from serialization tests.
