# Grid authoring completion status

Each row requires a public API, automated behavior checks and appropriate Bitwig evidence. “Implemented” and “live verified” are separate states.

| Area | Owner | Implementation | Live evidence |
| --- | --- | --- | --- |
| Sample/multisample assignment and sample-player state | Assets | Implemented, including manual sliced resources; tested | Pending |
| Wavetable oscillator and both Wavetable LFO payloads | Assets | Implemented; tested | Pending |
| Sidechain, note-source and hardware bindings | Bindings | Implemented, including legacy/multi-machine profiles; tested | Pending |
| Curve sustain/release/loop modes and wrapper flags | Curves/state | Integrated; tested | Pending |
| Module/modulator enable, per-voice, color and size | Curves/state | Integrated; tested | Pending |
| Remaining voice-stack/POLY settings | Curves/state | Named APIs mapped and tested | Pending |
| Named routing modes, scale and enable flags | Core | Implemented; tested | Pending |
| Grid-slot preset insertion and nested targets | Bindings/slots | Integrated; tested | Pending |
| Every shipped modulator and special atom discovery | Discovery | 233 module/43 modulator app definitions; special payloads integrated | App-only authoring checked offline |
| Unit conversions, enum names and value validation | Core | Physical wrappers and named enums implemented; see qualifications below | Pending |
| Step-data conventions and curve consumers | Core/discovery | Typed step data and shared curve API implemented | Steps/Segments previously checked; others pending |
| Reference ownership, editing and patch fragments | Core | Implemented; review regressions tested | Not applicable to pure graph operations |
| Decompiler fidelity and unknown-state preservation | Core | Lossless editing/CLI implemented; readable mode reports fidelity issues | File-level reconstruction checked |
| FX/Note/feedback/per-voice representative verification | Coordinator/live runner | Runner tested; adapter/fixtures pending | Accessibility blocked |
| Public-safe artifacts and standalone test execution | Coordinator | Artifact boundaries audited; clean export pending | Not applicable |
| Independent adversarial review | Coordinator | Two integrated reviews; findings addressed with regressions | Not applicable |
| Agent skill discovery and README | Coordinator | Written; npx skills discovers bitwig-grid | Not applicable |
| Public GitHub publication | Coordinator | Pending release checks | Not applicable |

The previous feature checks are recorded in [verification.md](verification.md). The detailed acceptance scope is [GRID-GAPS.md](../GRID-GAPS.md).

## Acceptance qualifications

The full definition of done is **not yet met**: new live audio, timing, hardware and UI checks remain pending. The software APIs must not be described as fully live-verified.

Physical conversion for imported `USE_LEGACY_RATE` presets is not mapped; retain stored rate values when editing those presets. Glide's physical conversion is confirmed, but an unknown upper bound is deliberately absent from the app-only descriptor. Other physical wrappers require authoritative descriptors rather than guessing units. Compressed audio/AIFF/RF64 can be assigned through explicit metadata references; automatic inspection currently supports WAV. Bitwig generates its own analysis caches.

Forward-format asset/curve variants outside the inspected schema retain opaque state through lossless import or are reported explicitly; that is preservation, not a promise of a named editor for unknown future data.
