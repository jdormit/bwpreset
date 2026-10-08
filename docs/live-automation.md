# Controlled live Grid verification

The live runner collects evidence for the outstanding checks in [verification.md](verification.md). Serialization tests establish file integrity; this runner measures output from a controlled Bitwig project. A screenshot or successful preset insertion does not establish audio, note timing, or correct inspector state.

**Current status: live verification remains blocked.** On 2026-10-07, System Events denied `osascript` Accessibility access after access was granted to Emacs, including after Jeremy restarted Emacs. The command host is `~/.opencode/bin/opencode`, launched by Emacs; its access still needs resolving. No new live checks have been claimed from this runner.

## Commands

Run from the repository root with Python 3.11 or later. These commands need no third-party Python packages.

```sh
# Offline installation check. Does not invoke System Events or capture the screen.
uv run python tools/live_verify.py check

# Generate a manifest with the nine outstanding verification categories.
uv run python tools/live_verify.py init --output /tmp/grid-manifest.json

# Build a blocked ledger without launching or interacting with Bitwig.
# Requires the manifest's fixture files to exist; each output directory must be new.
uv run python tools/live_verify.py run /tmp/grid-manifest.json --output /tmp/grid-offline-run

# Inspect manually exported evidence, without interacting with the app.
uv run python tools/live_verify.py audio /tmp/fx.wav --expect /tmp/audio-expect.json
uv run python tools/live_verify.py audio /tmp/fx.wav --reference /tmp/bypass.wav
uv run python tools/live_verify.py notes /tmp/notes.json --expect /tmp/expected-notes.json --tolerance-seconds 0.01
```

After Jeremy finishes offline work and restarts the host application, the live commands are:

```sh
uv run python tools/live_verify.py check --probe-permissions --output /tmp/grid-prerequisites.json
uv run python tools/live_verify.py run /tmp/grid-manifest.json \
  --output /tmp/grid-live-run --allow-live --probe-permissions
```

`--probe-permissions` reads System Events' `UI elements enabled` flag and requires `true`. A denied call, false flag or unrecognized reply is blocked; the runner does not change permission settings, retry through another host, or restart applications. The flag is a prerequisite, not a guarantee that every later UI operation is authorized; adapters must report actual denials. Screen Recording is separate and remains unverified even after capture. A successful capture still needs human review to confirm that it shows Bitwig and the intended patch.

The live runner requires Bitwig 6.1.x on macOS, `open`, `pgrep`, `osascript` and `screencapture`. It refuses to open a project when the `BitwigStudio` process is already running. Jeremy must save and close the existing session manually first. It opens a **copy of the supplied fixture directory** using Launch Services, then requires an adapter to confirm the expected active project before loading presets or producing output. It leaves the controlled test project open for review; it does not close Bitwig or change application settings.

Exit codes are `0` for ready, measured, template or pass; `1` for failed assertions, invalid inputs or manual-required results; and `2` for blocked prerequisites/evidence. The default offline check reports `installation_status` separately and remains blocked for live work until permissions are probed. `ready` means the checked prerequisites are available, not that live probes passed.

## Fixture and manifest

Prepare a small, dedicated project directory containing a `.bwproject`, generated presets, source audio, note clips and reference outputs. Keep relative asset paths inside that directory. The runner copies the whole directory, rejects symlinks, and records SHA-256 hashes of its input files. Do not point `fixture_dir` at a personal project library. Avoid absolute external media references: copying a project does not make those dependencies portable.

The template starts with manual observations. Connect one representative probe at a time:

```json
{
  "schema_version": 1,
  "fixture_dir": "fixture",
  "project": "verification.bwproject",
  "project_name": "verification",
  "adapter": ["python3", "/path/to/controlled_bitwig_adapter.py"],
  "probes": [
    {
      "id": "fx-grid",
      "preset": "fx-probe.bwpreset",
      "reference": "bypass.wav",
      "audio": {
        "duration_seconds": [3.99, 4.01],
        "rms": [0.02, 0.8],
        "difference_rms": [0.01, 1.0],
        "clipped_samples": [0, 0]
      },
      "screenshot": true,
      "manual": "Confirm the expected graph, cables and inspector fields after loading."
    },
    {
      "id": "note-grid",
      "preset": "note-probe.bwpreset",
      "notes": [
        {"time_seconds": 0.0, "on": true, "channel": 0, "key": 60, "velocity": 100},
        {"time_seconds": 0.25, "on": false, "channel": 0, "key": 60, "velocity": 0}
      ],
      "tolerance_seconds": 0.01
    }
  ]
}
```

Paths for `project`, `preset` and `reference` must resolve to existing files inside `fixture_dir`. `fixture_dir` is relative to the manifest directory, or absolute. Probe IDs are unique filename-safe names. Unknown probe keys are rejected; optional `metadata` can hold feature-specific provenance and adapter configuration. The adapter argv is invoked without a shell, from the runner's working directory; use absolute paths for adapter files.

`audio` maps scalar measurement names to inclusive `[minimum, maximum]` ranges. Supported values are `channels`, `sample_rate`, `frames`, `duration_seconds`, `peak`, `rms`, `dc`, `clipped_samples` and, with a reference, `difference_rms`. Measurements also include `channel_rms`, for human interpretation. Amplitudes are linear full scale; silence has RMS zero. WAV input must be integer PCM, 8/16/24/32-bit. Export float/compressed audio as integer PCM before measuring it. Python 3.11 reads classic PCM headers; integer `WAVE_FORMAT_EXTENSIBLE` headers need Python 3.12 or later. Unsupported/malformed evidence blocks the probe rather than establishing a feature failure. Difference measurements require exactly matching channels, rate and frame count; the runner does not align, resample or normalize files.

Notes are an ordered JSON event array. Times are nonnegative seconds relative to a documented capture origin, channels are zero-based `0..15`, keys and velocities are integers `0..127`, and `on` is boolean. All fields, event count and ordering must match, with only time allowed a tolerance. For simultaneous chord events, define a canonical order (for example, channel/key/on) shared by the fixture and capture adapter; near-simultaneous events with changed ordering do not match. Include note-off events to check gate duration. Agree on note-off velocity conventions with the capture adapter. For a controller observer's normalized float velocity, explicitly convert using `round(value * 127)` only after verifying its scale, preserve the raw observations, and record the conversion in the receipt. This conversion cannot establish velocity precision beyond the observer's resolution. Adjusting capture events to make them match invalidates the evidence.

## Adapter contract and grounded automation

**The runner includes an adapter interface, not an installed controller extension or a tested UI driver.** Missing adapters block the run before project launch. No render menu labels, keyboard shortcuts or coordinates are assumed.

Each adapter invocation receives:

```text
<adapter argv> --operation <operation> --context <context.json> --receipt <receipt.json>
```

The context contains a fresh `run_id`, expected `project_name`, copied `project` path and, for each probe, its manifest object, absolute copied `preset` path (or null), and fresh `output_dir`. The adapter must write a JSON receipt only after the operation completes:

```json
{
  "run_id": "the exact context run_id",
  "project_name": "the observed active project name",
  "operation": "load-preset",
  "status": "ok"
}
```

Use `status: "blocked"` with a `reason` for unavailable capabilities. Nonzero exit, timeout, missing receipts, wrong project/run identity or any non-ok receipt stops the remaining probes. The runner checks receipts, not the adapter's internal truthfulness. The adapter must recheck project and track identity immediately before every mutation; echoing the expected project name is not a valid observation. Project name alone is not globally unique. Also establish the controlled track/device identity or a project-specific fixture marker, and refuse ambiguous targets. A second Bitwig instance or concurrent manual project switch makes a run invalid.

The operations are:

| Operation | Required behavior |
| --- | --- |
| `status` | Wait for the copied project to finish loading, observe project identity, and confirm the dedicated target track/device. No project mutations. |
| `load-preset` | Replace only the intended test device with the copied preset. Wait for device observers to confirm the loaded identity and parameters. |
| `render` | Reset to the fixture's known transport range and stimulus. Produce `output_dir/audio.wav`, wait for export completion, then stop transport. Record range, rate, format, device and export method in the receipt. |
| `capture-notes` | Capture the graph's actual downstream output into `output_dir/notes.json`, document capture origin/resolution, and stop transport. Do not capture only the input stimulus. |

Operations have a 180-second subprocess timeout. An adapter should implement a shorter internal deadline and stop transport/release held notes when it fails. A timed-out adapter does not guarantee that Bitwig stopped its operation; inspect the test project before resuming. Use a new bundle for every run.

Offline sources in the installed Bitwig application establish these hooks:

- `Contents/Info.plist` declares `.bwproject` and `.bwpreset` document types and the `BitwigStudio` executable. The runner uses `open -a <app> <copied-project>` for project opening. Launch success is not treated as load confirmation.
- `Contents/Resources/Documentation/control-surface/api/com/bitwig/extension/controller/api/Application.html` documents `projectName()`, a `StringValue`. Subscribe before reading it and allow asynchronous observer updates.
- `Device.html` documents `replaceDeviceInsertionPoint()`. `InsertionPoint.html` documents `insertFile(String)`. This can insert a preset at a controlled device target, but the API explicitly says an impossible insertion does nothing. A returned call is not evidence that the preset loaded.
- `Track.html` documents `addNoteSource(NoteInput)` for routing a note source directly to a track regardless of monitoring. `Channel.html` documents `playingNotes()` and the deprecated `addNoteObserver`, whose callback reports on/off, key and floating-point velocity. Those APIs do not establish that observations occur downstream of Note Grid or provide sample-accurate MIDI timestamps. Validate the capture point; prefer a downstream recorder/MIDI capture for timing checks.

A controller adapter needs an explicitly installed extension and a transport to receive commands and send receipts. That installation is a separate, user-controlled action after offline work. A UI adapter can be added after restart by inspecting the actual Accessibility hierarchy and export dialog. Store observed control identifiers and app-version evidence rather than guessing menu paths. No controller render/export entry point was established by this offline inspection; manual export remains the fallback.

## Ledger and evidence

A run creates `manifest.json` and `ledger.json`. Once installation, permissions, closed-session, adapter and fixture checks pass, it also creates a copied `project/`, `context.json`, input hashes, adapter receipts and `probes/<id>/` evidence directories. Each result records `pass`, `fail`, `blocked` or `manual_required`, measurements, reasons and artifact hashes. Missing/skipped probes remain blocked. Reusing an output directory is rejected, so old renders cannot satisfy a new run accidentally.

A `pass` means the supplied output assertions passed in that controlled run. It does not prove every behavior of that feature. A probe with `manual` or `screenshot`, or one with only load evidence, stays `manual_required` even if its automated measurements pass. Screen Recording permission stays unverified: a valid PNG can contain only wallpaper, and the command captures the main display, which may not contain Bitwig. The top-level `live_verified` is true only when every probe passes; standalone `audio` and `notes` measurements always report false because their provenance is supplied externally. Human review of inspector state and listening remains separate evidence. Record reviewer, date, Bitwig version, project/preset hashes, expected observation and evidence paths alongside the ledger; don't edit an automated result to suggest the runner performed that review.

## Additional artifacts needed from feature owners

For each feature, supply a generated preset, a dedicated fixture project, stimulus and explicit expected output. Provide the API call that generated the preset and its authored settings as provenance. Assets need small distributable test samples/tables and a save/reload comparison. Bindings need named fixture tracks and a description of hardware-dependent conditions. Curve modes, voice stacking and per-voice routing need overlapping-note sequences and measurable time windows; whole-file RMS alone is too weak. Specialized step modules and feedback need known sequences/impulse responses, with stable stochastic seeds where available. Graph/decompiler checks need an authored baseline and a rebuilt preset, compared under the same stimulus.

The runner checks basic WAV statistics, matched-file differences and explicit note-event sequences automatically once connected. Feature-specific spectral, envelope, per-voice, latency or routing analyses should be implemented in a dedicated adapter/probe and accompanied by reviewable raw output. Do not infer those properties from nonzero RMS. Screenshots, hardware identity, missing-source labels, cable/inspector state and listening judgments require manual observation with this runner.

## Manual fallback

After offline work and the deferred restart, save and close the existing project. Open a disposable copy of the fixture manually. Verify the project, target track and stimulus before loading each preset. Capture inspector and graph views, play the known stimulus, and export integer PCM audio or record downstream MIDI. Use the offline `audio`/`notes` commands above to measure exports. Save and reload the copied project when persistence is part of the claim.

| Category | Manual evidence to collect |
| --- | --- |
| FX Grid | Dry and processed renders of the same known input, correct routing and graph screenshot. |
| Note Grid | Downstream note-on/off, pitch, velocity and channel capture against the authored schedule. |
| Feedback | Cable survival and bounded impulse/step response; inspect stability over the intended duration. |
| Per-voice modulation | Overlapping voices with separable pitches; compare independent modulation and mono/global control cases. |
| Curve markers | Gate-held/released sequences, marker screenshot and time-windowed output for forward/ping-pong loops. |
| Assets | Resolved sample/table identity, expected sound, dependency files and unchanged behavior after save/reload. |
| Source bindings | Distinct signals from named tracks/channels; expected unavailable-source indication with missing hardware. Hardware/CV needs the actual ports and loopback measurement. |
| Slot devices | Loaded slot identity, correct incoming/output signal, and separate modulation/remote-control target checks. |
| Instance state | Enabled/disabled behavior, colors, dimensions and voice-stack mode displayed as authored. |

For units and specialized modules, record displayed values plus signal behavior at named boundary cases. For decompiler fidelity, compare generated and rebuilt versions in the same copied fixture. Leave unavailable hardware/project-specific checks blocked. The acceptance ledger in `verification.md` remains unresolved until these observations exist.
