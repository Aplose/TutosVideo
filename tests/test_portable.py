"""Tests chemins portables + parse script assistant."""

from pathlib import Path

from tutosvideo.assistant import parse_script_paragraphs, write_script_md
from tutosvideo.config import find_project_root, rel_display


def test_find_project_root_from_scenarios(tmp_path: Path, monkeypatch):
    (tmp_path / "scenarios").mkdir()
    monkeypatch.chdir(tmp_path)
    assert find_project_root() == tmp_path.resolve()


def test_parse_and_write_script(tmp_path: Path):
    path = tmp_path / "script.md"
    write_script_md(path, "Demo", ["Premier.", "Deuxième."])
    paras = parse_script_paragraphs(path.read_text(encoding="utf-8"))
    assert paras == ["Premier.", "Deuxième."]


def test_rel_display_under_root(tmp_path: Path, monkeypatch):
    (tmp_path / "scenarios").mkdir()
    monkeypatch.chdir(tmp_path)
    from tutosvideo import config

    config.refresh_paths(tmp_path)
    p = tmp_path / "output" / "a.mp4"
    assert rel_display(p) == "output/a.mp4"
