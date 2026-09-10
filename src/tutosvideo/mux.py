"""Mux ffmpeg : vidéo silencieuse + audio aligné sur les slots de beats + burn-in SRT."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def which_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg introuvable dans le PATH.")
    return path


def concat_audio(audio_files: list[Path], out_path: Path) -> Path:
    """Concatène une liste de fichiers audio sans pads (legacy)."""
    return concat_audio_to_slots([(p, 0) for p in audio_files], out_path)


def concat_audio_to_slots(
    segments: list[tuple[Path, int]] | list[tuple[Path, int, int]],
    out_path: Path,
) -> Path:
    """Aligne chaque clip sur un slot (ms).

    Segment = (path, slot_ms) ou (path, slot_ms, lead_ms).
    - lead_ms : silence en tête (préparation visuelle avant la narration)
    - puis l'audio
    - puis silence jusqu'à slot_ms

    Ne tronque jamais le narratif : slot >= lead + durée_audio.
    """
    if not segments:
        raise SystemExit("Aucun segment audio à muxer.")
    ffmpeg = which_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audio_out = out_path.with_suffix(".m4a")

    normalized: list[tuple[Path, int, int]] = []
    for seg in segments:
        if len(seg) == 2:
            path, slot_ms = seg  # type: ignore[misc]
            lead_ms = 0
        else:
            path, slot_ms, lead_ms = seg  # type: ignore[misc]
        normalized.append((Path(path), int(slot_ms or 0), max(0, int(lead_ms or 0))))

    if len(normalized) == 1 and normalized[0][1] <= 0 and normalized[0][2] <= 0:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(normalized[0][0]),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(audio_out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"ffmpeg audio a échoué :\n{proc.stderr}")
        return audio_out

    from tutosvideo.tts import audio_duration_seconds

    resolved: list[tuple[Path, float, float]] = []
    for path, slot_ms, lead_ms in normalized:
        dur_s = float(audio_duration_seconds(path))
        lead_s = lead_ms / 1000.0
        min_slot_s = lead_s + dur_s
        slot_s = (slot_ms / 1000.0) if slot_ms and slot_ms > 0 else min_slot_s
        slot_s = max(slot_s, min_slot_s)
        resolved.append((path, slot_s, lead_s))

    inputs: list[str] = []
    for path, _, _ in resolved:
        inputs += ["-i", str(path)]

    filters: list[str] = []
    labels: list[str] = []
    for i, (_, slot_s, lead_s) in enumerate(resolved):
        whole = f"{slot_s:.3f}"
        # Normalise → délai (silence de tête) → pad → trim exact
        delay_ms = int(round(lead_s * 1000))
        chain = (
            f"[{i}:a]aformat=sample_rates=44100:channel_layouts=mono,"
            f"adelay={delay_ms}|{delay_ms},"
            f"apad=whole_dur={whole},atrim=0:{whole},asetpts=PTS-STARTPTS[a{i}]"
        )
        filters.append(chain)
        labels.append(f"[a{i}]")
    filters.append(f"{''.join(labels)}concat=n={len(resolved)}:v=0:a=1[aout]")
    cmd = [
        ffmpeg,
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(audio_out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg concat audio (slots) a échoué :\n{proc.stderr}")
    return audio_out


def mux(
    video: Path,
    audio: Path,
    srt: Path | None,
    out_mp4: Path,
    burn_subtitles: bool = True,
) -> Path:
    """Mux vidéo + audio. Pas de -shortest : on garde tout le narratif.

    Si la vidéo est un peu plus courte, ffmpeg prolonge la dernière image ;
    si l'audio est plus court, la fin de vidéo reste silencieuse.
    """
    ffmpeg = which_ffmpeg()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-i", str(video), "-i", str(audio)]
    if burn_subtitles and srt and srt.is_file():
        srt_escaped = (
            str(srt.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        )
        vf = f"subtitles='{srt_escaped}'"
        cmd += [
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out_mp4),
        ]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out_mp4),
        ]
    # Pas de -shortest : il coupait le narratif dès que la vidéo était
    # plus courte (durées audio sous-estimées / settle non paddés).
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg mux a échoué :\n{proc.stderr}")
    return out_mp4
