# Grid API

`Patch(device, name, **settings)` starts a Poly Grid, FX Grid or Note Grid from installed Bitwig defaults. `add` creates modules; `modulator` creates device-level modulators. `bwgrid list` and `bwgrid info` expose the locally discovered schema.

## Values and state

```python
from bwpreset.units import Seconds, Hz, Decibels, Rate, Beats

envelope.set(ATTACK=Seconds(0.01), RELEASE=Seconds(0.4))
filter.set(CUTOFF=Hz(1000))
lfo.set(TIMEBASE="Quarter note", RATE=Beats(2))
envelope.state(enabled=True, color="mint", width=8, height=3)
lfo.state(per_voice=True)
p.configure(voices=8, voice_stacking=2, mono_mode="digi", glide=Seconds(0.008))
```

Plain numbers use stored units. Physical wrappers require an authoritative descriptor. Timebase-aware rate conversions reject incompatible units; bar durations require explicit beats per bar. Legacy-rate physical conversion is not mapped, so retain its stored value when editing legacy presets. Validation rejects nonfinite values, incompatible types and known invalid ranges/enum members. It does not invent a bound where the installation supplies none.

## References and modulation

`module.out(name)` addresses a signal output. `node[parameter]` addresses a control or modulation source without writing a parameter value. Cables require outputs in the same patch; modulation/remotes require valid same-patch targets, and scale references require modulation sources.

```python
filter.connect(IN=oscillator)
lfo.route(filter["CUTOFF"], 24.0, mode="linear", enabled=True)
lfo.route(filter["CUTOFF"], 0.2, normalized=True)
p.remote(filter["CUTOFF"], "Cutoff", slot=0)
```

Amounts use target stored units. Normalized depth is a fraction of an authoritative numeric range. Named transfer functions are `linear`, `abs_linear`, `inv_abs_linear`, `cubic`, `inv_cubic`, `rectify_pos` and `rectify_neg`. `p.route` uses voice-stack spread. Remote pages have eight slots; `p.page(name)` starts a new page.

## Editing and fragments

```python
p = Patch.from_preset("existing.bwpreset")
p.modules[0].state(color="mint")
p.delete(p.modules[1])
p.reindex()
fragment = p.fragment(p.modules[0])
mapping = another_patch.import_fragment(fragment, x=4, y=2, bindings={...})
```

Imports copy the graph and retain unknown state, shared references, slot devices and attachments. Deleting nodes disconnects affected cables and removes their modulation/remotes before reindexing. Fragments require explicit bindings for external targets and device-local source selectors; the destination cannot silently inherit an unrelated slot's source.

`bwgrid decompile --lossless` emits an editable import of the complete preset. The compact readable mode reports unsupported or stale details, and automatically uses a lossless import when legacy representations cannot be faithfully reconstructed using current definitions. Use the explicit lossless mode for a complete editor round trip.

## Live acceptance

The APIs and serialization are covered by tests. Signal behavior, per-voice timing, hardware resolution and UI rendering require the live checks in [verification.md](verification.md). Generating a valid file is not equivalent to confirming its sound.
