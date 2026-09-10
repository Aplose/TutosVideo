"""Génération de sous-titres SRT alignés sur les beats."""

from __future__ import annotations

from pathlib import Path

from tutosvideo.pacing import ms_to_srt_ts


def write_srt(cues: list[tuple[int, int, str]], path: Path) -> Path:
    """cues: list of (start_ms, end_ms, text)."""
    lines: list[str] = []
    for i, (start, end, text) in enumerate(cues, start=1):
        clean = " ".join(text.split())
        lines.append(str(i))
        lines.append(f"{ms_to_srt_ts(start)} --> {ms_to_srt_ts(end)}")
        lines.append(clean)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
