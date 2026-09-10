"""Association fichier audio ↔ step_id (ids avec points)."""

from __future__ import annotations

from pathlib import Path

from tutosvideo.audio_pipeline import find_audio_file, is_audio_file_for_step


def test_is_audio_file_exact_step():
    assert is_audio_file_for_step("plans.essentiel.mp3", "plans.essentiel")
    assert is_audio_file_for_step("plans.essentiel.wav", "plans.essentiel")
    assert is_audio_file_for_step("plans.essentiel", "plans.essentiel")
    assert is_audio_file_for_step("plans.essentiel.cta.mp3", "plans.essentiel.cta")


def test_is_audio_file_does_not_match_child_id():
    assert not is_audio_file_for_step("plans.essentiel.cta.mp3", "plans.essentiel")
    assert not is_audio_file_for_step("plans.essentiel.cta.wav", "plans.essentiel")
    assert not is_audio_file_for_step("plans.essentiel.extra.mp3", "plans.essentiel")


def test_find_audio_file_distinct_siblings(tmp_path: Path):
    audio = tmp_path / "audio"
    audio.mkdir()
    parent = audio / "plans.essentiel.mp3"
    child = audio / "plans.essentiel.cta.mp3"
    parent.write_bytes(b"parent")
    child.write_bytes(b"child")
    assert find_audio_file(audio, "plans.essentiel") == parent
    assert find_audio_file(audio, "plans.essentiel.cta") == child
