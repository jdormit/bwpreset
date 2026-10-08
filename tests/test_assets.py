import io
import copy
import math
import struct
import wave
import zipfile

import pytest

from bwpreset.assets import (
    AudioFile,
    FileReference,
    Multisample,
    Sample,
    SamplePlayer,
    Wavetable,
    Zone,
    Asset,
    PreservedAsset,
    apply_asset,
    collect_dependencies,
    decode_asset,
    dependency_metadata,
)
from bwpreset.codec import Obj, Preset, parse, serialize
from bwpreset import model as m


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        w.writeframes(struct.pack("<80h", *range(80)))
    return out.getvalue()


def roundtrip(obj):
    preset = Preset("00010002000000000000000000000000000000", [], obj, 1)
    return parse(serialize(preset)).body


def test_audio_assignment_portable_reference_and_unknowns(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    sample = Sample(AudioFile.from_file(path, portable_path="samples/tone.wav"), root_key=48, loop_mode="loop")
    original = Obj(0x212, [(0x2B9, 8, "SAMPLE"), (0x74C, 10, None), (0x17CE, 9, Obj(0x67F, [(0x17CC, 13, b"browser")]))])
    result = apply_asset(original, sample)
    assert m.get(original, 0x74C) is None
    restored = decode_asset(roundtrip(result))
    assert restored.root_key == 48
    assert restored.loop_mode == "loop"
    assert restored.source.reference.portable_path == "samples/tone.wav"
    assert restored.source.duration == 0.01
    assert m.get(result, 0x17CE).cls == 0x67F
    deps = collect_dependencies(result)
    assert len(deps) == 1
    assert deps[0].portable_path == "samples/tone.wav"
    assert deps[0].source_path == str(path)
    assert deps[0].read_bytes() == wav_bytes()


@pytest.mark.parametrize("cls", [0xF38, 0x12AD])
def test_generated_wavetable_and_file_import(tmp_path, cls):
    table = Wavetable([[math.sin(2 * math.pi * i / 64) for i in range(64)], [0.5] * 64], name="Synthetic")
    path = tmp_path / "synthetic.wt"
    table.write(path)
    imported = Wavetable.from_file(path, portable_path="tables/synthetic.wt")
    parameter = Obj(cls, [(0x2B9, 8, "WAVETABLE"), (0x2C34, 10, None), (0x2C35, 10, None), (0x7FFF, 9, Obj(99, []))])
    result = roundtrip(apply_asset(parameter, imported))
    decoded = decode_asset(result)
    assert decoded.cycle_size == 64
    assert decoded.table_size == 2
    assert decoded.reference.portable_path == "tables/synthetic.wt"
    assert m.get(result, 0x7FFF).cls == 99
    assert imported.frames[0] == pytest.approx(table.frames[0], abs=1e-7)
    assert collect_dependencies(result)[0].read_bytes() == path.read_bytes()


def test_multisample_authoring_and_import(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    audio = AudioFile.from_file(path, portable_path="samples/tone.wav")
    multi = Multisample([Zone(Sample(audio, root_key=36), key_low=0, key_high=60), Zone(Sample(audio, root_key=72), key_low=61)])
    parameter = Obj(0x212, [(0x2B9, 8, "SAMPLE"), (0x74C, 10, None)])
    decoded = decode_asset(roundtrip(apply_asset(parameter, multi)))
    assert len(decoded.zones) == 2
    assert decoded.zones[1].key_low == 61
    assert decoded.zones[1].sample.root_key == 72
    assert len(collect_dependencies(apply_asset(parameter, multi))) == 1
    archive = tmp_path / "test.multisample"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("tone.wav", wav_bytes())
        z.writestr(
            "multisample.xml",
            '<multisample name="Test"><sample file="tone.wav" sample-stop="80"><key root="48" low="40" high="60"/><loop mode="loop" start="8" stop="72"/></sample></multisample>',
        )
    imported = Multisample.from_file(archive, portable_path="samples/test.multisample")
    result = roundtrip(apply_asset(parameter, imported))
    restored = decode_asset(result)
    assert restored.zones[0].sample.loop_start == 0.001
    assert restored.zones[0].sample.loop_end == 0.009
    assert restored.zones[0].sample.source.reference.sub_path == "tone.wav"
    assert collect_dependencies(result)[0].read_bytes() == archive.read_bytes()


def test_sampleplayer_named_modes_preserve_nested_unknowns():
    parameter = Obj(0x1622, [(0x2B9, 8, "SAMPLE_PLAYER"), (0x4194, 1, 0), (0x7FFF, 9, Obj(99, [(123, 13, b"state")]))])
    result = apply_asset(parameter, SamplePlayer(playback_mode="spectral", spectral_quality_mode="ultra", spectral_formant_processing=True))
    decoded = decode_asset(roundtrip(result))
    assert decoded.playback_mode == "spectral"
    assert decoded.spectral_quality_mode == "ultra"
    assert decoded.spectral_formant_processing is True
    assert m.get(result, 0x4194) == 3
    result2 = apply_asset(result, SamplePlayer(playback_mode="fragments", fragments_maximum_number_of_grains=128))
    assert m.get(result2, 0x4194) == 4
    assert m.get(result2, 0x7FFF).fields == [(123, 13, b"state")]
    assert decode_asset(result2).spectral_quality_mode == "ultra"


def test_lossless_decoded_reapply_and_edit(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    p = apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE")]), Sample(AudioFile.from_file(path)))
    resource = m.get(p, 0x74C)
    resource.fields.append((0x7FFF, 9, Obj(99, [(88, 13, b"unknown")])))
    decoded = decode_asset(roundtrip(p))
    rebuilt = apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE")]), decoded)
    assert serialize(Preset("00010002000000000000000000000000000000", [], rebuilt, 1)) == serialize(
        Preset("00010002000000000000000000000000000000", [], roundtrip(p), 1)
    )
    decoded.root_key = 24
    edited = apply_asset(p, decoded)
    assert m.get(m.get(edited, 0x74C), 0x75C) == 24
    assert m.get(m.get(edited, 0x74C), 0x7FFF).cls == 99


def test_validation(tmp_path):
    with pytest.raises(ValueError):
        Wavetable([[float("nan")] * 64])
    with pytest.raises(ValueError):
        SamplePlayer(playback_mode="guessed")
    with pytest.raises(ValueError):
        SamplePlayer(fragments_maximum_number_of_grains=0)
    with pytest.raises(ValueError):
        FileReference(portable_path="../escape.wav")
    with pytest.raises(ValueError):
        apply_asset(Obj(0x85, []), SamplePlayer())


def test_moving_decoded_assets_keeps_target_state(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    source = apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE"), (0x7FFF, 8, "source")]), Sample(AudioFile.from_file(path)))
    target = Obj(0x212, [(0x2B9, 8, "SAMPLE"), (0x7FFF, 8, "target")])
    result = apply_asset(target, decode_asset(source))
    assert m.get(result, 0x7FFF) == "target"
    table = Wavetable([[0.0] * 64], phase_mode="aligned")
    table.write(tmp_path / "table.wt")
    oscillator = apply_asset(Obj(0xF38, [(0x2B9, 8, "WAVETABLE")]), table)
    lfo = apply_asset(Obj(0x12AD, [(0x2B9, 8, "WAVETABLE"), (0x7FFF, 8, "lfo")]), decode_asset(oscillator))
    assert m.get(lfo, 0x2D3C) is None
    assert m.get(lfo, 0x7FFF) == "lfo"


def test_imported_multisample_zone_reorder_replace_and_source_edit(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    audio = AudioFile.from_file(path)
    original = Multisample([Zone(Sample(audio, root_key=36), key_high=60), Zone(Sample(audio, root_key=72), key_low=61)])
    wrapper = Obj(0x212, [(0x2B9, 8, "SAMPLE")])
    decoded = decode_asset(apply_asset(wrapper, original))
    decoded.zones.reverse()
    reordered = decode_asset(roundtrip(apply_asset(wrapper, decoded)))
    assert [z.sample.root_key for z in reordered.zones] == [72, 36]
    reordered.zones[0].sample = copy.deepcopy(reordered.zones[1].sample)
    replaced = decode_asset(roundtrip(apply_asset(wrapper, reordered)))
    assert replaced.zones[0].sample.root_key == 36
    reordered.zones[0] = copy.deepcopy(reordered.zones[1])
    assert decode_asset(apply_asset(wrapper, reordered)).zones[0].key_low == 0


def test_decoded_asset_repr_and_copy_preserve_analysis_and_remap_paths(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    p = apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE")]), Sample(AudioFile.from_file(path), keep_in_memory=True))
    analysis = m.get(m.get(p, 0x74C), 0x4112)
    assert analysis.cls == 0x15EB
    analysis.fields.append((0x4114, 9, Obj(0x15E2, [(0x7FFF, 13, b"unknown analysis")])))
    decoded = copy.deepcopy(decode_asset(roundtrip(p)))
    restored = eval(repr(decoded), {"Asset": Asset})
    assert restored.keep_in_memory is True
    restored.source.reference.portable_path = "samples/moved.wav"
    edited = apply_asset(p, restored)
    assert collect_dependencies(edited)[0].portable_path == "samples/moved.wav"
    assert m.get(m.get(m.get(edited, 0x74C), 0x4112), 0x4114).cls == 0x15E2


def test_imported_foreign_paths_and_unknown_player_values():
    item = Obj(
        0x4A9,
        [
            (
                0x129E,
                9,
                Obj(
                    0x4A6,
                    [
                        (0x512, 8, "tone.wav"),
                        (0xD3A, 8, r"samples\tone.wav"),
                        (0xD3B, 9, Obj(0x2A, [(0x3B, 8, r"C:\Samples\tone.wav")])),
                        (0xCD4, 8, "Vendor/Pack/samples/tone.wav"),
                    ],
                ),
            )
        ],
    )
    dep = collect_dependencies(item)[0]
    assert dep.source_path == r"C:\Samples\tone.wav"
    assert dep.portable_path == r"samples\tone.wav"
    assert dependency_metadata(item) == [("referenced_packaged_file_ids", 0x19, ["Vendor/Pack/samples/tone.wav"])]
    player = Obj(0x1622, [(0x2B9, 8, "SAMPLE_PLAYER"), (0x4194, 1, 99), (0x4199, 3, 1000)])
    decoded = decode_asset(player)
    decoded.playhead_freeze = True
    result = apply_asset(player, decoded)
    assert m.get(result, 0x4194) == 99
    assert m.get(result, 0x4199) == 1000
    assert m.get(result, 0x42E1) is True


def test_specialized_resources_are_preserved():
    for cls, fid in [(0x212, 0x74C), (0xF38, 0x2C34), (0x12AD, 0x2C34)]:
        p = Obj(cls, [(0x2B9, 8, "ASSET"), (fid, 9, Obj(0x7FFF, [(5, 13, b"specialized")]))])
        decoded = decode_asset(p)
        assert isinstance(decoded, PreservedAsset)
        assert Asset.from_bytes(decoded.to_bytes()).to_bytes() == decoded.to_bytes()
    missing = Obj(0x212, [(0x2B9, 8, "SAMPLE"), (0x74C, 9, Obj(0x210, [(0x748, 10, None)]))])
    assert isinstance(decode_asset(missing), PreservedAsset)


@pytest.mark.parametrize("flags,expected", [(4, 1.0), (12, 0.5)])
def test_wt_integer_scaling_matches_bitwig_reader(tmp_path, flags, expected):
    path = tmp_path / "int16.wt"
    path.write_bytes(struct.pack("<4sIHH64h", b"vawt", 64, 1, flags, *([16384] * 64)))
    assert Wavetable.from_file(path).frames[0][0] == expected


def test_legacy_layer_multisample_pingpong_and_zone_metadata(tmp_path):
    path = tmp_path / "old.multisample"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("tone.wav", wav_bytes())
        z.writestr(
            "multisample.xml",
            '<multisample name="Legacy"><layer name="Layer"><sample file="tone.wav" sample-stop="80" round-robin="1" parameter-1="0.5"><key track="true"/><velocity low="1" high="127" low-fade="4"/><loop mode="ping-pong" start="8" stop="72"/></sample></layer></multisample>',
        )
    imported = Multisample.from_file(path)
    assert imported.zones[0].zone_logic == "round_robin"
    assert imported.zones[0].velocity_low_fade == 4
    assert imported.zones[0].parameter_1 == 0.5
    assert imported.zones[0].sample.loop_mode == "ping_pong"
    assert imported.groups[0][0] == "Layer"


def test_portable_only_audio_reference(tmp_path):
    directory = tmp_path / "samples"
    directory.mkdir()
    (directory / "tone.wav").write_bytes(wav_bytes())
    audio = AudioFile(FileReference(portable_path="samples/tone.wav", size=len(wav_bytes())), sample_rate=8000, frames=80, channels=1)
    parameter = apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE")]), Sample(audio))
    restored = roundtrip(parameter)
    dependency = collect_dependencies(restored)[0]
    assert dependency.source_path is None
    assert dependency.read_bytes(base_directory=tmp_path) == wav_bytes()


def float_wav_bytes():
    payload = struct.pack("<64f", *([0.25] * 64))
    fmt = struct.pack("<HHIIHH", 3, 1, 44100, 176400, 4, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def test_float_wav_audio_and_wavetable_import(tmp_path):
    path = tmp_path / "float.wav"
    path.write_bytes(float_wav_bytes())
    assert AudioFile.from_file(path).frames == 64
    assert Wavetable.from_file(path, cycle_size=64).frames == ((0.25,) * 64,)
    path.write_bytes(float_wav_bytes()[:-1])
    with pytest.raises(ValueError, match="truncated"):
        AudioFile.from_file(path)


def test_legacy_sustain_and_outside_boundaries_are_imported(tmp_path):
    path = tmp_path / "old.multisample"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("tone.wav", wav_bytes())
        z.writestr(
            "multisample.xml",
            '<multisample><sample file="tone.wav" sample-stop="100"><key root="48"/><loop mode="sustain" start="0" stop="100"/></sample></multisample>',
        )
    imported = Multisample.from_file(path)
    assert imported.zones[0].sample.loop_mode == "loop"
    imported.zones[0].sample.root_key = 60
    assert imported.zones[0].sample.sample_end == 0.0125
    result = decode_asset(roundtrip(apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE")]), imported)))
    assert result.zones[0].sample.root_key == 60
    assert result.zones[0].sample.sample_end == 0.0125


def test_empty_wrapper_settings_survive_decompiler_spec():
    parameter = Obj(0xF38, [(0x2B9, 8, "WAVETABLE"), (0x2C34, 10, None), (0x2D3C, 1, 2)])
    decoded = Asset.from_bytes(decode_asset(parameter).to_bytes())
    target = Obj(0xF38, [(0x2B9, 8, "WAVETABLE"), (0x2C34, 10, None), (0x2D3C, 1, 1)])
    assert m.get(apply_asset(target, decoded), 0x2D3C) == 2


def test_edit_shared_archive_reference_has_no_orphan_items(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    audio = AudioFile.from_file(path, portable_path="samples/tone.wav")
    wrapper = Obj(0x212, [(0x2B9, 8, "SAMPLE")])
    p = apply_asset(wrapper, Multisample([Zone(Sample(audio)), Zone(Sample(audio))]))
    zones = m.get(m.get(p, 0x74C), 0x76D)
    items = [m.get(m.get(m.get(z, 0x76E), 0x4112), 0x748) for z in zones]
    shared = m.get(items[0], 0x129E)
    m.set_field(items[1], 0x129E, shared)
    m.set_field(shared, 0x129C, items)
    decoded = decode_asset(p)
    decoded.zones[0].sample.source.reference.portable_path = "samples/moved.wav"
    edited = roundtrip(apply_asset(wrapper, decoded))
    from bwpreset.codec import walk

    assert len([obj for obj in walk(edited) if obj.cls == 0x4A9]) == 2
    refs = [obj for obj in walk(edited) if obj.cls == 0x4A6]
    assert len(refs) == 2
    assert all(len(m.get(ref, 0x129C)) == 1 for ref in refs)


def test_repeated_imported_zone_and_group_unknown_fields(tmp_path):
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    sample = Sample(AudioFile.from_file(path))
    original = Multisample([Zone(sample, group=0), Zone(sample, group=0)], groups=[("Original", [0.0, 0.0, 0.0, 1.0])])
    wrapper = Obj(0x212, [(0x2B9, 8, "SAMPLE")])
    p = apply_asset(wrapper, original)
    group = m.get(m.get(p, 0x74C), 0xFAC)[0]
    group.fields.append((0x7FFF, 9, Obj(88, [])))
    decoded = decode_asset(p)
    decoded.zones[1] = decoded.zones[0]
    decoded.groups[0] = ("Renamed", [1.0, 0.0, 0.0, 1.0])
    result = roundtrip(apply_asset(wrapper, decoded))
    zones = m.get(m.get(result, 0x74C), 0x76D)
    assert zones[0] is not zones[1]
    assert m.get(m.get(m.get(result, 0x74C), 0xFAC)[0], 0x7FFF).cls == 88


def test_large_file_size_foreign_path_edit_and_audio_length_validation(tmp_path):
    item = Obj(
        0x4A9,
        [
            (
                0x129E,
                9,
                Obj(
                    0x4A6,
                    [
                        (0x512, 8, "tone.wav"),
                        (0xD3A, 8, r"samples\tone.wav"),
                        (0x17, 3, 100),
                        (0xD3B, 9, Obj(0x2A, [(0x3B, 8, r"C:\tone.wav")])),
                    ],
                ),
            )
        ],
    )
    reference = FileReference.from_object(item)
    reference.source_path = str(tmp_path / "tone.wav")
    reference.size = 2**31
    rebuilt = roundtrip(reference.to_object())
    assert m.get(rebuilt, 0x17) == 2**31
    assert m.get(rebuilt, 0xD3A) == r"samples\tone.wav"
    path = tmp_path / "tone.wav"
    path.write_bytes(wav_bytes())
    p = apply_asset(Obj(0x212, [(0x2B9, 8, "SAMPLE")]), Sample(AudioFile.from_file(path)))
    decoded = decode_asset(p)
    decoded.source.frames = 40
    with pytest.raises(ValueError, match="sample boundaries"):
        apply_asset(p, decoded)
