import json
import math
import struct
import subprocess
import wave

import pytest

from tools import live_verify as live


def wav(path, values, width=2, rate=1000):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, width, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(int(value).to_bytes(width, "little", signed=True) for value in values))


def test_audio_measurements_and_comparison(tmp_path):
    signal = tmp_path / "signal.wav"
    reference = tmp_path / "reference.wav"
    wav(signal, [16384, -16384] * 500)
    wav(reference, [8192, -8192] * 500)
    result = live.measure_audio(signal, reference)
    assert result["duration_seconds"] == 1
    assert result["peak"] == 0.5
    assert result["rms"] == 0.5
    assert result["difference_rms"] == 0.25
    assert result["clipped_samples"] == 0


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_pcm_widths(tmp_path, width):
    path = tmp_path / "signal.wav"
    if width == 1:
        with wave.open(str(path), "wb") as output:
            output.setparams((1, 1, 1000, 0, "NONE", "not compressed"))
            output.writeframes(bytes([128, 192, 64]))
    else:
        wav(path, [0, 2 ** (width * 8 - 2), -(2 ** (width * 8 - 2))], width)
    assert live.measure_audio(path)["peak"] == 0.5


def test_mismatched_audio_is_not_compared(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    wav(a, [1, 2])
    wav(b, [1, 2, 3])
    with pytest.raises(ValueError, match="matching"):
        live.measure_audio(a, b)


def test_notes_compare_pitch_velocity_gate_and_time(tmp_path):
    path = tmp_path / "notes.json"
    events = [{"time_seconds": 0.1, "on": True, "channel": 0, "key": 60, "velocity": 100}]
    path.write_text(json.dumps(events))
    expected = [{**events[0], "time_seconds": 0.105}]
    assert live.measure_notes(path, expected, 0.01)["matches"]
    assert not live.measure_notes(path, expected, 0.001)["matches"]
    assert not live.measure_notes(path, [{**events[0], "velocity": 99}], 0.01)["matches"]
    path.write_text(json.dumps([{**events[0], "time_seconds": math.nan}]))
    with pytest.raises(ValueError):
        live.measure_notes(path, expected, 0.01)


def test_default_check_never_probes_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(live.subprocess, "run", lambda *a, **kw: pytest.fail("subprocess must not run"))
    report = live.prerequisites(tmp_path / "absent.app")
    assert report["status"] == "blocked"
    assert any("Accessibility" in reason for reason in report["reasons"])


def test_accessibility_denial_is_blocked(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    monkeypatch.setattr(live.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(live.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []

    def execute(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "osascript is not allowed assistive access (-1719)")

    monkeypatch.setattr(live.subprocess, "run", execute)
    report = live.prerequisites(app, probe_permissions=True)
    assert report["status"] == "blocked"
    assert "-1719" in " ".join(report["reasons"])
    assert len(calls) == 1


@pytest.mark.parametrize("reply, status", [("true\n", "ready"), ("false\n", "blocked"), ("", "blocked")])
def test_accessibility_flag_must_be_confirmed(tmp_path, monkeypatch, reply, status):
    app = fake_app(tmp_path)
    monkeypatch.setattr(live.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(live.shutil, "which", lambda name: name)
    monkeypatch.setattr(live.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, reply, ""))
    assert live.prerequisites(app, probe_permissions=True)["status"] == status


def fake_app(tmp_path):
    import plistlib

    app = tmp_path / "Bitwig.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "6.1.3"}))
    return app


def manifest(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "Test.bwproject").write_bytes(b"fixture")
    (fixture / "test.bwpreset").write_bytes(b"fixture")
    return {
        "schema_version": 1,
        "fixture_dir": "fixture",
        "project": "Test.bwproject",
        "project_name": "Test",
        "probes": [{"id": "fx", "preset": "test.bwpreset", "audio": {"rms": [0.01, 1]}}],
    }


def test_run_without_consent_writes_blocked_ledger(tmp_path, monkeypatch):
    data = manifest(tmp_path)
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data))
    monkeypatch.setattr(live.subprocess, "run", lambda *a, **kw: pytest.fail("live call"))
    output = tmp_path / "run"
    report = live.run_bundle(source, output)
    assert report["status"] == "blocked"
    assert report["results"][0]["status"] == "blocked"
    assert not report["live_verified"]
    assert json.loads((output / "ledger.json").read_text())["status"] == "blocked"


def test_running_bitwig_prevents_project_open(tmp_path, monkeypatch):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest(tmp_path)))
    monkeypatch.setattr(live, "prerequisites", lambda *a, **kw: {"status": "ready", "reasons": []})
    calls = []

    def execute(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "123\n", "")

    monkeypatch.setattr(live.subprocess, "run", execute)
    report = live.run_bundle(source, tmp_path / "run", allow_live=True, probe_permissions=True)
    assert report["status"] == "blocked"
    assert "already running" in " ".join(report["reasons"])
    assert not any(command[0] == "open" for command in calls)


def test_adapter_receipt_requires_fresh_identity(tmp_path, monkeypatch):
    context = {"run_id": "fresh", "project_name": "Test"}
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))

    def execute(argv, **kw):
        receipt = tmp_path / "receipt.json"
        receipt.write_text(json.dumps({"run_id": "old", "project_name": "Test", "status": "ok"}))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(live.subprocess, "run", execute)
    with pytest.raises(live.Blocked, match="identity"):
        live.adapter_call(["adapter"], "status", context_path, tmp_path / "receipt.json")


def test_empty_and_unknown_assertions_do_not_pass():
    with pytest.raises(ValueError, match="assertion"):
        live.check_ranges({"rms": 0.5}, {})
    with pytest.raises(ValueError, match="unknown"):
        live.check_ranges({"rms": 0.5}, {"invented": [0, 1]})
    assert not live.check_ranges({"rms": 0.5}, {"rms": [0, 0.1]})


def test_invalid_manifest_paths_are_rejected(tmp_path):
    data = manifest(tmp_path)
    data["project"] = "../outside.bwproject"
    with pytest.raises(ValueError, match="fixture"):
        live.validate_manifest(data, tmp_path)


def test_float_wav_rejected_explicitly(tmp_path):
    path = tmp_path / "float.wav"
    payload = struct.pack("<f", 0.5)
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 3, 1, 1000, 4000, 4, 32)
        + b"data"
        + struct.pack("<I", len(payload))
        + payload
    )
    with pytest.raises(ValueError, match="PCM"):
        live.measure_audio(path)


@pytest.mark.parametrize("outcome", ["pass", "fail", "blocked", "manual_required", "corrupt", "screenshot", "manual_fail"])
def test_mocked_live_pipeline_ledger(tmp_path, monkeypatch, outcome):
    data = manifest(tmp_path)
    data["adapter"] = ["mock-adapter"]
    if outcome in ("manual_required", "manual_fail"):
        data["probes"][0]["manual"] = "Inspect cables"
    if outcome == "screenshot":
        data["probes"][0]["screenshot"] = True
    if outcome == "blocked":
        data["probes"].append({"id": "next", "audio": {"rms": [0, 1]}})
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data))
    monkeypatch.setattr(live, "prerequisites", lambda *a, **kw: {"status": "ready", "reasons": [], "version": "6.1.3"})
    monkeypatch.setattr(live.shutil, "which", lambda name: name)
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "pgrep":
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "mock-adapter":
            operation = argv[argv.index("--operation") + 1]
            context = json.loads(live.Path(argv[argv.index("--context") + 1]).read_text())
            receipt_path = live.Path(argv[argv.index("--receipt") + 1])
            status = "blocked" if outcome == "blocked" and operation == "load-preset" else "ok"
            receipt_path.write_text(
                json.dumps(
                    {
                        "run_id": context["run_id"],
                        "project_name": context["project_name"],
                        "status": status,
                        "operation": operation,
                        "reason": "not available",
                    }
                )
            )
            if operation == "render":
                audio = live.Path(context["output_dir"]) / "audio.wav"
                if outcome == "corrupt":
                    audio.write_bytes(b"")
                else:
                    wav(audio, [0 if outcome in ("fail", "manual_fail") else 8192] * 1000)
        if argv[0] == "screencapture":
            live.Path(argv[-1]).write_bytes(b"\x89PNG\r\n\x1a\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(live.subprocess, "run", execute)
    output = tmp_path / "run"
    report = live.run_bundle(source, output, allow_live=True, probe_permissions=True)
    expected_status = {"corrupt": "blocked", "screenshot": "manual_required", "manual_fail": "fail"}.get(outcome, outcome)
    assert report["status"] == expected_status
    assert report["live_verified"] == (outcome == "pass")
    assert (output / "project/Test.bwproject").read_bytes() == b"fixture"
    assert report["input_sha256"]["test.bwpreset"] == live.digest(tmp_path / "fixture/test.bwpreset")
    assert json.loads((output / "ledger.json").read_text())["status"] == expected_status
    if outcome == "blocked":
        assert report["results"][1]["status"] == "blocked"
        assert not any("render" in command for command in calls)
    elif outcome != "corrupt":
        assert "audio.wav" in report["results"][0]["artifacts"]


def test_existing_bundle_is_never_reused(tmp_path):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest(tmp_path)))
    output = tmp_path / "run"
    output.mkdir()
    with pytest.raises(FileExistsError):
        live.run_bundle(source, output)


def test_negative_note_time_is_invalid(tmp_path):
    with pytest.raises(ValueError, match="nonnegative"):
        live.validate_events([{"time_seconds": -0.5, "on": True, "channel": 0, "key": 60, "velocity": 100}])


def test_template_contains_each_outstanding_category(tmp_path, capsys):
    target = tmp_path / "manifest.json"
    assert live.main(["init", "--output", str(target)]) == 0
    template = json.loads(target.read_text())
    assert len(template["probes"]) == 9
    assert all(probe["manual"] for probe in template["probes"])
    assert not json.loads(capsys.readouterr().out)["live_verified"]


def test_standalone_measurement_is_not_live_verification(tmp_path, capsys):
    path = tmp_path / "signal.wav"
    wav(path, [8192] * 1000)
    expected = tmp_path / "expect.json"
    expected.write_text(json.dumps({"rms": [0.2, 0.3]}))
    assert live.main(["audio", str(path), "--expect", str(expected)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert not report["live_verified"]


def test_stereo_measurement(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 1000, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack("<hhhh", 16384, 0, -16384, 0))
    metrics = live.measure_audio(path)
    assert metrics["channel_rms"] == [0.5, 0]
    assert metrics["rms"] == pytest.approx(math.sqrt(0.125))


@pytest.mark.parametrize("payload", [b"", b"RI", b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01"])
def test_truncated_wav_is_explicit_error(tmp_path, capsys, payload):
    path = tmp_path / "truncated.wav"
    path.write_bytes(payload)
    assert live.main(["audio", str(path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "PCM WAV" in report["reasons"][0]


@pytest.mark.parametrize("adapter", [None, ["missing-executable"]])
def test_missing_adapter_blocks_before_open(tmp_path, monkeypatch, adapter):
    data = manifest(tmp_path)
    data["adapter"] = adapter
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data))
    monkeypatch.setattr(live, "prerequisites", lambda *a, **kw: {"status": "ready", "reasons": []})
    monkeypatch.setattr(live, "bitwig_stopped", lambda: None)
    monkeypatch.setattr(live.shutil, "which", lambda name: None)
    monkeypatch.setattr(live.subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    report = live.run_bundle(source, tmp_path / "run", allow_live=True, probe_permissions=True)
    assert report["status"] == "blocked"
    assert "adapter" in " ".join(report["reasons"]).lower()


def test_unknown_probe_key_cannot_drop_manual_requirement(tmp_path):
    data = manifest(tmp_path)
    data["probes"][0]["manul"] = "Inspect geometry"
    with pytest.raises(ValueError, match="Unknown probe keys"):
        live.validate_manifest(data, tmp_path)


def test_array_manifest_reports_error(tmp_path, capsys):
    source = tmp_path / "manifest.json"
    source.write_text("[]")
    assert live.main(["run", str(source), "--output", str(tmp_path / "run")]) == 1
    assert "JSON object" in json.loads(capsys.readouterr().out)["reasons"][0]


def test_modified_context_cannot_validate_wrong_receipt(tmp_path, monkeypatch):
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps({"run_id": "fresh", "project_name": "Test"}))
    receipt_path = tmp_path / "receipt.json"

    def execute(argv, **kw):
        context_path.write_text(json.dumps({"run_id": "wrong", "project_name": "Other"}))
        receipt_path.write_text(json.dumps({"run_id": "wrong", "project_name": "Other", "status": "ok", "operation": "status"}))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(live.subprocess, "run", execute)
    with pytest.raises(live.Blocked, match="identity"):
        live.adapter_call(["adapter"], "status", context_path, receipt_path)


def test_fixture_symlink_blocks_before_open(tmp_path, monkeypatch):
    data = manifest(tmp_path)
    data["adapter"] = ["mock-adapter"]
    (tmp_path / "fixture/link").symlink_to(tmp_path / "fixture/test.bwpreset")
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data))
    monkeypatch.setattr(live, "prerequisites", lambda *a, **kw: {"status": "ready", "reasons": []})
    monkeypatch.setattr(live, "bitwig_stopped", lambda: None)
    monkeypatch.setattr(live.shutil, "which", lambda name: name)
    monkeypatch.setattr(live.subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    report = live.run_bundle(source, tmp_path / "run", allow_live=True, probe_permissions=True)
    assert report["status"] == "blocked"
    assert "symlinks" in report["reasons"][0]


def test_bundle_cannot_be_nested_in_fixture(tmp_path):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest(tmp_path)))
    with pytest.raises(ValueError, match="contain one another"):
        live.run_bundle(source, tmp_path / "fixture/run")


def test_difference_assertion_requires_reference(tmp_path):
    data = manifest(tmp_path)
    data["probes"][0]["audio"] = {"difference_rms": [0, 1]}
    with pytest.raises(ValueError, match="unknown"):
        live.validate_manifest(data, tmp_path)
