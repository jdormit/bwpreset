# Grid verification

File serialization, public API behavior and live Bitwig behavior are separate checks. A passing object round trip does not establish signal routing, audio output or timing.

## Automated checks

Run `uv run pytest -q`, `uvx ruff check bwpreset tests experiments`, `uvx ruff format --check bwpreset tests experiments`, and `uv build`. Synthetic tests must run without a Bitwig installation. Corpus integration tests use the locally installed app and packs; proprietary definitions and audio are not distributed with this project.

## Live verification ledger

Previously confirmed in Bitwig 6.1.3:

- Generated Poly Grid patches load and play.
- Device voice count, stacking count, note priority, retrigger and common inspector fields display correctly.
- Module parameters, cables, device modulators and remote controls load correctly; a larger cutoff modulation amount produces an audible change.
- Steps displays a generated 16-step ramp.
- The newly recovered modules display correctly, including Bite, Heat, Howl, Rasp, Shred, Soar, Tuner, Segments and XY.
- Custom Segments curves display the expected asymmetric peak, vertical step and outgoing bend. Imported ADSR curve geometry also displays correctly.

Still requiring live checks for the completion effort:

| Check | Required observation |
| --- | --- |
| FX Grid | Input audio reaches the generated effect graph and its output changes as expected. |
| Note Grid | The generated graph emits the expected notes, gates, velocities and timing. |
| Feedback | The feedback graph loads and executes without losing cables; the measured behavior matches the patch design. |
| Per-voice modulation | Independently played notes receive independent modulation where configured; mono/global sources behave globally. |
| Curve markers | Sustain and release follow note gate state; forward/ping-pong loop modes and marker boundaries match the named options. |
| Assets | Generated/imported sample and wavetable content resolves and produces the expected signal; dependencies survive saving/reloading. |
| Source bindings | Selected tracks/channels feed the expected signals; unavailable project/hardware bindings remain identifiable. |
| Slot devices | A preset inserted into a Grid slot loads, receives the correct signal, and its modulation/remotes target the intended controls. |
| Instance state | Disabled modules/modulators, colors, resize state and voice-stack modes appear and behave as authored. |

## Accessibility blocker

On 2026-10-07, System Events denied `osascript` Accessibility access even after Jeremy enabled access for Emacs. Jeremy prefers to finish offline work before restarting Emacs. Do not report the remaining live checks as passed until the automation can run or Jeremy confirms them.
