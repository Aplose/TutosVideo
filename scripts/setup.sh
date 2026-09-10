#!/usr/bin/env bash
# Installation portable : venv local + dépendances + Chromium Playwright.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

echo "==> Projet : $ROOT"
echo "==> Python : $($PYTHON --version)"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> Création du venv ($VENV_DIR)"
  "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Installation du package (editable)"
pip install -U pip
pip install -e ".[dev]"

echo "==> Navigateur Playwright (Chromium)"
playwright install chromium

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "==> .env créé depuis .env.example (à configurer via : tutosvideo)"
fi

echo
echo "OK. Pour utiliser :"
echo "  source $VENV_DIR/bin/activate"
echo "  tutosvideo                 # interface graphique (si DISPLAY)"
echo "  tutosvideo gui             # forcer la GUI"
echo "  tutosvideo assist          # menu terminal"
echo "  ./scripts/tutosvideo-gui   # lanceur GUI via venv"
