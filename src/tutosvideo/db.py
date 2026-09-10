"""Base SQLite embarquée (prompts par défaut + prompts par scénario + méta)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tutosvideo.config import ROOT, SCENARIOS_DIR

KIND_DISCOVER = "discover"
KIND_AUTHOR = "author"

# Graines initiales (une seule fois) — ensuite tout vit en BDD.
_SEED_DISCOVER = """# Brief de découverte

## Objectif à explorer
Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud (essai gratuit),
depuis la page d'accueil jusqu'au formulaire d'inscription — sans soumettre.

## Site de départ
https://www.ma-gestion-cloud.fr/

## Ce qu'il faut cataloguer
- Navigation vers la section Tarifs / offres
- Carte / offre « Essentiel » et CTA d'essai
- Page d'inscription : champs visibles (e-mail, société, téléphone, mots de passe, sous-domaine)
- Bouton de validation (à NE PAS cliquer pendant discover)

## Contraintes
- Ne jamais soumettre le formulaire
- Ne pas inventer de pages hors parcours
- Préférer les libellés visibles à l'écran
"""

_SEED_AUTHOR = """# Prompt d'authoring

## Objectif détaillé
Tutoriel vidéo : créer une instance Dolibarr **Essentiel** sur Ma Gestion Cloud,
depuis l'accueil jusqu'à un tableau de bord Dolibarr utilisable.

## Contraintes
- Source des actions : uniquement `process.json` (action_id réels).
- Un paragraphe = un geste (un champ, un bouton, une carte tarif).
- Nommer les libellés visibles.
- Ne pas inventer de bouton.
- Français clair, ton professionnel.
- Placeholders `{{vars.*}}` pour les données d'essai.
- Ne pas survoler les témoignages ni les autres offres une par une :
  rester sur Essentiel puis démarrer l'essai.

## Parcours attendu
1. Accueil ma-gestion-cloud.fr
2. Section Tarifs — présenter Essentiel
3. CTA essai Essentiel
4. Formulaire d'inscription champ par champ
5. Code e-mail
6. Installation (phrase courte)
7. Connexion Dolibarr (login admin) + tableau de bord

## Filmage
- Avant chaque action : amener directement le champ / bouton au centre (saut),
  sans défilement progressif long.
"""


def db_path() -> Path:
    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data / "tutosvideo.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_INITIALIZED = False


def init_db() -> Path:
    """Crée le schéma et peuplement initial des prompts par défaut."""
    global _INITIALIZED
    path = db_path()
    if _INITIALIZED:
        return path
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS default_prompts (
              kind TEXT PRIMARY KEY,
              body TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenario_prompts (
              scenario_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              body TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (scenario_id, kind)
            );
            CREATE TABLE IF NOT EXISTS scenario_meta (
              scenario_id TEXT PRIMARY KEY,
              title TEXT,
              script_status TEXT NOT NULL DEFAULT 'absent',
              validated_at TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )
        for kind, body in (
            (KIND_DISCOVER, _SEED_DISCOVER.strip() + "\n"),
            (KIND_AUTHOR, _SEED_AUTHOR.strip() + "\n"),
        ):
            row = conn.execute(
                "SELECT kind FROM default_prompts WHERE kind = ?", (kind,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO default_prompts (kind, body, updated_at) VALUES (?, ?, ?)",
                    (kind, body, _now()),
                )
    _INITIALIZED = True
    _import_existing_scenario_files()
    return path


def _import_existing_scenario_files() -> None:
    """Importe les .md déjà présents si la BDD n'a pas encore la ligne."""
    if not SCENARIOS_DIR.is_dir():
        return
    mapping = {
        KIND_DISCOVER: "discover_prompt.md",
        KIND_AUTHOR: "prompt.md",
    }
    with connect() as conn:
        for sdir in SCENARIOS_DIR.iterdir():
            if not sdir.is_dir() or sdir.name.startswith("."):
                continue
            sid = sdir.name
            for kind, filename in mapping.items():
                exists = conn.execute(
                    "SELECT 1 FROM scenario_prompts WHERE scenario_id = ? AND kind = ?",
                    (sid, kind),
                ).fetchone()
                if exists:
                    continue
                fpath = sdir / filename
                if fpath.is_file():
                    body = fpath.read_text(encoding="utf-8")
                else:
                    body = get_default_prompt(kind)
                conn.execute(
                    "INSERT INTO scenario_prompts (scenario_id, kind, body, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (sid, kind, body, _now()),
                )
            _sync_meta_row(conn, sid)


def get_default_prompt(kind: str) -> str:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT body FROM default_prompts WHERE kind = ?", (kind,)
        ).fetchone()
        if row:
            return str(row["body"])
    return _SEED_AUTHOR if kind == KIND_AUTHOR else _SEED_DISCOVER


def set_default_prompt(kind: str, body: str) -> None:
    init_db()
    text = body.rstrip() + "\n"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO default_prompts (kind, body, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(kind) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
            """,
            (kind, text, _now()),
        )


def get_scenario_prompt(scenario_id: str, kind: str) -> str:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT body FROM scenario_prompts WHERE scenario_id = ? AND kind = ?",
            (scenario_id, kind),
        ).fetchone()
        if row:
            return str(row["body"])
        # Créer à partir du défaut
        body = get_default_prompt(kind)
        conn.execute(
            "INSERT INTO scenario_prompts (scenario_id, kind, body, updated_at) VALUES (?, ?, ?, ?)",
            (scenario_id, kind, body, _now()),
        )
        return body


def set_scenario_prompt(scenario_id: str, kind: str, body: str) -> None:
    init_db()
    text = body.rstrip() + "\n"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO scenario_prompts (scenario_id, kind, body, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scenario_id, kind) DO UPDATE SET
              body = excluded.body, updated_at = excluded.updated_at
            """,
            (scenario_id, kind, text, _now()),
        )
    _export_prompt_file(scenario_id, kind, text)


def ensure_scenario_prompts(scenario_id: str) -> None:
    """À la création d'un scénario : copie les prompts par défaut courants."""
    init_db()
    for kind in (KIND_DISCOVER, KIND_AUTHOR):
        with connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM scenario_prompts WHERE scenario_id = ? AND kind = ?",
                (scenario_id, kind),
            ).fetchone()
            if exists:
                continue
            body = get_default_prompt(kind)
            conn.execute(
                "INSERT INTO scenario_prompts (scenario_id, kind, body, updated_at) VALUES (?, ?, ?, ?)",
                (scenario_id, kind, body, _now()),
            )
            _export_prompt_file(scenario_id, kind, body)
    refresh_scenario_status(scenario_id)


def _export_prompt_file(scenario_id: str, kind: str, body: str) -> None:
    """Miroir fichier pour lecture humaine / outils externes."""
    sdir = SCENARIOS_DIR / scenario_id
    sdir.mkdir(parents=True, exist_ok=True)
    name = "discover_prompt.md" if kind == KIND_DISCOVER else "prompt.md"
    (sdir / name).write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")


def _sync_meta_row(conn: sqlite3.Connection, scenario_id: str) -> None:
    from tutosvideo.approve import SCRIPT_NAME, is_approved, load_validation

    sdir = SCENARIOS_DIR / scenario_id
    script = sdir / SCRIPT_NAME
    if not script.is_file():
        status, validated_at = "absent", None
    else:
        ok, _ = is_approved(sdir)
        if ok:
            status = "validé"
            val = load_validation(sdir) or {}
            validated_at = val.get("validated_at")
        else:
            status = "brouillon"
            validated_at = None
    title = scenario_id
    yaml_path = sdir / "scenario.yaml"
    if yaml_path.is_file():
        try:
            import yaml

            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if data.get("title"):
                title = str(data["title"])
        except Exception:
            pass
    conn.execute(
        """
        INSERT INTO scenario_meta (scenario_id, title, script_status, validated_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(scenario_id) DO UPDATE SET
          title = excluded.title,
          script_status = excluded.script_status,
          validated_at = excluded.validated_at,
          updated_at = excluded.updated_at
        """,
        (scenario_id, title, status, validated_at, _now()),
    )


def refresh_scenario_status(scenario_id: str) -> str:
    """Recalcule et stocke le statut de validation. Retourne validé|brouillon|absent."""
    init_db()
    with connect() as conn:
        _sync_meta_row(conn, scenario_id)
        row = conn.execute(
            "SELECT script_status FROM scenario_meta WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
        return str(row["script_status"]) if row else "absent"


def get_scenario_status(scenario_id: str) -> str:
    init_db()
    # Toujours resynchroniser depuis validation.json / script.md
    return refresh_scenario_status(scenario_id)


def list_scenario_statuses() -> dict[str, str]:
    init_db()
    if not SCENARIOS_DIR.is_dir():
        return {}
    out: dict[str, str] = {}
    for sdir in sorted(SCENARIOS_DIR.iterdir()):
        if sdir.is_dir() and not sdir.name.startswith("."):
            out[sdir.name] = refresh_scenario_status(sdir.name)
    return out
