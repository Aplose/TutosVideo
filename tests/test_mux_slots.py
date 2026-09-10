"""Alignement audio sur slots de beats."""

from __future__ import annotations

import wave
from pathlib import Path

from tutosvideo.mux import concat_audio_to_slots
from tutosvideo.tts import audio_duration_seconds


def _silent_wav(path: Path, seconds: float, rate: int = 24000) -> None:
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n)


def test_concat_pads_to_slot(tmp_path: Path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _silent_wav(a, 1.0)
    _silent_wav(b, 0.5)
    out = concat_audio_to_slots([(a, 2000), (b, 1500)], tmp_path / "narration")
    assert out.is_file()
    # 2.0 + 1.5 = 3.5s (±150ms)
    dur = audio_duration_seconds(out)
    assert 3.3 <= dur <= 3.7


def test_concat_lead_silence(tmp_path: Path):
    a = tmp_path / "a.wav"
    _silent_wav(a, 1.0)
    # 500ms lead + 1s audio → slot at least 1.5s ; request 2.0s
    out = concat_audio_to_slots([(a, 2000, 500)], tmp_path / "narration_lead")
    dur = audio_duration_seconds(out)
    assert 1.85 <= dur <= 2.2
