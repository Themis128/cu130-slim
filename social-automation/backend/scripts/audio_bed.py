#!/usr/bin/env python3
"""Generate a license-free synth audio bed (ffmpeg only, no samples).

Presets:
    chime   soft rising chime arpeggio (reveals, certificates, wins)
    pad     warm ambient chord pad (background for voice-overs/stills)
    whoosh  filtered-noise sweep (transitions, reveals)
    tick    quiet metronome pulse (countdowns, timers)

    python audio_bed.py --preset chime --seconds 6 --out bed.wav
    python audio_bed.py --preset pad --seconds 30 --volume 0.5 --out bed.wav

Output: stereo 44.1kHz WAV with fade in/out baked in. Safe to attach to
social video — synthesized, so no copyright surface.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def _fade_chain(seconds: float, fade: float = 0.8) -> str:
    out_fade_start = max(0.0, seconds - fade)
    return f"afade=t=in:d={fade},afade=t=out:st={out_fade_start:.2f}:d={fade}"


def filtergraph(preset: str, seconds: float, volume: float) -> str:
    """Return an ffmpeg -filter_complex graph that emits 'aout'."""
    fade = _fade_chain(seconds)

    if preset == "chime":
        # Rising arpeggio C5-E5-G5-C6, each note a decaying sine ping.
        notes = [523.25, 659.25, 783.99, 1046.50]
        step = seconds / (len(notes) + 1)
        parts = []
        for i, f in enumerate(notes):
            d = seconds - i * step
            parts.append(
                f"sine=frequency={f}:duration={d:.2f},"
                f"adelay={int(i * step * 1000)}:all=1,"
                f"volume={0.5 - i * 0.06:.2f},"
                f"afade=t=out:st={max(0.1, d - 1.2):.2f}:d=1.1[n{i}]"
            )
        mix = "".join(f"[n{i}]" for i in range(len(notes)))
        return ";".join(parts) + f";{mix}amix=inputs={len(notes)}:normalize=0,volume={volume},{fade},aformat=channel_layouts=stereo:sample_rates=44100[aout]"

    if preset == "pad":
        # Warm Cmaj7 pad: detuned saws through a lowpass, slow swells.
        return (
            f"sawtooth=frequency=130.81:duration={seconds:.2f},"
            f"volume=0.30,lowpass=f=800[s1];"
            f"sawtooth=frequency=164.81:duration={seconds:.2f},"
            f"volume=0.28,lowpass=f=800[s2];"
            f"sawtooth=frequency=196.00:duration={seconds:.2f},"
            f"volume=0.26,lowpass=f=700[s3];"
            f"sine=frequency=246.94:duration={seconds:.2f},volume=0.18[s4];"
            f"[s1][s2][s3][s4]amix=inputs=4:normalize=0,"
            f"tremolo=f=0.8:d=0.4,volume={volume},{fade},"
            f"aformat=channel_layouts=stereo:sample_rates=44100[aout]"
        )

    if preset == "whoosh":
        # Brown noise band-swept upward, soft tail.
        return (
            f"anoisesrc=color=brown:duration={seconds:.2f}:amplitude=0.8,"
            f"bandpass=f=600:w=300,"
            f"volume='min(1,t/{seconds:.2f})*1.1':eval=frame,"
            f"volume={volume},{fade},aformat=channel_layouts=stereo:sample_rates=44100[aout]"
        )

    if preset == "tick":
        # 1Hz soft tick — filtered click train.
        return (
            f"sine=frequency=880:duration={seconds:.2f},"
            f"apulsator=hz=1:width=0.06:levels='0+1':offset_r=0,"
            f"lowpass=f=1200,volume=0.35*{volume},{fade},"
            f"aformat=channel_layouts=stereo:sample_rates=44100[aout]"
        )

    raise SystemExit(f"unknown preset: {preset}")


def render(preset: str, seconds: float, out: Path, volume: float = 0.7) -> Path:
    graph = filtergraph(preset, seconds, volume)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=duration={seconds:.2f}",
        "-filter_complex",
        graph,
        "-map",
        "[aout]",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"{out}  ({preset}, {seconds:.1f}s)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=("chime", "pad", "whoosh", "tick"), default="chime")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--volume", type=float, default=0.7, help="Bed loudness 0-1 (default 0.7)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    render(args.preset, args.seconds, args.out, args.volume)


if __name__ == "__main__":
    main()
