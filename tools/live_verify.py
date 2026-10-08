"""Controlled Bitwig verification bundles; no live operations without explicit opt-in."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import uuid
import wave


DEFAULT_APP = Path("/Applications/Bitwig Studio.app")
ACCESSIBILITY_PROBE = 'tell application "System Events" to get UI elements enabled'


class Blocked(RuntimeError):
    pass


def execute(argv, timeout=30):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Blocked(str(error)) from error
    if result.returncode:
        raise Blocked(f"{argv[0]} exited {result.returncode}: {result.stderr.strip()}")
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prerequisites(app=DEFAULT_APP, *, probe_permissions=False):
    reasons = []
    version = None
    if platform.system() != "Darwin":
        reasons.append("Live automation requires macOS.")
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            version = plistlib.load(stream).get("CFBundleShortVersionString")
        if not str(version).startswith("6.1."):
            reasons.append(f"Expected Bitwig 6.1.x, found {version!r}.")
    except (OSError, plistlib.InvalidFileException) as error:
        reasons.append(f"Bitwig installation unavailable: {error}")
    for tool in ("open", "pgrep", "osascript", "screencapture"):
        if not shutil.which(tool):
            reasons.append(f"Required command unavailable: {tool}")
    installation_status = "blocked" if reasons else "ready"
    if not probe_permissions:
        reasons.append("Accessibility/Automation not probed; use --probe-permissions when live checks are permitted.")
    elif not reasons:
        try:
            result = execute(["osascript", "-e", ACCESSIBILITY_PROBE])
            if result.stdout.strip().lower() != "true":
                reasons.append("Accessibility blocked or unconfirmed: System Events did not report UI elements enabled.")
        except Blocked as error:
            reasons.append(f"Accessibility/Automation blocked: {error}")
    return {
        "status": "blocked" if reasons else "ready",
        "version": version,
        "installation_status": installation_status,
        "reasons": reasons,
        "screen_recording": "unverified; screenshots require human review even when capture succeeds",
        "live_verified": False,
    }


def read_pcm(path):
    try:
        with wave.open(str(path), "rb") as stream:
            channels, width, rate, frames, compression, _ = stream.getparams()
            raw = stream.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise ValueError(f"Unreadable/unsupported PCM WAV header; float, compressed or unsupported extensible WAV: {error}") from error
    if compression != "NONE" or width not in (1, 2, 3, 4):
        raise ValueError("Expected 8/16/24/32-bit integer PCM WAV")
    if not frames or len(raw) != frames * channels * width:
        raise ValueError("Empty or truncated PCM WAV")
    scale = 2 ** (8 * width - 1)
    if width == 1:
        samples = [(value - 128) / scale for value in raw]
    else:
        samples = [int.from_bytes(raw[i : i + width], "little", signed=True) / scale for i in range(0, len(raw), width)]
    return (channels, rate, frames), samples, scale


def measure_audio(path, reference=None):
    shape, samples, scale = read_pcm(Path(path))
    channels, rate, frames = shape
    metrics = {
        "channels": channels,
        "sample_rate": rate,
        "frames": frames,
        "duration_seconds": frames / rate,
        "peak": max(abs(value) for value in samples),
        "rms": math.sqrt(sum(value * value for value in samples) / len(samples)),
        "dc": sum(samples) / len(samples),
        "clipped_samples": sum(value <= -1 or value >= 1 - 1 / scale for value in samples),
        "channel_rms": [math.sqrt(sum(value * value for value in samples[c::channels]) / frames) for c in range(channels)],
    }
    if reference is not None:
        reference_shape, reference_samples, _ = read_pcm(Path(reference))
        if shape != reference_shape:
            raise ValueError("Audio comparison requires matching channels, sample rate and frame count; no implicit alignment")
        metrics["difference_rms"] = math.sqrt(sum((a - b) ** 2 for a, b in zip(samples, reference_samples)) / len(samples))
    return metrics


def validate_events(events):
    if not isinstance(events, list):
        raise ValueError("Notes must be an ordered JSON event array")
    previous = -1.0
    for event in events:
        if not isinstance(event, dict) or set(event) != {"time_seconds", "on", "channel", "key", "velocity"}:
            raise ValueError("Note events require time_seconds, on, channel, key and velocity")
        time = event["time_seconds"]
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time) or time < 0 or time < previous:
            raise ValueError("Note times must be finite, nonnegative and ordered")
        previous = time
        if type(event["on"]) is not bool:
            raise ValueError("Note on must be boolean")
        for field, maximum in (("channel", 15), ("key", 127), ("velocity", 127)):
            if type(event[field]) is not int or not 0 <= event[field] <= maximum:
                raise ValueError(f"Invalid MIDI {field}")
    return events


def measure_notes(path, expected, tolerance_seconds=0.01):
    if not math.isfinite(tolerance_seconds) or tolerance_seconds < 0:
        raise ValueError("Note tolerance must be finite and nonnegative")
    actual = validate_events(json.loads(Path(path).read_text()))
    validate_events(expected)
    matches = len(actual) == len(expected)
    max_error = 0.0
    for a, b in zip(actual, expected):
        error = abs(a["time_seconds"] - b["time_seconds"])
        max_error = max(max_error, error)
        matches &= error <= tolerance_seconds and all(a[key] == b[key] for key in ("on", "channel", "key", "velocity"))
    return {
        "matches": bool(matches),
        "event_count": len(actual),
        "expected_count": len(expected),
        "max_time_error_seconds": max_error,
        "tolerance_seconds": tolerance_seconds,
    }


def check_ranges(metrics, assertions):
    if not isinstance(assertions, dict) or not assertions:
        raise ValueError("At least one measurement assertion is required")
    passed = True
    for key, bounds in assertions.items():
        if key not in metrics or not isinstance(metrics[key], (int, float)):
            raise ValueError(f"unknown or nonscalar measurement assertion: {key}")
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in bounds)
            or bounds[0] > bounds[1]
        ):
            raise ValueError(f"Invalid inclusive range for {key}")
        passed &= bounds[0] <= metrics[key] <= bounds[1]
    return bool(passed)


def confined(root, relative):
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path must stay inside fixture: {relative}")
    if not path.is_file():
        raise ValueError(f"Missing fixture file: {relative}")
    return path


def validate_manifest(data, base):
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a JSON object")
    if data.get("schema_version") != 1:
        raise ValueError("Expected manifest schema_version 1")
    fixture = (base / data["fixture_dir"]).resolve()
    if not fixture.is_dir():
        raise ValueError("fixture_dir must be a controlled test project directory")
    project = confined(fixture, data["project"])
    if project.suffix != ".bwproject" or not data.get("project_name"):
        raise ValueError("A .bwproject and its observed project_name are required")
    probes = data.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ValueError("At least one probe is required")
    ids = set()
    for probe in probes:
        if not isinstance(probe, dict):
            raise ValueError("Each probe must be a JSON object")
        unknown = set(probe) - {"id", "preset", "reference", "audio", "notes", "tolerance_seconds", "screenshot", "manual", "metadata"}
        if unknown:
            raise ValueError(f"Unknown probe keys: {sorted(unknown)}")
        if "screenshot" in probe and type(probe["screenshot"]) is not bool:
            raise ValueError("screenshot must be boolean")
        if "manual" in probe and (not isinstance(probe["manual"], str) or not probe["manual"].strip()):
            raise ValueError("manual must be a nonempty observation description")
        name = probe["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or name in ids:
            raise ValueError("Probe IDs must be unique filename-safe names")
        ids.add(name)
        if "preset" in probe:
            if confined(fixture, probe["preset"]).suffix != ".bwpreset":
                raise ValueError("preset must be a .bwpreset")
        if "reference" in probe:
            confined(fixture, probe["reference"])
        if "audio" in probe:
            scalar_names = ("channels", "sample_rate", "frames", "duration_seconds", "peak", "rms", "dc", "clipped_samples")
            metrics = dict.fromkeys(scalar_names, 0)
            if "reference" in probe:
                metrics["difference_rms"] = 0
            check_ranges(metrics, probe["audio"])
        if "notes" in probe:
            validate_events(probe["notes"])
            tolerance = probe.get("tolerance_seconds", 0.01)
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("Note tolerance must be finite and nonnegative")
    adapter = data.get("adapter")
    if adapter is not None and (not isinstance(adapter, list) or not adapter or any(not isinstance(arg, str) for arg in adapter)):
        raise ValueError("adapter must be a nonempty argv array")
    return fixture


def adapter_call(command, operation, context_path, receipt_path):
    if not command:
        raise Blocked(f"No adapter configured for {operation}; use the manual runbook")
    if receipt_path.exists():
        raise Blocked("Adapter receipt already exists; use a fresh bundle")
    context = json.loads(context_path.read_text())
    execute([*command, "--operation", operation, "--context", str(context_path), "--receipt", str(receipt_path)], timeout=180)
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as error:
        raise Blocked(f"Missing/invalid adapter receipt: {error}") from error
    if not isinstance(receipt, dict) or any(receipt.get(key) != context[key] for key in ("run_id", "project_name")):
        raise Blocked("Adapter receipt identity does not match this run and project")
    if receipt.get("operation") != operation or receipt.get("status") != "ok":
        raise Blocked(f"Adapter did not confirm {operation}: {receipt.get('reason', receipt)}")
    return receipt


def bitwig_stopped():
    try:
        result = subprocess.run(["pgrep", "-x", "BitwigStudio"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Blocked(f"Cannot establish whether Bitwig is running: {error}") from error
    if result.returncode == 0:
        raise Blocked("Bitwig is already running; save and close the user's session manually before a controlled run")
    if result.returncode != 1:
        raise Blocked(f"Process check failed: {result.stderr}")


def run_bundle(manifest_path, output, *, app=DEFAULT_APP, allow_live=False, probe_permissions=False):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    data = json.loads(manifest_path.read_text())
    fixture = validate_manifest(data, manifest_path.parent)
    if output.is_relative_to(fixture) or fixture.is_relative_to(output):
        raise ValueError("Bundle and fixture directories must not contain one another")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "status": "blocked",
        "live_verified": False,
        "manifest_sha256": digest(manifest_path),
        "reasons": [],
        "results": [],
    }
    write_json(output / "manifest.json", data)
    try:
        if not allow_live or not probe_permissions:
            raise Blocked("Live operations disabled; use --allow-live --probe-permissions only in a permitted, closed test session")
        prereqs = prerequisites(app, probe_permissions=True)
        report["prerequisites"] = prereqs
        if prereqs["status"] != "ready":
            raise Blocked("; ".join(prereqs["reasons"]))
        bitwig_stopped()
        if not data.get("adapter"):
            raise Blocked("No adapter configured; project loading alone cannot verify preset placement or outputs")
        if not shutil.which(data["adapter"][0]):
            raise Blocked("Adapter executable unavailable")
        if any(path.is_symlink() for path in fixture.rglob("*")):
            raise Blocked("Controlled fixture must not contain symlinks to external project data")
        copied = output / "project"
        shutil.copytree(fixture, copied)
        report["input_sha256"] = {str(path.relative_to(copied)): digest(path) for path in copied.rglob("*") if path.is_file()}
        project_path = confined(copied, data["project"])
        execute(["open", "-a", str(app), str(project_path)])
        context = {"run_id": report["run_id"], "project_name": data["project_name"], "project": str(project_path)}
        context_path = output / "context.json"
        write_json(context_path, context)
        adapter_call(data["adapter"], "status", context_path, output / "status.json")
        probes_dir = output / "probes"
        probes_dir.mkdir()
        for probe in data["probes"]:
            probe_dir = probes_dir / probe["id"]
            probe_dir.mkdir()
            result = {"id": probe["id"], "status": "blocked", "measurements": {}, "artifacts": {}}
            report["results"].append(result)
            context.update(
                {
                    "probe": probe,
                    "output_dir": str(probe_dir),
                    "preset": str(confined(copied, probe["preset"])) if "preset" in probe else None,
                }
            )
            write_json(context_path, context)
            try:
                adapter_call(data["adapter"], "status", context_path, probe_dir / "status.json")
                if "preset" in probe:
                    adapter_call(data["adapter"], "load-preset", context_path, probe_dir / "load-preset.json")
                passed = True
                assertions = 0
                if "audio" in probe:
                    adapter_call(data["adapter"], "render", context_path, probe_dir / "render.json")
                    audio = probe_dir / "audio.wav"
                    if not audio.is_file():
                        raise Blocked("Render adapter did not produce audio.wav")
                    result["artifacts"]["audio.wav"] = digest(audio)
                    reference = confined(copied, probe["reference"]) if "reference" in probe else None
                    metrics = measure_audio(audio, reference)
                    result["measurements"]["audio"] = metrics
                    passed &= check_ranges(metrics, probe["audio"])
                    assertions += 1
                if "notes" in probe:
                    adapter_call(data["adapter"], "capture-notes", context_path, probe_dir / "capture-notes.json")
                    notes = probe_dir / "notes.json"
                    if not notes.is_file():
                        raise Blocked("Note adapter did not produce notes.json")
                    result["artifacts"]["notes.json"] = digest(notes)
                    metrics = measure_notes(notes, probe["notes"], probe.get("tolerance_seconds", 0.01))
                    result["measurements"]["notes"] = metrics
                    passed &= metrics["matches"]
                    assertions += 1
                if probe.get("screenshot"):
                    screenshot = probe_dir / "screen.png"
                    execute(["screencapture", "-x", str(screenshot)])
                    if not screenshot.is_file() or screenshot.stat().st_size < 8 or screenshot.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                        raise Blocked("Screenshot unavailable; check Screen Recording permission after restart")
                    result["artifacts"]["screen.png"] = digest(screenshot)
                if not assertions or probe.get("manual") or probe.get("screenshot"):
                    result["status"] = "manual_required" if passed else "fail"
                    result["reason"] = probe.get("manual", "Screenshot/load receipt requires human review; no output assertions supplied")
                else:
                    result["status"] = "pass" if passed else "fail"
            except Blocked as error:
                result["reason"] = str(error)
                break
            except (OSError, ValueError) as error:
                result.update(status="blocked", reason=f"Invalid or unavailable output evidence: {error}")
                break
            finally:
                write_json(output / "ledger.json", report)
        statuses = {result["status"] for result in report["results"]}
        report["status"] = (
            "fail"
            if "fail" in statuses
            else "blocked"
            if "blocked" in statuses
            else ("manual_required" if "manual_required" in statuses else "pass")
        )
    except Blocked as error:
        report["reasons"].append(str(error))
    except (OSError, ValueError) as error:
        report["status"] = "fail"
        report["reasons"].append(str(error))
    finally:
        attempted = {result["id"] for result in report["results"]}
        for probe in data["probes"]:
            if probe["id"] not in attempted:
                report["results"].append(
                    {
                        "id": probe["id"],
                        "status": "blocked",
                        "measurements": {},
                        "artifacts": {},
                        "reason": "Run prerequisites or previous stage blocked",
                    }
                )
        report["live_verified"] = report["status"] == "pass"
        write_json(output / "ledger.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Write a manifest template covering the outstanding verification ledger")
    init.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("check", help="Check installation; permission probe is opt-in")
    check.add_argument("--app", type=Path, default=DEFAULT_APP)
    check.add_argument("--probe-permissions", action="store_true")
    check.add_argument("--output", type=Path)
    run = sub.add_parser("run", help="Create a new bundle and optionally execute controlled live probes")
    run.add_argument("manifest", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--app", type=Path, default=DEFAULT_APP)
    run.add_argument("--allow-live", action="store_true")
    run.add_argument("--probe-permissions", action="store_true")
    audio = sub.add_parser("audio", help="Measure existing PCM WAV evidence without touching Bitwig")
    audio.add_argument("path", type=Path)
    audio.add_argument("--reference", type=Path)
    audio.add_argument("--expect", type=Path, help="JSON object of metric: [min, max]")
    notes = sub.add_parser("notes", help="Compare captured JSON MIDI events without touching Bitwig")
    notes.add_argument("path", type=Path)
    notes.add_argument("--expect", type=Path, required=True)
    notes.add_argument("--tolerance-seconds", type=float, default=0.01)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            observations = {
                "fx-grid": "Input audio reaches the effect graph and changes as designed.",
                "note-grid": "Output notes, gates, velocities and timing match the expected events.",
                "feedback": "Cables survive load and the measured response matches the feedback design.",
                "per-voice": "Overlapping notes receive independent modulation; global sources remain global.",
                "curve-markers": "Gate sustain/release and forward/ping-pong loops follow authored markers.",
                "assets": "Sample/wavetable dependencies resolve and survive save/reload.",
                "source-bindings": "Expected tracks/channels feed the patch; missing hardware remains identifiable.",
                "slot-devices": "Slot signal flow, modulation and remotes target the intended device controls.",
                "instance-state": "Enable, color, dimensions and voice-stack modes match authored state.",
            }
            template = {
                "schema_version": 1,
                "fixture_dir": "fixture",
                "project": "verification.bwproject",
                "project_name": "verification",
                "adapter": None,
                "probes": [{"id": name, "screenshot": True, "manual": observation} for name, observation in observations.items()],
            }
            with args.output.open("x") as stream:
                stream.write(json.dumps(template, indent=2) + "\n")
            report = {"status": "template", "live_verified": False, "manifest": str(args.output)}
        elif args.command == "check":
            report = prerequisites(args.app, probe_permissions=args.probe_permissions)
            if args.output:
                write_json(args.output, report)
        elif args.command == "run":
            report = run_bundle(
                args.manifest, args.output, app=args.app, allow_live=args.allow_live, probe_permissions=args.probe_permissions
            )
        elif args.command == "audio":
            metrics = measure_audio(args.path, args.reference)
            passed = check_ranges(metrics, json.loads(args.expect.read_text())) if args.expect else None
            report = {
                "status": "measured" if passed is None else "pass" if passed else "fail",
                "live_verified": False,
                "sha256": digest(args.path),
                "measurements": metrics,
            }
        else:
            metrics = measure_notes(args.path, json.loads(args.expect.read_text()), args.tolerance_seconds)
            report = {
                "status": "pass" if metrics["matches"] else "fail",
                "live_verified": False,
                "sha256": digest(args.path),
                "measurements": metrics,
            }
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {"status": "fail", "live_verified": False, "reasons": [str(error)]}
    print(json.dumps(report, indent=2, allow_nan=False))
    return 2 if report["status"] == "blocked" else 1 if report["status"] in ("fail", "manual_required") else 0


if __name__ == "__main__":
    sys.exit(main())
