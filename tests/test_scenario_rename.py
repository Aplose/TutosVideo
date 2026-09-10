"""Renommage de scénarios."""

from pathlib import Path

import pytest

from tutosvideo import config
from tutosvideo.db import connect, init_db, set_scenario_prompt
from tutosvideo.scenario_ops import rename_scenario, validate_scenario_slug


def test_validate_slug():
    assert validate_scenario_slug("  Foo-Bar_1  ") == "Foo-Bar_1"
    with pytest.raises(ValueError):
        validate_scenario_slug("")
    with pytest.raises(ValueError):
        validate_scenario_slug("../x")
    with pytest.raises(ValueError):
        validate_scenario_slug("bad name")


def test_rename_scenario(tmp_path: Path, monkeypatch):
    scenarios = tmp_path / "scenarios"
    output = tmp_path / "output"
    scenarios.mkdir()
    output.mkdir()
    monkeypatch.setattr(config, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(config, "OUTPUT_DIR", output)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr("tutosvideo.scenario_ops.SCENARIOS_DIR", scenarios)
    monkeypatch.setattr("tutosvideo.scenario_ops.OUTPUT_DIR", output)
    monkeypatch.setattr("tutosvideo.db.ROOT", tmp_path)
    monkeypatch.setattr("tutosvideo.db.SCENARIOS_DIR", scenarios)

    import tutosvideo.db as dbmod

    dbmod._INITIALIZED = False

    old = scenarios / "demo-old"
    old.mkdir()
    (old / "script.md").write_text("1. Hello\n", encoding="utf-8")
    (old / "scenario.yaml").write_text(
        "id: demo-old\ntitle: Demo\nstatus: draft\n", encoding="utf-8"
    )
    (output / "demo-old").mkdir()
    (output / "demo-old" / "audio").mkdir()
    (output / "demo-old" / "audio" / "x.mp3").write_bytes(b"xx")

    init_db()
    set_scenario_prompt("demo-old", "author", "# prompt old\n")

    new_dir = rename_scenario("demo-old", "demo-new")
    assert new_dir == (scenarios / "demo-new").resolve()
    assert not (scenarios / "demo-old").exists()
    assert (scenarios / "demo-new" / "script.md").is_file()
    yml = (scenarios / "demo-new" / "scenario.yaml").read_text(encoding="utf-8")
    assert "id: demo-new" in yml
    assert (output / "demo-new" / "audio" / "x.mp3").is_file()
    assert not (output / "demo-old").exists()

    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM scenario_prompts WHERE scenario_id = ?",
            ("demo-new",),
        ).fetchone()["c"]
        assert n >= 1
        gone = conn.execute(
            "SELECT COUNT(*) AS c FROM scenario_prompts WHERE scenario_id = ?",
            ("demo-old",),
        ).fetchone()["c"]
        assert gone == 0

    with pytest.raises(FileExistsError):
        (scenarios / "demo-old").mkdir()
        rename_scenario("demo-new", "demo-old")
