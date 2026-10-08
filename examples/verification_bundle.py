"""Generate small, locally sourced presets for live Grid checks."""

import argparse
import math
import struct
import wave
from pathlib import Path

from bwpreset.assets import AudioFile, Sample, SamplePlayer, Slice, SlicedSample, Wavetable
from bwpreset.build import Curve, Patch
from bwpreset.units import Hz, Seconds


def generate(out):
    out = Path(out).resolve()
    media = out / "assets"
    media.mkdir(parents=True, exist_ok=True)
    audio_path = media / "tone.wav"
    rate = 48000
    with wave.open(str(audio_path), "wb") as f:
        f.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        f.writeframes(b"".join(struct.pack("<h", round(8192 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate * 2)))
    audio = AudioFile.from_file(audio_path, portable_path="assets/tone.wav")
    paths = []

    def save(p):
        paths.append(p.save(out / p.filename))

    p = Patch("Poly Grid", "30 Sample and player", voices=4, glide=Seconds(0))
    source = p.add("Oscillator/Sampler", 0, 0, SAMPLE=Sample(audio, root_key=69), SAMPLE_PLAYER=SamplePlayer(playback_mode="repitch"))
    p.add("I/O/Audio Out", 12, 0).connect(IN=source)
    save(p)

    p = Patch("Poly Grid", "31 Manual slices", voices=4)
    source = p.add("Oscillator/Sampler", 0, 0, SAMPLE=SlicedSample(audio, [Slice(0), Slice(0.5), Slice(1), Slice(1.5)]))
    p.add("I/O/Audio Out", 12, 0).connect(IN=source)
    save(p)

    table = Wavetable(
        [[math.sin(2 * math.pi * i / 2048) for i in range(2048)], [2 * i / 2048 - 1 for i in range(2048)]], name="Sine to saw"
    )
    table.write(media / "sine-to-saw.wt")
    p = Patch("Poly Grid", "32 Wavetable and per voice", voices=4)
    source = p.add("Oscillator/Wavetable", 0, 0, WAVETABLE=table)
    env = p.add("Envelope/ADSR", 9, 0, ATTACK=Seconds(0.01), RELEASE=Seconds(0.2))
    env.connect(IN=source)
    p.add("I/O/Audio Out", 14, 0).connect(IN=env)
    lfo = p.modulator("LFO/LFO", RATE=Hz(1))
    lfo.state(per_voice=True, color="mint")
    lfo.route(source["TABLE_INDEX"], 0.8)
    p.remote(source["TABLE_INDEX"], "Table")
    save(p)

    p = Patch("Poly Grid", "33 Curve hold and loop", voices=4, mono_mode="true")
    source = p.add("Oscillator/Sine", 0, 0)
    env = p.add(
        "Envelope/Segments",
        4,
        0,
        CURVE=Curve(
            [(0, 0), (0.1, 1), (0.5, 0.4), (0.75, 0.6), (1, 0)], kind="envelope", sustain=2, loop_start=2, loop_end=3, playback="ping_pong"
        ),
    )
    env.state(color="mint", width=8, height=3)
    env.connect(IN=source)
    p.add("I/O/Audio Out", 14, 0).connect(IN=env)
    save(p)

    p = Patch("FX Grid", "34 FX low pass")
    source = p.add("I/O/Audio In", 0, 0)
    filt = p.add("Filter/Low-pass", 3, 0, CUTOFF=Hz(440))
    filt.connect(IN=source)
    p.add("I/O/Audio Out", 6, 0).connect(IN=filt)
    save(p)

    p = Patch("Note Grid", "35 Note expressions through", NOTE_THRU=False)
    source = p.add("I/O/Note In", 0, 0, ENABLE_EXPRESSIONS=True)
    p.add("I/O/Note Out", 4, 0, ENABLE_EXPRESSIONS=True).connect(
        GATE_IN=source.out("GATE_OUT"),
        PITCH_IN=source.out("PITCH_OUT"),
        VELOCITY_IN=source.out("VELOCITY_OUT"),
        TIMBRE_IN=source.out("TIMBRE_OUT"),
        PRESSURE_IN=source.out("PRESSURE_OUT"),
    )
    save(p)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    for path in generate(parser.parse_args().out):
        print(path)
