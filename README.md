# bwpreset

Write native Bitwig Studio Grid patches in Python. Build Poly Grid, FX Grid and Note Grid presets, or inspect and reconstruct existing patches.

Requires Python 3.11+ and an installed copy of Bitwig Studio. The schema is learned from your local Bitwig definitions and library; proprietary presets, definitions and samples are not bundled. Development targets Bitwig 6.1.3.

## Install

```sh
uv tool install git+https://github.com/jdormit/bwpreset
bwgrid list sine
bwgrid info "Envelope/ADSR"
```

## Build a patch

Save this as `pluck.py`:

```python
from bwpreset.build import Patch

p = Patch("Poly Grid", "Sine Pluck", voices=8, glide=0.0)
osc = p.add("Oscillator/Sine", 0, 0)
env = p.add("Envelope/ADSR", 4, 0, ATTACK=0.0, DECAY=0.8, SUSTAIN=0.0)
out = p.add("I/O/Audio Out", 8, 0)
env.connect(IN=osc)
out.connect(IN=env)
p.remote(env["DECAY"], "Decay")
```

```sh
bwgrid build pluck.py --out ./presets
```

Drag the resulting `.bwpreset` into Bitwig. Unspecified module parameters use Bitwig's defaults. `bwgrid info` describes stored values, ranges and enum choices.

## Agent skill

```sh
npx skills add jdormit/bwpreset --skill bitwig-grid
```

The skill teaches a coding agent to discover modules, author patches, inspect presets and verify results using the library.

## Inspect a preset

```sh
bwgrid decompile ./preset.bwpreset --lossless
bwgrid dump ./preset.bwpreset
bwgrid refresh
```

See the [completion ledger](docs/completion-status.md) for feature and live-verification status, and the [verification guide](docs/verification.md) for checks in Bitwig.

The library supports media assets, curve sustain/loops, instance state, source bindings, Grid-owned device slots, modulation, remote controls and graph editing. File-level behavior is tested; broader live audio/hardware acceptance is still in progress. See [core API](docs/core-api.md), [assets](docs/assets.md), [bindings/slots](docs/bindings-slots.md) and [curves/state](docs/curve-state-format.md).

## Development

```sh
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Synthetic tests run without Bitwig. Local corpus integration tests require an installation. MIT licensed.
