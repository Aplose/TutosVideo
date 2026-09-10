"""Schéma et chargement des scénarios YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tutosvideo.config import Settings


@dataclass
class Choreography:
    # 0 = action démarre avec le début de la voix (durée audio réelle)
    action_at: float = 0.0
    min_action_at: float = 0.0
    type_delay_ms: int = 100
    settle_ms: int = 350
    pointer_ms: int = 280


@dataclass
class Step:
    id: str
    narrate: str
    action_id: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    until: str | None = None  # checkpoint for --until (e.g. before_submit)


@dataclass
class Scenario:
    id: str
    title: str
    start_url: str
    locale: str
    status: str
    voice: dict[str, Any]
    choreography: Choreography
    viewport: dict[str, int]
    vars: dict[str, Any]
    steps: list[Step]
    path: Path

    @property
    def dir(self) -> Path:
        return self.path.parent


def _expand_vars(value: str, variables: dict[str, Any], settings: Settings | None) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key.startswith("env:"):
            env_key = key[4:]
            if settings is None:
                return ""
            return getattr(settings, env_key.lower(), "") or ""
        if key.startswith("vars."):
            return str(variables.get(key[5:], ""))
        return str(variables.get(key, match.group(0)))

    return re.sub(r"\{\{([^}]+)\}\}", repl, value)


def expand_tree(obj: Any, variables: dict[str, Any], settings: Settings | None) -> Any:
    if isinstance(obj, str):
        return _expand_vars(obj, variables, settings)
    if isinstance(obj, list):
        return [expand_tree(item, variables, settings) for item in obj]
    if isinstance(obj, dict):
        return {k: expand_tree(v, variables, settings) for k, v in obj.items()}
    return obj


def load_scenario(scenario_path: Path, settings: Settings | None = None) -> Scenario:
    yaml_path = scenario_path / "scenario.yaml" if scenario_path.is_dir() else scenario_path
    if not yaml_path.is_file():
        raise SystemExit(f"scenario.yaml introuvable : {yaml_path}")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    vars_map = dict(raw.get("vars") or {})
    if settings:
        vars_map.setdefault("email", settings.tuto_email)
        vars_map.setdefault("password", settings.tuto_password)
        vars_map.setdefault("dolibarr_login", settings.tuto_dolibarr_login)
        vars_map.setdefault("company", settings.tuto_company)
        vars_map.setdefault("phone", settings.tuto_phone)
        vars_map.setdefault(
            "subdomain",
            settings.tuto_subdomain
            or f"tuto-essentiel-{__import__('datetime').datetime.now():%Y%m%d}",
        )
    expanded = expand_tree(raw, vars_map, settings)
    choreo_raw = expanded.get("choreography") or {}
    choreography = Choreography(
        action_at=float(choreo_raw.get("action_at", 0.0)),
        min_action_at=float(choreo_raw.get("min_action_at", 0.0)),
        type_delay_ms=int(choreo_raw.get("type_delay_ms", 100)),
        settle_ms=int(choreo_raw.get("settle_ms", 350)),
        pointer_ms=int(choreo_raw.get("pointer_ms", 280)),
    )
    steps = [
        Step(
            id=str(s["id"]),
            narrate=str(s.get("narrate") or "").strip(),
            action_id=s.get("action_id"),
            actions=list(s.get("actions") or []),
            until=s.get("until"),
        )
        for s in expanded.get("steps") or []
    ]
    viewport = expanded.get("viewport") or {"width": 1920, "height": 1080}
    return Scenario(
        id=str(expanded.get("id") or yaml_path.parent.name),
        title=str(expanded.get("title") or expanded.get("id") or ""),
        start_url=str(expanded.get("start_url") or "https://www.ma-gestion-cloud.fr/"),
        locale=str(expanded.get("locale") or "fr"),
        status=str(expanded.get("status") or "draft"),
        voice=dict(expanded.get("voice") or {}),
        choreography=choreography,
        viewport={
            "width": int(viewport.get("width", 1920)),
            "height": int(viewport.get("height", 1080)),
        },
        vars=vars_map,
        steps=steps,
        path=yaml_path,
    )


def dump_scenario(data: dict[str, Any], path: Path) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
