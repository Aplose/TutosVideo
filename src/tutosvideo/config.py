"""Chargement de la configuration depuis l'environnement / .env.

Chemins relatifs au répertoire projet (CWD ou racine détectée), jamais hardcodés.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _looks_like_project(path: Path) -> bool:
    return (path / "scenarios").is_dir() or (
        (path / "pyproject.toml").is_file()
        and "tutosvideo" in (path / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    )


def find_project_root(start: Path | None = None) -> Path:
    """Racine du projet : TUTOSVIDEO_ROOT > CWD (et parents) > package installé."""
    env = os.getenv("TUTOSVIDEO_ROOT", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
        if root.is_dir():
            return root

    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if _looks_like_project(candidate):
            return candidate

    # fallback : src/tutosvideo/config.py → parents[2] = repo si install editable
    package_root = Path(__file__).resolve().parents[2]
    if _looks_like_project(package_root):
        return package_root
    return here


ROOT = find_project_root()
SCENARIOS_DIR = ROOT / "scenarios"
OUTPUT_DIR = ROOT / "output"


def refresh_paths(root: Path | None = None) -> Path:
    """Recalcule ROOT / SCENARIOS_DIR / OUTPUT_DIR (après chdir ou setup)."""
    global ROOT, SCENARIOS_DIR, OUTPUT_DIR
    ROOT = find_project_root(root) if root is None else root.resolve()
    SCENARIOS_DIR = ROOT / "scenarios"
    OUTPUT_DIR = ROOT / "output"
    return ROOT


def load_env(env_file: Path | None = None) -> None:
    path = env_file or (ROOT / ".env")
    if path.is_file():
        load_dotenv(path, override=False)
    else:
        load_dotenv(override=False)


@dataclass(frozen=True)
class Settings:
    mistral_api_key: str
    mistral_voice_id: str
    mistral_chat_model: str
    mistral_tts_model: str
    mistral_vision_model: str
    tuto_email: str
    tuto_password: str
    tuto_dolibarr_login: str
    tuto_company: str
    tuto_phone: str
    tuto_subdomain: str
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str
    verify_code_file: Path

    @classmethod
    def from_env(cls) -> Settings:
        load_env()
        verify = os.getenv("VERIFY_CODE_FILE", "output/verify_code.txt").strip()
        verify_path = Path(verify)
        if not verify_path.is_absolute():
            verify_path = ROOT / verify_path
        return cls(
            mistral_api_key=os.getenv("MISTRAL_API_KEY", "").strip(),
            mistral_voice_id=os.getenv("MISTRAL_VOICE_ID", "").strip(),
            mistral_chat_model=os.getenv("MISTRAL_CHAT_MODEL", "mistral-small-latest").strip(),
            mistral_tts_model=os.getenv(
                "MISTRAL_TTS_MODEL", "voxtral-mini-tts-2603"
            ).strip(),
            mistral_vision_model=os.getenv(
                "MISTRAL_VISION_MODEL", "mistral-small-latest"
            ).strip(),
            tuto_email=os.getenv("TUTO_EMAIL", "").strip(),
            tuto_password=os.getenv("TUTO_PASSWORD", "").strip(),
            tuto_dolibarr_login=os.getenv("TUTO_DOLIBARR_LOGIN", "admin").strip() or "admin",
            tuto_company=os.getenv("TUTO_COMPANY", "Dupont Conseil Demo").strip(),
            tuto_phone=os.getenv("TUTO_PHONE", "+33600000000").strip(),
            tuto_subdomain=os.getenv("TUTO_SUBDOMAIN", "").strip(),
            imap_host=os.getenv("IMAP_HOST", "").strip(),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            imap_user=os.getenv("IMAP_USER", "").strip(),
            imap_password=os.getenv("IMAP_PASSWORD", "").strip(),
            imap_folder=os.getenv("IMAP_FOLDER", "INBOX").strip(),
            verify_code_file=verify_path,
        )

    def require_mistral(self) -> None:
        if not self.mistral_api_key:
            raise SystemExit(
                "MISTRAL_API_KEY manquante. Lancez `tutosvideo` (assistant) pour la configurer, "
                "ou renseignez .env."
            )

    def require_tuto_account(self) -> None:
        missing = [
            name
            for name, value in (
                ("TUTO_EMAIL", self.tuto_email),
                ("TUTO_PASSWORD", self.tuto_password),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "Identifiants d'essai manquants. Lancez `tutosvideo` (assistant) pour les saisir : "
                + ", ".join(missing)
            )


def scenario_dir(name_or_path: str | Path, *, create: bool = False) -> Path:
    path = Path(name_or_path)
    if path.is_dir():
        return path.resolve()
    candidate = SCENARIOS_DIR / str(name_or_path)
    if candidate.is_dir():
        return candidate.resolve()
    if create and not path.is_absolute() and "/" not in str(name_or_path) and "\\" not in str(
        name_or_path
    ):
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate.resolve()
    raise SystemExit(
        f"Scénario introuvable : {name_or_path}. "
        "Créez-le via l'assistant (`tutosvideo`) ou `tutosvideo discover <nom>`."
    )


def rel_display(path: Path) -> str:
    """Affiche un chemin relatif au projet quand c'est possible."""
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)
