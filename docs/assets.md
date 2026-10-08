# Grid media assets

`bwpreset.assets` builds the resource graphs used by Sampler, Wavetable and Wavetable LFO. It supports new assignments and edits to imported presets, including Bitwig 6.1's sample-player mode state. Media stays in external files; the preset embeds its resource descriptors, playback settings and file references.

## Assign audio

```python
from bwpreset.assets import AudioFile, Sample, SamplePlayer

audio = AudioFile.from_file("assets/tone.wav", portable_path="samples/tone.wav")
sampler.set(
    SAMPLE=Sample(
        audio,
        root_key=48,
        sample_start=0.0,
        sample_end=audio.duration,
        loop_start=0.0,
        loop_end=audio.duration,
        loop_mode="loop",
        keep_in_memory=True,
    ),
    SAMPLE_PLAYER=SamplePlayer(
        playback_mode="fragments",
        fragments_maximum_number_of_grains=128,
        fragments_playhead_mode="random_direction",
    ),
)
```

`AudioFile.from_file` reads PCM or IEEE-float RIFF WAV metadata and checks the data length. It does not copy or modify the audio. A path passed directly as `SAMPLE` imports a WAV or a `.multisample`, selected by its suffix.

To reference a file without reading it, construct `AudioFile(FileReference(...), sample_rate=..., frames=..., channels=...)`. This works for package references and for other audio formats whose metadata you already know. WAV import supports PCM 8/16/24/32-bit and float 32/64-bit, including standard extensible format headers. Compressed audio, AIFF and RF64 do not have automatic metadata readers here.

Sample boundaries, loop boundaries and crossfade lengths are **seconds**. XML multisample boundaries are sample-frame positions; the importer converts them using each file's sample rate. Root keys are MIDI notes. Fine tuning and gain use the stored preset units. Sample options are:

| Options | Meaning |
| --- | --- |
| `root_key`, `keytrack`, `fine_tune`, `gain_db` | Resource tuning and level. |
| `sample_start`, `sample_end` | Playback boundaries within the audio. |
| `loop_start`, `loop_end`, `loop_crossfade_length` | Loop geometry within the playback boundaries. |
| `loop_mode`, `reverse` | `off`, `loop` or `ping_pong`; reverse is boolean. |
| `manual_bpm`, `use_analyzed_bpm`, `use_analyzed_root_f0` | Manual tempo and selection of Bitwig's analysis results. |
| `keep_in_memory` | The SAMPLE wrapper's RAM flag; `None` retains the target setting. |

Changing a decoded Sample's named options retains its unknown resource fields and analysis objects. Replacing its audio source creates a new analyzable reference, because the old analysis belongs to the old audio. Remapping the existing file reference retains the analysis.

## Bitwig 6.1 sample-player state

`SamplePlayer(**options)` is a partial update: unspecified settings retain their target values. Its public attributes can also be changed after decoding. New values are validated; unchanged imported values outside the known enum or range remain intact.

| Option | Accepted values |
| --- | --- |
| `playback_mode` | `repitch`, `textures`, `cycles`, `spectral`, `fragments` |
| `envelope_mode` | `ahdsr`, `one_shot` |
| `repitch_mode` | `clean`, `analog`, `digital` |
| `cycles_mode` | `bend`, `harmonics`, `phase_modulation` |
| `fragments_playhead_mode` | `sample_direction`, `playhead_direction`, `random_direction`, `alternate_direction` |
| `fragments_maximum_number_of_grains` | Integer 1..256 |
| `spectral_mode` | `clean`, `bend_harmonics` |
| `spectral_quality_mode` | `low`, `mid`, `high`, `ultra` |
| `spectral_bend_harmonics_mode` | `unquantized`, `octave_quantized`, `scale_quantized` |
| `spectral_onset_processing_threshold` | Finite stored numeric value |

The remaining options are boolean: `playhead_freeze`, `playhead_sync`, `repitch_digital_varirate`, `repitch_digital_imaging_filter`, `cycles_use_positional_fade`, `fragments_keep_grains_after_voice_ends`, `fragments_latch_initial_rate`, `fragments_repeat_at_initial_position`, `spectral_use_zero_latency_note_on`, `spectral_formant_processing`, `spectral_formant_root_coupling`, `spectral_onset_processing`, `spectral_apply_tonality_limit`, and `module_analysis_output`.

`textures` is the legacy granular mode, stored as 1; `fragments` is the newer triggered-grain mode, stored as 4. These are serialization names from Bitwig's enum, not UI-label guesses. Spectral is stored as 3, despite being the second Java enum member.

Pitch, speed, start/loop offsets, grain rate/size, spectral bend/tilt and other module controls are separate scalar parameters. Continue setting them through `Node.set`; `SamplePlayer` handles the settings inside the object-valued `SAMPLE_PLAYER` payload. It preserves imported analysis but does not synthesize Bitwig's onset, tone or spectral-analysis caches offline.

## Multisamples

```python
from bwpreset.assets import Multisample, Zone

multi = Multisample(
    [
        Zone(Sample(audio, root_key=36), key_low=0, key_high=60),
        Zone(Sample(audio, root_key=72), key_low=61, key_high=127),
    ],
    name="Two ranges",
)
sampler.set(SAMPLE=multi)

sampler.set(
    SAMPLE=Multisample.from_file(
        "assets/instrument.multisample",
        portable_path="samples/instrument.multisample",
    )
)
```

`Zone` supports key, velocity and select ranges and their low/high fades; `group` (`-1` means ungrouped); `zone_logic` (`always_play` or `round_robin`); and `parameter_1`, `parameter_2`, `parameter_3`. `Multisample` supports name, category, creator, description, keywords, groups and the RAM flag. Groups are `(name, [red, green, blue, alpha])` pairs, with color channels 0..1.

The ZIP importer reads `multisample.xml` and its WAV entries without extracting them. It supports current groups and legacy layers, boolean or numeric key tracking, per-zone tuning, fades, round robin and hyphenated XML loop modes. Legacy `sustain` imports as `loop`, matching Bitwig's XML reader. Existing boundaries outside the audio duration are retained rather than clamped; edits to tuning do not revalidate unrelated imported geometry. Audio references point to the archive plus each entry's `sub_path`, so the dependency is the original archive, not a directory of extracted files. Imported preset zones can be reordered, replaced and edited while retaining unknown fields and shared file references.

## Wavetables

```python
import math
from bwpreset.assets import Wavetable

table = Wavetable(
    [
        [math.sin(2 * math.pi * i / 2048) for i in range(2048)],
        [2 * i / 2048 - 1 for i in range(2048)],
    ],
    name="Sine to saw",
    phase_mode="original",
)
table.write("assets/sine-to-saw.wt")
oscillator.set(WAVETABLE=table)

lfo_table = Wavetable.from_file(
    "assets/sine-to-saw.wt",
    portable_path="tables/sine-to-saw.wt",
    enable_discontinuity=False,
)
grid_lfo.set(WAVETABLE=lfo_table)
device_lfo.set(WAVETABLE=lfo_table)
```

Frame arrays contain finite float32-representable samples, with equal lengths. Values may exceed ±1; Bitwig's own `.wt` reader supports those values. `write` emits a float32 `vawt` file and assigns its reference. The caller chooses the output location; assignment does not write files automatically. A decoded wavetable has dimensions and a reference, but `frames=None`: use `Wavetable.from_file` to load its data before generating another file.

`.wt` import supports the installed float32 and int16 variants, including the 6 dB difference between int16 flags 4 and 12. Mono WAV import takes an explicit `cycle_size`; its length must be a multiple of that size. Use `Wavetable.from_reference(FileReference(...), cycle_size=..., table_size=..., name=...)` when the source is not locally available.

Both oscillator and LFO wrappers support `disable_index_interpolation`. Oscillators additionally support `phase_mode` (`diffused`, `original`, `aligned`), `unison_model` (`flat`, `pyramid`, `prime`), `enable_unison_phase_spread`, `remove_dc` and `remove_f0`. Both Grid and device-level LFOs use the same payload class and support `enable_discontinuity`. Position, table modulation and scalar unison controls remain ordinary node parameters.

## Dependencies and portable paths

`FileReference` separates the local `source_path`, project-relative `portable_path`, package `package_path`, and archive `sub_path`. Authoring requires POSIX relative portable paths that stay within the destination directory. Imported paths are retained verbatim, including paths from other operating systems.

`collect_dependencies(root: Obj) -> list[Dependency]` walks the complete graph, including resources inside device slots. Each dependency exposes `source_path`, `portable_path`, `package_path`, and `read_bytes()`. Pass `base_directory=...` to `read_bytes` to resolve a project-relative reference from an explicit directory. Identical locations are deduplicated. Local references remain recoverable after serialize/parse; collection does not depend on an in-memory side table.

Portable paths describe where a caller should copy the media. They do not copy it or turn the preset into a self-contained archive. Resolve package paths through the installed package library, and resolve foreign-machine paths before attempting to read them. New file references omit checksums rather than inventing Bitwig's checksum algorithm.

`dependency_metadata(root)` returns the verified metadata entry `("referenced_packaged_file_ids", 0x19, [...])`. Merge those package IDs with existing metadata when integrating into `to_preset`. Local and project-relative paths belong in the body's file references; there is no invented local-dependency metadata key.

## Coordinator integration

`ASSET_PARAMETER_CLASSES` contains SAMPLE `0x212`, SAMPLE_PLAYER `0x1622`, oscillator WAVETABLE `0xf38`, LFO WAVETABLE `0x12ad`, and the common wavetable base `0xf0e`.

In `Node.set`, route those classes through `apply_asset(parameter: Obj, value) -> Obj` and replace the parameter with the returned object. It is an atomic copy operation; the input parameter is not mutated. It accepts the typed assets above, file paths for media imports, and `None` to clear SAMPLE/WAVETABLE. SAMPLE_PLAYER requires a `SamplePlayer` value.

In the decompiler, use `decode_asset(parameter)` and import `Asset`. Decoding returns a `Sample`, `Multisample`, `SamplePlayer`, `Wavetable`, an empty value, or a `PreservedAsset`. `repr(value)` emits executable `Asset.from_bytes(bytes.fromhex(...))`, which carries the original graph and its unknown nested fields. Typed decoded objects still expose named properties for editing. Moving a decoded wavetable from an oscillator to an LFO transfers its media and common settings; unchanged oscillator-only settings are omitted.

`SlicedSample(audio, [Slice(start_seconds, parameter_1=..., parameter_2=..., parameter_3=..., manual=True), ...])` authors and edits sliced resources. It supports manual/onset/pitch/equal/beat slicing modes, MIDI start/root keys, tuning, sample boundaries, detection thresholds, slice-duration behavior and chromatic/white/black/select/velocity assignment. Imported analysis and unknown fields are retained when the underlying audio is unchanged. These mappings come from Bitwig 6.1.3's `zt3`, `kqv`, `ce`, `cf` and `dA` registrations and stored enum values.

`PreservedAsset` handles missing references, future wavetable resources and empty wrappers with state. This fallback is separate from the named media-authoring APIs, which build actual resource graphs.

## Evidence and validation

The mappings come from the installed Bitwig 6.1.3 definitions, actual installed presets and `bitwig.jar`, rather than field-name inference:

| Source | Evidence |
| --- | --- |
| `Sampler.bwmodule`, `Sampler.bwdevice` | SAMPLE atom, sample-player atom and separate scalar controls. |
| `Wavetable.bwmodule`, both Wavetable LFO definitions | Common resource payload and oscillator/LFO-specific wrappers. |
| JVM `kB`, `PaU` | Single sample `0x210`; current analyzable reference field `0x4112`, class `0x15eb`, file-item field `0x748`; tuning, BPM and loop fields. New Samples use this 6.1 representation, while imported legacy `0x748` samples retain their original representation. |
| JVM `kkk`, `JKE` and related enums | SAMPLE_PLAYER `0x1622`, named settings and actual stored mode values; maximum-grain range 1..256. |
| JVM `wgS`, `FiU`, `cd` | Multisample `0x219`, zone `0x21a`, ranges, fades, group indices, zone parameters and round-robin values. |
| JVM `dhV`, `dia` and installed multisample XML | Archive metadata, range and playback attributes; legacy `sustain` maps to ordinary looping. |
| JVM `RiB`, `GwA`, `nsk`, `PoD`, `j3i`, `fZT`, `gQy` | Wavetable resource `0xf0d`, item `0xf03`, dimensions and wrapper settings. |
| JVM `hha`, `qXr` | File reference `0x4a6`, project path `0xd3a`, package path `0xcd4`, original location `0xd3b`, file item reference `0x129e`, archive subpath `0xfb8`. |
| JVM `dlK`, `sIL`, `sF`, `Uzn` | Little-endian `vawt` header; flag 4 selects int16, flag 8 selects ordinary int16 scaling. Without flag 8, the multiplier is 1/16384; with it, 1/32768. |

Run `uv run python -m experiments.assets_probe --bytecode` to inspect the registrations, or omit the flag for a read-only installed-preset audit. Synthetic tests cover WAV import, modern analyzable references, multisample archives, generated/imported tables, both LFO payload forms, portable references, mode edits, imported unknown fields and serialized reconstruction. No proprietary fixtures are required.

The installed audit covers 9,934 SAMPLE, 44 SAMPLE_PLAYER, 495 oscillator WAVETABLE and 61 LFO WAVETABLE parameters, with no decoded-reapply differences. It returns 5,718 typed Samples, 4,206 Multisamples, 555 Wavetables, 44 SamplePlayers and 11 preserved specialized/empty values. All 427 installed `.multisample` archives and 364 `.wt` files import. These are offline format checks. Accessibility is blocked, so new asset playback, path resolution in Bitwig and analysis-cache generation have not been live-verified.
