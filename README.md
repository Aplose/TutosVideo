# TutosVideo

Pipeline de tutoriels vidéo (découvrir → rédiger → valider → filmer), rythme voix-d'abord.

## Installation

```bash
./scripts/setup.sh
source .venv/bin/activate
```

Prérequis : `python3` ≥ 3.11, `ffmpeg`, tkinter (souvent `python3-tk` sous Debian/Ubuntu).

## Interface graphique (recommandé)

Un seul outil, plusieurs écrans :

| Écran | Rôle |
|---|---|
| Accueil | État venv / .env / scénario |
| Setup | Créer venv, pip install, Chromium |
| Configuration | Clés Mistral, compte essai, IMAP |
| Scénario | Choisir / créer |
| Découverte | Catalogue d'actions (sans soumettre) |
| Script | Générer, relire paragraphes, valider |
| Filmage | Preview / render dry-run / render complet |

```bash
./scripts/tutosvideo-gui
# ou
tutosvideo gui
# ou simplement (si DISPLAY) :
tutosvideo
```

## Terminal

```bash
tutosvideo assist          # menu texte
tutosvideo discover …
tutosvideo render … --dry-run
```

Secrets via l'écran Configuration (ou `.env`). À ignorer localement si besoin : `.venv/`, `.env`, `output/`.

Chemins relatifs au dépôt (ou `TUTOSVIDEO_ROOT`).
