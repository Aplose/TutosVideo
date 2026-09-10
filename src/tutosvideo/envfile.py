"""Lecture / écriture du fichier .env (partagé CLI + GUI).

Les champs connus sont déclarés dans ENV_CATALOG (groupés).
Toute clé supplémentaire trouvée dans `.env` ou `.env.example` apparaît
automatiquement dans le groupe déduit de son préfixe (IA, Dolibarr, …).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tutosvideo.config import ROOT, load_env


@dataclass(frozen=True)
class EnvField:
    key: str
    label: str
    group: str
    secret: bool = False
    default: str = ""


# Ordre d'affichage des blocs dans l'UI / le fichier .env
ENV_GROUPS: list[tuple[str, str]] = [
    ("ia", "IA (Mistral)"),
    ("compte", "Compte d'essai"),
    ("dolibarr", "Dolibarr"),
    ("imap", "E-mail (IMAP)"),
    ("autre", "Autres"),
]

# Catalogue connu — ajouter ici les variables « officielles ».
# Les clés absentes du catalogue mais présentes dans .env(.example) sont
# aussi exposées (groupe + secret déduits).
ENV_CATALOG: list[EnvField] = [
    EnvField("MISTRAL_API_KEY", "Clé API Mistral", "ia", secret=True),
    EnvField("MISTRAL_VOICE_ID", "Voice ID TTS (défaut)", "ia"),
    EnvField("MISTRAL_CHAT_MODEL", "Modèle chat", "ia", default="mistral-small-latest"),
    EnvField(
        "MISTRAL_VISION_MODEL",
        "Modèle vision (captcha)",
        "ia",
        default="mistral-small-latest",
    ),
    EnvField("MISTRAL_TTS_MODEL", "Modèle TTS", "ia", default="voxtral-mini-tts-2603"),
    EnvField("TUTO_EMAIL", "E-mail essai", "compte"),
    EnvField("TUTO_PASSWORD", "Mot de passe essai", "compte", secret=True),
    EnvField("TUTO_COMPANY", "Société démo", "compte", default="Dupont Conseil Demo"),
    EnvField("TUTO_PHONE", "Téléphone démo", "compte", default="+33600000000"),
    EnvField("TUTO_SUBDOMAIN", "Sous-domaine", "compte"),
    EnvField("TUTO_DOLIBARR_LOGIN", "Login Dolibarr", "dolibarr", default="admin"),
    EnvField(
        "TUTO_DOLIBARR_PASSWORD",
        "Mot de passe Dolibarr (local)",
        "dolibarr",
        secret=True,
    ),
    EnvField("IMAP_HOST", "IMAP host", "imap"),
    EnvField("IMAP_PORT", "IMAP port", "imap", default="993"),
    EnvField("IMAP_USER", "IMAP user", "imap"),
    EnvField("IMAP_PASSWORD", "IMAP password", "imap", secret=True),
    EnvField("IMAP_FOLDER", "IMAP folder", "imap", default="INBOX"),
    EnvField(
        "VERIFY_CODE_FILE",
        "Fichier code vérif",
        "imap",
        default="output/verify_code.txt",
    ),
]


def infer_group(key: str) -> str:
    upper = key.upper()
    if upper.startswith("MISTRAL_") or upper.endswith("_API_KEY") or "VOICE_ID" in upper:
        return "ia"
    if "DOLIBARR" in upper:
        return "dolibarr"
    if upper.startswith("IMAP_") or upper.startswith("VERIFY_"):
        return "imap"
    if upper.startswith("TUTO_"):
        return "compte"
    return "autre"


def infer_secret(key: str) -> bool:
    upper = key.upper()
    return any(
        token in upper
        for token in ("PASSWORD", "API_KEY", "SECRET", "TOKEN", "PRIVATE_KEY")
    )


def infer_label(key: str) -> str:
    return key.replace("_", " ").strip()


def _parse_env_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def discover_env_keys(path: Path | None = None) -> list[str]:
    """Clés catalogue + clés présentes dans .env et .env.example."""
    path = path or env_path()
    ordered: list[str] = []
    for field in ENV_CATALOG:
        if field.key not in ordered:
            ordered.append(field.key)
    for source in (ROOT / ".env.example", path):
        for key in _parse_env_keys(source):
            if key not in ordered:
                ordered.append(key)
    return ordered


def all_env_fields(path: Path | None = None) -> list[EnvField]:
    """Catalogue + variables découvertes (prêtes pour l'UI)."""
    by_key = {f.key: f for f in ENV_CATALOG}
    for key in discover_env_keys(path):
        if key in by_key:
            continue
        by_key[key] = EnvField(
            key=key,
            label=infer_label(key),
            group=infer_group(key),
            secret=infer_secret(key),
            default=DEFAULTS.get(key, ""),
        )
    group_rank = {gid: i for i, (gid, _) in enumerate(ENV_GROUPS)}
    catalog_rank = {f.key: i for i, f in enumerate(ENV_CATALOG)}

    def sort_key(field: EnvField) -> tuple[int, int, str]:
        return (
            group_rank.get(field.group, len(ENV_GROUPS)),
            catalog_rank.get(field.key, 10_000),
            field.key,
        )

    return sorted(by_key.values(), key=sort_key)


def iter_env_groups(
    path: Path | None = None,
) -> list[tuple[str, str, list[EnvField]]]:
    """[(group_id, titre, champs)] — groupes vides omis."""
    fields = all_env_fields(path)
    by_group: dict[str, list[EnvField]] = {gid: [] for gid, _ in ENV_GROUPS}
    for field in fields:
        by_group.setdefault(field.group, []).append(field)
    out: list[tuple[str, str, list[EnvField]]] = []
    titles = dict(ENV_GROUPS)
    for gid, _ in ENV_GROUPS:
        chunk = by_group.get(gid) or []
        if chunk:
            out.append((gid, titles[gid], chunk))
    for gid, chunk in by_group.items():
        if gid in titles or not chunk:
            continue
        out.append((gid, gid.replace("_", " ").title(), chunk))
    return out


# Compat : listes dérivées du catalogue + découverte
def _secret_keys() -> set[str]:
    return {f.key for f in all_env_fields() if f.secret}


def _env_keys() -> list[str]:
    return [f.key for f in all_env_fields()]


# Conservés pour imports existants (réévalués à l'usage via fonctions de préférence)
ENV_KEYS = [f.key for f in ENV_CATALOG]
SECRET_KEYS = {f.key for f in ENV_CATALOG if f.secret}

DEFAULTS = {f.key: f.default for f in ENV_CATALOG if f.default}


def env_path() -> Path:
    return ROOT / ".env"


def read_env_map(path: Path | None = None) -> dict[str, str]:
    path = path or env_path()
    example = ROOT / ".env.example"
    values = dict(DEFAULTS)
    # Valeurs example puis .env (ce dernier gagne)
    for source in (example, path):
        if not source.is_file():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def write_env_map(values: dict[str, str], path: Path | None = None) -> Path:
    """Écrit le .env par blocs. Secrets vides → conserve la valeur précédente."""
    path = path or env_path()
    previous = read_env_map(path) if path.is_file() else dict(DEFAULTS)
    fields = all_env_fields(path)
    known = {f.key for f in fields}

    merged: dict[str, str] = {}
    for field in fields:
        incoming = (values.get(field.key) or "").strip()
        if field.secret and not incoming:
            merged[field.key] = previous.get(field.key, field.default)
        elif incoming:
            merged[field.key] = incoming
        else:
            # Non-secret vide : garder default catalogue si défini, sinon vide
            merged[field.key] = field.default
    for key, val in previous.items():
        if key not in known:
            merged[key] = (values.get(key) or val or "").strip() or val

    lines = ["# Généré par TutosVideo", ""]
    written: set[str] = set()
    for _gid, title, group_fields in iter_env_groups(path):
        lines.append(f"# --- {title} ---")
        for field in group_fields:
            lines.append(f"{field.key}={merged.get(field.key, field.default)}")
            written.add(field.key)
        lines.append("")
    extras = [k for k in merged if k not in written]
    if extras:
        lines.append("# --- Autres (hors catalogue) ---")
        for key in extras:
            lines.append(f"{key}={merged[key]}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    load_env(path)
    return path
