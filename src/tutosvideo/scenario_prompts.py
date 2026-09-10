"""Prompts de scénario — source de vérité : BDD SQLite embarquée."""

from __future__ import annotations

from pathlib import Path

from tutosvideo.db import (
    KIND_AUTHOR,
    KIND_DISCOVER,
    ensure_scenario_prompts,
    get_default_prompt,
    get_scenario_prompt,
    init_db,
    set_default_prompt,
    set_scenario_prompt,
)

DISCOVER_PROMPT_NAME = "discover_prompt.md"
AUTHOR_PROMPT_NAME = "prompt.md"

# Alias rétrocompat (graines / tests) — les vraies valeurs sont en BDD
DEFAULT_DISCOVER_PROMPT = property  # placeholder replaced below


def _boot() -> None:
    init_db()


def discover_prompt_path(scenario_dir: Path) -> Path:
    return scenario_dir / DISCOVER_PROMPT_NAME


def author_prompt_path(scenario_dir: Path) -> Path:
    return scenario_dir / AUTHOR_PROMPT_NAME


def ensure_prompt_files(scenario_dir: Path) -> None:
    """Assure les lignes BDD (+ miroir .md) pour ce scénario."""
    scenario_dir.mkdir(parents=True, exist_ok=True)
    ensure_scenario_prompts(scenario_dir.name)


def load_discover_prompt(scenario_dir: Path) -> str:
    _boot()
    ensure_prompt_files(scenario_dir)
    return get_scenario_prompt(scenario_dir.name, KIND_DISCOVER)


def save_discover_prompt(scenario_dir: Path, text: str) -> Path:
    _boot()
    set_scenario_prompt(scenario_dir.name, KIND_DISCOVER, text)
    return discover_prompt_path(scenario_dir)


def load_author_prompt(scenario_dir: Path) -> str:
    _boot()
    ensure_prompt_files(scenario_dir)
    return get_scenario_prompt(scenario_dir.name, KIND_AUTHOR)


def save_author_prompt(scenario_dir: Path, text: str) -> Path:
    _boot()
    set_scenario_prompt(scenario_dir.name, KIND_AUTHOR, text)
    return author_prompt_path(scenario_dir)


def load_default_discover_prompt() -> str:
    _boot()
    return get_default_prompt(KIND_DISCOVER)


def load_default_author_prompt() -> str:
    _boot()
    return get_default_prompt(KIND_AUTHOR)


def save_default_discover_prompt(text: str) -> None:
    _boot()
    set_default_prompt(KIND_DISCOVER, text)


def save_default_author_prompt(text: str) -> None:
    _boot()
    set_default_prompt(KIND_AUTHOR, text)


def extract_objective_title(author_prompt: str, fallback: str = "Tutoriel") -> str:
    """Titre court depuis la section Objectif du prompt, sinon fallback."""
    lines = [ln.strip() for ln in author_prompt.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if ln.lower().startswith("## objectif"):
            for nxt in lines[i + 1 :]:
                if nxt.startswith("#"):
                    break
                if len(nxt) > 8:
                    return nxt[:120]
    for ln in lines:
        if ln.startswith("# ") and "prompt" not in ln.lower():
            return ln[2:].strip()[:120]
    return fallback


# Exposition pour tests / anciens imports
def _default_discover() -> str:
    return load_default_discover_prompt()


def _default_author() -> str:
    return load_default_author_prompt()


# Module-level names used by tests
DEFAULT_DISCOVER_PROMPT = ""  # filled lazily via __getattr__


def __getattr__(name: str):
    if name == "DEFAULT_DISCOVER_PROMPT":
        return load_default_discover_prompt()
    if name == "DEFAULT_AUTHOR_PROMPT":
        return load_default_author_prompt()
    raise AttributeError(name)
