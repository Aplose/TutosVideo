"""Validation humaine du script texte (hash SHA-256)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_NAME = "script.md"
VALIDATION_NAME = "validation.json"
SCENARIO_NAME = "scenario.yaml"


def script_hash(script_path: Path) -> str:
    data = script_path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def validation_path(scenario_dir: Path) -> Path:
    return scenario_dir / VALIDATION_NAME


def load_validation(scenario_dir: Path) -> dict | None:
    path = validation_path(scenario_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def is_approved(scenario_dir: Path) -> tuple[bool, str]:
    script = scenario_dir / SCRIPT_NAME
    if not script.is_file():
        return False, f"{SCRIPT_NAME} manquant — lancez tutosvideo author d'abord."
    validation = load_validation(scenario_dir)
    if not validation:
        return False, (
            "Script non validé. Relisez-le dans l'assistant (`tutosvideo`, menu 5) "
            f"ou lancez `tutosvideo approve {scenario_dir.name}`."
        )
    current = script_hash(script)
    expected = validation.get("script_sha256")
    if current != expected:
        return False, (
            "script.md a changé depuis la dernière validation. "
            "Relisez-le dans l'assistant puis validez à nouveau."
        )
    if validation.get("status") != "approved":
        return False, f"Statut de validation invalide : {validation.get('status')}"
    return True, "OK"


def require_approved(scenario_dir: Path) -> None:
    ok, message = is_approved(scenario_dir)
    if not ok:
        raise SystemExit(message)


def approve(scenario_dir: Path) -> Path:
    script = scenario_dir / SCRIPT_NAME
    if not script.is_file():
        raise SystemExit(f"{SCRIPT_NAME} introuvable dans {scenario_dir}")
    text = script.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Une seule ligne de statut, toujours « validé »
    cleaned: list[str] = []
    status_written = False
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("statut"):
            if not status_written:
                cleaned.append("Statut : validé.")
                status_written = True
            continue
        cleaned.append(line)
    if not status_written:
        if cleaned and cleaned[0].startswith("#"):
            cleaned[1:1] = ["", "Statut : validé."]
        else:
            cleaned.insert(0, "Statut : validé.")
            cleaned.insert(1, "")
    script.write_text("\n".join(cleaned).rstrip() + "\n", encoding="utf-8")

    digest = script_hash(script)
    payload = {
        "status": "approved",
        "script_sha256": digest,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "script_file": SCRIPT_NAME,
    }
    out = validation_path(scenario_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    yaml_path = scenario_dir / SCENARIO_NAME
    if yaml_path.is_file():
        ytext = yaml_path.read_text(encoding="utf-8")
        if re_status_draft(ytext):
            yaml_path.write_text(
                ytext.replace("status: draft", "status: approved", 1),
                encoding="utf-8",
            )
    try:
        from tutosvideo.db import refresh_scenario_status

        refresh_scenario_status(scenario_dir.name)
    except Exception:
        pass
    return out


def re_status_draft(text: str) -> bool:
    return "status: draft" in text
