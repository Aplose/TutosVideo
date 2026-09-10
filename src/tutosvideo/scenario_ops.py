"""Opérations sur les dossiers de scénarios (renommage, etc.)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from tutosvideo.config import OUTPUT_DIR, SCENARIOS_DIR

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_scenario_slug(name: str) -> str:
    """Retourne le slug normalisé ou lève ValueError."""
    slug = (name or "").strip()
    if not slug:
        raise ValueError("Identifiant vide.")
    if "/" in slug or "\\" in slug or slug in (".", ".."):
        raise ValueError("Identifiant invalide (pas de chemin).")
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "Identifiant invalide : lettres, chiffres, ., _, - "
            "(max 64 car., commence par alphanumérique)."
        )
    return slug


def rename_scenario(old_id: str, new_id: str) -> Path:
    """Renomme un scénario : dossier, YAML ``id``, BDD, dossier output.

    Retourne le nouveau chemin ``scenarios/<new_id>``.
    """
    old = validate_scenario_slug(old_id)
    new = validate_scenario_slug(new_id)
    if old == new:
        raise ValueError("L'ancien et le nouveau nom sont identiques.")

    src = SCENARIOS_DIR / old
    dst = SCENARIOS_DIR / new
    if not src.is_dir():
        raise FileNotFoundError(f"Scénario introuvable : {old}")
    if dst.exists():
        raise FileExistsError(f"Un scénario « {new} » existe déjà.")

    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    yaml_path = dst / "scenario.yaml"
    if yaml_path.is_file():
        _update_yaml_id(yaml_path, new)

    out_src = OUTPUT_DIR / old
    out_dst = OUTPUT_DIR / new
    if out_src.is_dir():
        if out_dst.exists():
            # Fusion prudente : ne pas écraser ; garder l'ancien sous un suffixe
            shutil.move(str(out_src), str(OUTPUT_DIR / f"{old}.pre-rename"))
        else:
            out_src.rename(out_dst)

    _rename_db_rows(old, new)

    try:
        from tutosvideo.db import refresh_scenario_status

        refresh_scenario_status(new)
    except Exception:
        pass

    return dst.resolve()


def _update_yaml_id(yaml_path: Path, new_id: str) -> None:
    text = yaml_path.read_text(encoding="utf-8")
    updated, n = re.subn(
        r"(?m)^id:\s*.*$",
        f"id: {new_id}",
        text,
        count=1,
    )
    if n:
        yaml_path.write_text(updated, encoding="utf-8")
        return
    yaml_path.write_text(f"id: {new_id}\n{text}", encoding="utf-8")


def _rename_db_rows(old_id: str, new_id: str) -> None:
    from tutosvideo.db import connect, init_db

    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, body, updated_at FROM scenario_prompts WHERE scenario_id = ?",
            (old_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO scenario_prompts (scenario_id, kind, body, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scenario_id, kind) DO UPDATE SET
                  body = excluded.body,
                  updated_at = excluded.updated_at
                """,
                (new_id, row["kind"], row["body"], row["updated_at"]),
            )
        conn.execute("DELETE FROM scenario_prompts WHERE scenario_id = ?", (old_id,))

        meta = conn.execute(
            "SELECT title, script_status, validated_at, updated_at "
            "FROM scenario_meta WHERE scenario_id = ?",
            (old_id,),
        ).fetchone()
        if meta:
            conn.execute(
                """
                INSERT INTO scenario_meta
                  (scenario_id, title, script_status, validated_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                  title = excluded.title,
                  script_status = excluded.script_status,
                  validated_at = excluded.validated_at,
                  updated_at = excluded.updated_at
                """,
                (
                    new_id,
                    meta["title"],
                    meta["script_status"],
                    meta["validated_at"],
                    meta["updated_at"],
                ),
            )
            conn.execute("DELETE FROM scenario_meta WHERE scenario_id = ?", (old_id,))
