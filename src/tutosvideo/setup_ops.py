"""Opérations d'installation (équivalent scripts/setup.sh), appelables depuis la GUI."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tutosvideo.config import ROOT

LogFn = Callable[[str], None]


def venv_python(venv_dir: Path | None = None) -> Path:
    venv_dir = venv_dir or (ROOT / ".venv")
    name = "python.exe" if os.name == "nt" else "python"
    return venv_dir / ("Scripts" if os.name == "nt" else "bin") / name


def venv_exists(venv_dir: Path | None = None) -> bool:
    return venv_python(venv_dir).is_file()


def run_setup(
    log: LogFn,
    *,
    venv_dir: Path | None = None,
    with_playwright: bool = True,
    with_dev: bool = True,
) -> None:
    """Crée le venv, installe le package, optionnellement Chromium."""
    venv_dir = venv_dir or (ROOT / ".venv")
    py = Path(sys.executable)
    log(f"Projet : {ROOT}")
    log(f"Python hôte : {py} ({sys.version.split()[0]})")

    if not venv_exists(venv_dir):
        log(f"Création du venv → {venv_dir}")
        subprocess.run([str(py), "-m", "venv", str(venv_dir)], check=True, cwd=ROOT)
    else:
        log(f"venv déjà présent → {venv_dir}")

    vpy = venv_python(venv_dir)
    log("Mise à jour de pip…")
    subprocess.run([str(vpy), "-m", "pip", "install", "-U", "pip"], check=True, cwd=ROOT)

    target = ".[dev]" if with_dev else "."
    log(f"Installation editable ({target})…")
    subprocess.run([str(vpy), "-m", "pip", "install", "-e", target], check=True, cwd=ROOT)

    if with_playwright:
        log("Installation Chromium (Playwright)…")
        subprocess.run([str(vpy), "-m", "playwright", "install", "chromium"], check=True, cwd=ROOT)

    env_example = ROOT / ".env.example"
    env_file = ROOT / ".env"
    if not env_file.is_file() and env_example.is_file():
        env_file.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
        log(".env créé depuis .env.example")

    log("Setup terminé.")


def setup_status() -> dict[str, bool | str]:
    venv = ROOT / ".venv"
    return {
        "root": str(ROOT),
        "venv": venv_exists(venv),
        "env_file": (ROOT / ".env").is_file(),
        "ffmpeg": _which("ffmpeg"),
        "python": sys.version.split()[0],
    }


def _which(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None
