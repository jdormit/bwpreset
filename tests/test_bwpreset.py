import itertools

import pytest
from conftest import bitwig_available

from bwpreset import model as m
from bwpreset.build import Patch, PatchError, Port
from bwpreset.codec import Obj, parse, serialize, walk
from bwpreset.schema import GRID_DEVICES, corpus_files


def is_grid_preset(path):
    if b"Grid" not in open(path, "rb").read(4000):
        return False
    meta = {k: v for k, _, v in parse(open(path, "rb").read()).meta}
    return meta.get("device_name") in GRID_DEVICES


def grid_corpus(n=40):
    if not bitwig_available():
        return []
    return list(itertools.islice(filter(is_grid_preset, corpus_files()), n))


@pytest.mark.parametrize("path", grid_corpus())
def test_round_trip_is_byte_identical(path):
    data = open(path, "rb").read()
    assert serialize(parse(data)) == data


def test_references_resolve_to_shared_objects():
    for path in grid_corpus(200):
        body = parse(open(path, "rb").read()).body
        rcs = [o for o in walk(body) if o.cls == m.REMOTE_CONTROL]
        descriptors = [m.get(o, m.F_RC_DESCRIPTOR) for o in rcs]
        if len({id(d) for d in descriptors}) < len(descriptors):
            return
    pytest.skip("no preset with a shared remote control descriptor")


def reparse(p: Patch):
    return parse(serialize(p.to_preset()))


def grid_modules(preset):
    return m.get(next(o for o in walk(preset.body) if o.cls == m.GRID), m.F_LIST)


def test_patch_writes_only_set_params_and_cables():
    p = Patch("Poly Grid", "t")
    osc = p.add("Oscillator/Sine", 0, 0)
    env = p.add("Envelope/ADSR", 3, 0, DECAY=0.4)
    env.connect(IN=osc)
    mods = grid_modules(reparse(p))
    assert [m.get(x, m.F_NAME) for x in mods] == ["0", "1"]
    env_params = {m.get(x, m.F_NAME): x for x in m.params(mods[1])}
    assert set(env_params) == {"DECAY", "IN"}
    assert m.get(env_params["IN"], m.F_SOURCE) == "CONTENTS/MODULES/0/CONTENTS/OUT"
    assert m.get(env_params["DECAY"], 0x136) == 0.4
    assert m.params(mods[0]) == []


def test_skeleton_modules_and_modulators_are_removed():
    preset = reparse(Patch("Poly Grid", "t"))
    assert grid_modules(preset) == []
    device = m.get(preset.body, m.F_DEVICE)
    assert m.get(m.get(device, m.F_MODULATORS), m.F_LIST) == []
    meta = {k: v for k, _, v in preset.meta}
    assert meta["referenced_module_ids"] == [] and "revision_id" not in meta


def test_unknown_names_are_rejected():
    p = Patch("Poly Grid", "t")
    osc = p.add("Oscillator/Sine", 0, 0)
    with pytest.raises(PatchError):
        osc.connect(NOPE=osc)
    with pytest.raises(PatchError):
        osc.set(NOPE=1)
    with pytest.raises(PatchError):
        p.add("Oscillator/Nope", 0, 0)
    with pytest.raises(PatchError):
        osc.connect(PITCH=osc)


def test_device_settings():
    p = Patch("Poly Grid", "t", voices=8, glide=0.0, retrigger="note_on", OUTPUT=0.5)
    poly = next(o for o in walk(reparse(p).body) if o.cls == m.POLY)
    assert m.get(poly, 0x28FF) == 8 and m.get(poly, 0x2906) == 1
    assert m.get(m.get(poly, 0x2902), 0x136) == 0.0


def test_modulator_routing_and_remote_controls():
    p = Patch("Poly Grid", "t")
    env = p.add("Envelope/ADSR", 0, 0)
    lfo = p.modulator("LFO/LFO")
    lfo.route(env["DECAY"], 0.25)
    p.remote(env["DECAY"], "Decay")
    preset = reparse(p)
    routing = next(o for o in walk(preset.body) if o.cls == m.MOD_ROUTING)
    assert m.get(routing, m.F_ROUTE_TARGET) == "CONTENTS/MODULES/0/CONTENTS/DECAY"
    assert m.get(routing, m.F_ROUTE_AMOUNT) == 0.25
    assert m.get(m.get(routing, m.F_ROUTE_DESCRIPTOR), 0x125) == 2.0
    rc = next(o for o in walk(preset.body) if o.cls == m.REMOTE_CONTROL)
    assert (m.get(rc, m.F_RC_NAME), m.get(rc, m.F_RC_TARGET)) == ("Decay", "CONTENTS/MODULES/0/CONTENTS/DECAY")
    meta = {k: v for k, _, v in preset.meta}
    assert meta["referenced_modulator_ids"] == [str(lfo.type_id)]


def test_module_mod_source_routes_to_device_param():
    p = Patch("Poly Grid", "t")
    env = p.add("Envelope/ADSR", 0, 0)
    env.route(p["PITCH_TRANSPOSE"], 0.5)
    routing = next(o for o in walk(reparse(p).body) if o.cls == m.MOD_ROUTING)
    assert m.get(routing, m.F_ROUTE_TARGET) == "CONTENTS/PITCH_TRANSPOSE"


@pytest.mark.filterwarnings("ignore:modulation amount")
@pytest.mark.parametrize("path", grid_corpus(60))
def test_decompiled_code_rebuilds_the_same_graph(path):
    from bwpreset.decompile import decompile
    from experiments.coverage import compare

    original = parse(open(path, "rb").read())
    code, unsupported = decompile(original)
    ns = {}
    exec(code, ns)
    assert compare(original, parse(serialize(ns["p"].to_preset())), unsupported) == []


def test_decompile_escapes_awkward_names():
    from bwpreset.decompile import decompile

    p = Patch("Note Grid", "t")
    q = p.add("Pitch/Pitch Quantize", 0, 0, title="2x ish", **{"C#": True})
    q.set(**{"D#": False})
    code, _ = decompile(reparse(p))
    ns = {}
    exec(code, ns)
    params = {m.get(x, m.F_NAME): m.param_value(x) for x in m.params(grid_modules(reparse(ns["p"]))[0])}
    assert params["C#"] is True and params["D#"] is False


def module_params(preset, index=0):
    return {m.get(x, m.F_NAME): x for x in m.params(grid_modules(preset)[index])}


def test_referencing_a_port_does_not_write_the_parameter():
    p = Patch("Poly Grid", "t")
    osc = p.add("Oscillator/Sine", 0, 0)
    lfo = p.modulator("LFO/LFO")
    lfo.route(osc["PITCH"], 1.0)
    p.remote(osc["DETUNE"])
    assert module_params(reparse(p)) == {}


def test_failed_calls_leave_the_patch_unchanged():
    p = Patch("Poly Grid", "t")
    osc = p.add("Oscillator/Sine", 0, 0)
    with pytest.raises(PatchError):
        osc.connect(PITCH=osc)
    with pytest.raises(PatchError):
        osc.set(PITCH_IN=1)
    with pytest.raises(PatchError):
        p.remote(Port(osc, "NO_DESCRIPTOR_HERE"))
    with pytest.raises(PatchError):
        p.configure(retrigger="sometimes")
    assert module_params(reparse(p)) == {}
    assert p.remote_pages == []


def test_set_rejects_wrong_value_types():
    p = Patch("Poly Grid", "t")
    osc = p.add("Oscillator/Sine", 0, 0)
    with pytest.raises(PatchError):
        osc.set(NUMERATOR=1.5)
    with pytest.raises(PatchError):
        osc.set(PITCH="high")


def test_remote_controls_keep_pages_and_slots():
    p = Patch("Poly Grid", "t")
    env = p.add("Envelope/ADSR", 0, 0)
    p.remote(env["DECAY"], "Decay", slot=3)
    p.page("Second")
    p.remote(env["ATTACK"])
    pages = m.get(m.get(m.get(reparse(p).body, m.F_DEVICE), m.F_REMOTE_CONTROLS), m.F_REMOTE_PAGES)
    assert [m.get(pg, m.F_PAGE_NAME) for pg in pages] == ["", "Second"]
    assert [m.get(rc, m.F_RC_INDEX) for rc in m.get(pages[0], m.F_PAGE_CONTROLS)] == [3]


def test_generated_presets_are_not_learned_from():
    from bwpreset.schema import is_generated

    assert is_generated(reparse(Patch("Poly Grid", "t")))


def test_note_priority_and_legato_glide():
    p = Patch("Poly Grid", "t", note_priority="high", legato_glide=True, retrigger="never", steal_same_key=True)
    poly = next(o for o in walk(reparse(p).body) if o.cls == m.POLY)
    assert (m.get(poly, 0x40D6), m.get(poly, 0x2903), m.get(poly, 0x2906), m.get(poly, 0x2904)) == (1, True, 0, True)
    with pytest.raises(PatchError):
        p.configure(note_priority="middle")


def test_number_params_without_known_descriptor_get_a_placeholder():
    p = Patch("Poly Grid", "t")
    osc = p.add("Oscillator/Sine", 0, 0)
    osc.available["UNSEEN"] = Obj(m.P_NUMBER, [(m.F_NAME, 0x08, "UNSEEN"), (0x136, 0x07, 0.0)])
    p.remote(Port(osc, "UNSEEN"))
    rc = next(o for o in walk(reparse(p).body) if o.cls == m.REMOTE_CONTROL)
    assert m.get(m.get(rc, m.F_RC_DESCRIPTOR), 0x125) == 1.0


def test_tiny_modulation_amount_warns():
    p = Patch("Poly Grid", "t")
    lp = p.add("Filter/Low-pass", 0, 0)
    with pytest.warns(UserWarning, match="under 2%"):
        p.modulator("LFO/LFO").route(lp["CUTOFF"], 0.3)
