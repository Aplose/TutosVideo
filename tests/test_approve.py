"""Tests du verrou approve."""

from pathlib import Path

from tutosvideo.approve import approve, is_approved, require_approved, script_hash


def test_approve_gate(tmp_path: Path):
    script = tmp_path / "script.md"
    script.write_text("1. Bonjour\n", encoding="utf-8")
    ok, _ = is_approved(tmp_path)
    assert not ok
    approve(tmp_path)
    ok, msg = is_approved(tmp_path)
    assert ok
    digest = script_hash(script)
    # mutate → invalidate
    script.write_text("1. Bonjour!\n", encoding="utf-8")
    ok, _ = is_approved(tmp_path)
    assert not ok
    assert digest != script_hash(script)
