import pytest

from bwpreset import model as m
from bwpreset.assets import AudioFile, FileReference, Multisample, Sample, Slice, SlicedSample, Zone, apply_asset, decode_asset
from bwpreset.bindings import DeviceSource, NoSource, apply_binding
from bwpreset.build import Patch, PatchError
from bwpreset.codec import Obj
from test_core_completion import catalog as catalog


def audio():
    return AudioFile(FileReference(source_path="tone.wav"), sample_rate=48000, frames=48000, channels=1)


def test_sparse_imported_parameter_can_be_set(catalog):
    p = Patch("Test Grid", "sparse")
    n = p.add("Test", 0, 0, TIME=0.5)
    n.used["TIME"].fields = [(m.F_NAME, 8, "TIME")]
    edited = Patch.from_preset(p.to_preset())
    edited.modules[0].set(TIME=0.7)
    assert m.param_value(edited.modules[0].used["TIME"]) == 0.7


def test_object_parameter_edit_keeps_shared_identity(catalog):
    p = Patch("Test Grid", "shared")
    n = p.add("Test", 0, 0)
    selector = apply_binding(Obj(0x37C, [(m.F_NAME, 8, "SOURCE")]), DeviceSource("AUDIO_INPUT"))
    m.params(n.obj).append(selector)
    n.obj.fields.append((88888, 9, selector))
    edited = Patch.from_preset(p.to_preset())
    edited.modules[0].set(SOURCE=NoSource())
    assert edited.modules[0].used["SOURCE"] is m.get(edited.modules[0].obj, 88888)
    assert m.get(m.get(edited.modules[0].obj, 88888), 0x10FF) is None


def test_fragment_device_selector_requires_explicit_rebinding(catalog):
    p = Patch("Test Grid", "source")
    n = p.add("Test", 0, 0)
    path = "CONTENTS/POST_FX/Chain/DEVICE_CHAIN/0:AUDIO_OUTPUT"
    selector = apply_binding(Obj(0x37C, [(m.F_NAME, 8, "SOURCE")]), DeviceSource(path))
    m.params(n.obj).append(selector)
    imported = Patch.from_preset(p.to_preset())
    fragment = imported.fragment(imported.modules[0])
    target = Patch("Test Grid", "destination")
    with pytest.raises(PatchError, match="selector"):
        target.import_fragment(fragment)
    assert target.modules == []
    target.import_fragment(fragment, bindings={path: NoSource()})
    assert m.get(target.modules[0].used["SOURCE"], 0x10FF) is None


def test_sliced_sample_path_remap_keeps_analysis():
    obj = SlicedSample(audio(), [Slice(0)]).parameter_object()
    analyzer = m.get(m.get(obj, 0x74C), 0x4112)
    analyzer.fields.append((99999, 8, "retained analysis"))
    value = decode_asset(obj)
    value.source.reference.portable_path = "media/tone.wav"
    rebuilt = apply_asset(obj, value)
    assert m.get(m.get(m.get(rebuilt, 0x74C), 0x4112), 99999) == "retained analysis"


def test_multisample_edit_checks_new_group_assignments():
    obj = Multisample([Zone(Sample(audio()))], groups=[("one", [1, 0, 0, 1])]).parameter_object()
    value = decode_asset(obj)
    value.zones[0].group = 8
    with pytest.raises(ValueError, match="group"):
        value.to_object()


def test_device_definition_invalidates_fingerprint(monkeypatch, tmp_path):
    from bwpreset import schema

    monkeypatch.setattr(schema, "CORPUS_DIRS", [tmp_path])
    path = tmp_path / "Test.bwdevice"
    path.write_bytes(b"first")
    first = schema.fingerprint()
    path.write_bytes(b"edited definition")
    assert schema.fingerprint() != first


def test_object_edit_preserves_back_reference_to_owner(catalog):
    p = Patch("Test Grid", "backref")
    n = p.add("Test", 0, 0)
    selector = apply_binding(Obj(0x37C, [(m.F_NAME, 8, "SOURCE")]), DeviceSource("AUDIO_INPUT"))
    selector.fields.append((88888, 9, n.obj))
    m.params(n.obj).append(selector)
    edited = Patch.from_preset(p.to_preset())
    edited.modules[0].set(SOURCE=NoSource())
    assert m.get(edited.modules[0].used["SOURCE"], 88888) is edited.modules[0].obj


def test_unknown_selector_does_not_block_or_partially_mutate_delete(catalog):
    p = Patch("Test Grid", "opaque")
    p.add("Test", 0, 0)
    b = p.add("Test", 2, 0)
    opaque = Obj(0x37C, [(m.F_NAME, 8, "SOURCE"), (0x10FF, 9, Obj(99999, [(1, 8, "future binding")]))])
    m.params(b.obj).append(opaque)
    imported = Patch.from_preset(p.to_preset())
    imported.delete(imported.modules[0])
    assert len(imported.modules) == 1
    assert m.get(m.get(imported.modules[0].used["SOURCE"], 0x10FF), 1) == "future binding"


def test_readable_decompile_reports_unknown_wrapper_state(catalog):
    from bwpreset.decompile import decompile

    p = Patch("Test Grid", "unknown wrapper")
    n = p.add("Test", 0, 0, TIME=0.5)
    n.used["TIME"].fields.append((987654, 8, "future metadata"))
    _, issues = decompile(p.to_preset())
    assert issues


def test_documented_lossless_cli_flag(catalog, tmp_path, capsys):
    from bwpreset.cli import main

    p = Patch("Test Grid", "cli")
    p.add("Test", 0, 0)
    path = p.save(tmp_path / "cli.bwpreset")
    main(["decompile", str(path), "--lossless"])
    code = capsys.readouterr().out
    ns = {}
    exec(code, ns)
    assert len(ns["p"].modules) == 1
