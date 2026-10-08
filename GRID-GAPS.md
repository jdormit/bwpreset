# Grid authoring gaps

Status as of 2026-10-07, against Bitwig 6.1.3. This catalog covers authoring Poly Grid, FX Grid and Note Grid presets, including their modules, curves, modulators and remote controls. Device chains are included only where they belong to a Grid's slots.

**This is the original scope catalog.** The completion effort has implemented the named software APIs below; [completion-status.md](docs/completion-status.md) records their current evidence and outstanding live checks. The original limits are retained here so the acceptance scope stays reviewable.

The basic patch graph works: module placement, cables, scalar parameters, step data, embedded curves, modulation and remote controls. All 233 shipped Grid module definitions are discoverable. That establishes module availability, not complete control or live validation of every module.

## Missing functionality

| Gap | Current limit | What remains |
| --- | --- | --- |
| Sampler assets | Playback parameters such as pitch, speed and loop offsets are settable, but `SAMPLE` and `SAMPLE_PLAYER` are unsupported object values. | Assign audio/multisamples and configure the associated sample-player state. Track file dependencies in the generated preset. |
| Wavetables | Wavetable and Wavetable LFO controls are settable, but their wavetable payloads are unsupported. | Import/reference wavetable files and author the associated table data. This includes both Grid and device-level Wavetable LFOs. |
| External source bindings | Audio Sidechain, Pitch Quantize's note sidechain and audio/note-sidechain modulators have unsupported source selectors. HW/CV module availability does not provide an interface/channel selector. | Bind tracks, audio channels and hardware ports; distinguish portable preset settings from project- or machine-specific references. |
| Curve loop/sustain controls | Points, bends and file import work. Imported curve editor/loop metadata is retained as raw fields. | Map loop boundaries, sustain/release markers and curve mode into named options. Map the curve parameter wrapper's playback flags, which currently come from a template and are not exposed. |
| Modulator-specific state | Most modulators are learned from presets rather than their own definitions. HW CV In is the one shipped `.bwmodulator` type absent from the current catalog. | Learn modulator definitions directly. Expose per-voice/mono settings, enable state and other instance-level flags instead of inheriting them from a template. |
| Advanced modulation options | Routing amount, source, scale source and raw `mode` are supported. The meaning of `mode` is unmapped; routing enabled is always written as true. | Name the mode choices and expose routing flags. Preserve them during decompilation. |
| Voice-stack settings | Voice count, stacking count and spread modulation work. | Map remaining POLY fields, including `0x2901`, and audit the voice-stacking mode selector and other settings inherited from the skeleton. |
| Module presentation/state | Coordinates, user names and text parameters work. | Expose module color, instance active/enable flags, resizable dimensions and related instance state. These are not ordinary module parameters. |
| Grid FX slots | The builder empties device chains in Grid slots. The decompiler reports them as unsupported. | Attach existing presets/devices to the slots and target their parameters from Grid modulation or remote controls. General device authoring remains outside this scope. |

## Partial support and validation gaps

### Parameter semantics and units

The API usually accepts stored values and numeric enum indices. Confirmed display conversions include cubed envelope/glide times, note-number filter cutoff and percent values. There is no comprehensive seconds/Hz/dB conversion layer, enum-name setter or normalized modulation-depth API. Other units and scaling curves need mapping; LFO timebase-dependent rate behavior is not fully validated in the UI.

Validation mostly checks serialized value types. It does not consistently check parameter ranges, finite scalar values, voice counts or enum membership. Step arrays accept arbitrary lengths and values without module-specific validation.

### Module-specific payloads

The current unsupported schema objects are `SAMPLE`/`SAMPLE_PLAYER`, wavetable payloads and source selectors. Scalar definition conversion does not cover every atom class; it supplements parameters already learned from presets. Consequently, seeing every module in the catalog does not prove that every special setting is available.

Curve support uses the shared `CURVE` parameter representation. Segments has been visually checked. Scrawl, Curves, Slopes, Transfer and curve-based modulators need their own checks for mode, polarity and playback behavior. Curves with unrecognized point fields or object-valued settings are explicitly rejected/reported rather than silently simplified.

Steps has a confirmed rising-ramp example. Gates, Pitches, Probabilities and Accents need validation of their data conventions. Array/Recorder runtime behavior should be checked in Bitwig; their buffers should not be assumed to be editable preset data.

### Graph integrity and editing

The API does not validate ownership of references across patches or distinguish every parameter reference from an actual signal output. It has no explicit module deletion/reindexing API or reusable patch-fragment importer. Python functions can compose patches, but importing and editing an existing graph depends on the decompiler.

### Decompiler fidelity

The decompiler reproduces core graph structure but is not a complete Grid editor round trip. Known gaps include FX chains and asset/source payloads; module colors and dimensions; modulator/instance flags; routing enabled state; unmapped voice settings; and curve-wrapper flags. It also drops references identified as stale. Some instance state is inherited from templates instead of recovered from the original preset.

### End-to-end verification

Serialization and corpus checks are broader than live Bitwig checks. Poly Grid synthesis, modulation/remotes, step display and Segments geometry have been exercised. FX Grid processing, Note Grid note generation, external hardware, feedback patches, per-voice modulation and the remaining specialized modules still need representative playback checks. Screenshots confirm geometry, not audio or timing behavior.

## Suggested order

1. Map curve loop/sustain controls and instance-level inspector flags.
2. Add Sampler and wavetable assets, since their missing payloads block otherwise useful modules.
3. Add external source/hardware selectors and finish device-level modulator coverage.
4. Add unit-aware helpers and representative FX Grid/Note Grid/per-voice tests.
5. Complete presentation and decompiler fidelity, then Grid-slot preset insertion.

## Sources

- `bwpreset/build.py`: setters, routing, Grid skeletons and slot handling.
- `bwpreset/curves.py`: point and raw metadata support.
- `bwpreset/decompile.py`: reconstruction and unsupported-item handling.
- `bwpreset/definitions.py` and `schema.py`: definition conversion and learned types.
- `~/.cache/bwpreset/schema.json`: discovered parameter types.
- Bitwig's installed `.bwmodule` and `.bwmodulator` definitions.
- Jeremy's load reports and screenshots for the generated test presets.
