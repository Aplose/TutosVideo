"""Prompts de scénario + BDD."""

from pathlib import Path

from tutosvideo.db import (
    KIND_AUTHOR,
    KIND_DISCOVER,
    get_default_prompt,
    get_scenario_prompt,
    get_scenario_status,
    init_db,
    set_default_prompt,
    set_scenario_prompt,
)
from tutosvideo.scenario_prompts import (
    ensure_prompt_files,
    extract_objective_title,
    load_discover_prompt,
    save_discover_prompt,
)


def test_db_defaults_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tutosvideo.db.ROOT", tmp_path)
    monkeypatch.setattr("tutosvideo.db.SCENARIOS_DIR", tmp_path / "scenarios")
    (tmp_path / "scenarios").mkdir()
    # reset init flag
    import tutosvideo.db as dbmod

    dbmod._INITIALIZED = False
    init_db()
    set_default_prompt(KIND_DISCOVER, "# Custom default discover\n")
    assert "Custom default" in get_default_prompt(KIND_DISCOVER)


def test_scenario_prompts_isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tutosvideo.db.ROOT", tmp_path)
    monkeypatch.setattr("tutosvideo.db.SCENARIOS_DIR", tmp_path / "scenarios")
    (tmp_path / "scenarios" / "a").mkdir(parents=True)
    (tmp_path / "scenarios" / "b").mkdir(parents=True)
    import tutosvideo.db as dbmod

    dbmod._INITIALIZED = False
    init_db()
    set_scenario_prompt("a", KIND_AUTHOR, "# Prompt A\n")
    set_scenario_prompt("b", KIND_AUTHOR, "# Prompt B\n")
    assert "Prompt A" in get_scenario_prompt("a", KIND_AUTHOR)
    assert "Prompt B" in get_scenario_prompt("b", KIND_AUTHOR)


def test_ensure_and_load(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tutosvideo.db.ROOT", tmp_path)
    monkeypatch.setattr("tutosvideo.db.SCENARIOS_DIR", tmp_path / "scenarios")
    sdir = tmp_path / "scenarios" / "demo"
    sdir.mkdir(parents=True)
    import tutosvideo.db as dbmod

    dbmod._INITIALIZED = False
    ensure_prompt_files(sdir)
    text = load_discover_prompt(sdir)
    assert len(text) > 20
    save_discover_prompt(sdir, "# Hello\n\nTest brief.\n")
    assert "Test brief" in load_discover_prompt(sdir)


def test_extract_title():
    title = extract_objective_title(
        "## Objectif détaillé\nCréer une instance demo pour le test.\n", "x"
    )
    assert "Créer" in title


def test_status_after_approve(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tutosvideo.db.ROOT", tmp_path)
    monkeypatch.setattr("tutosvideo.db.SCENARIOS_DIR", tmp_path / "scenarios")
    sdir = tmp_path / "scenarios" / "demo"
    sdir.mkdir(parents=True)
    import tutosvideo.db as dbmod

    dbmod._INITIALIZED = False
    init_db()
    (sdir / "script.md").write_text(
        "# Script — demo\n\nStatut : brouillon — à valider.\n\n1. Hello\n",
        encoding="utf-8",
    )
    assert get_scenario_status("demo") == "brouillon"
    from tutosvideo.approve import approve

    approve(sdir)
    assert get_scenario_status("demo") == "validé"
    text = (sdir / "script.md").read_text(encoding="utf-8")
    assert text.count("Statut :") == 1
    assert "validé" in text
