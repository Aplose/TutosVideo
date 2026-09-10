"""Assistant interactif : config, découverte, rédaction, relecture, validation, filmage."""

from __future__ import annotations

import getpass
import re
from pathlib import Path

import yaml

from tutosvideo.approve import SCRIPT_NAME, approve, is_approved
from tutosvideo.author import author
from tutosvideo.browser import Runner
from tutosvideo.config import (
    OUTPUT_DIR,
    ROOT,
    SCENARIOS_DIR,
    Settings,
    rel_display,
    scenario_dir,
)
from tutosvideo.discover import DEFAULT_START, discover
from tutosvideo.schema import load_scenario


def _ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    raw = getpass.getpass(f"{prompt}{suffix} : ") if secret else input(f"{prompt}{suffix} : ")
    raw = raw.strip()
    if not raw and default is not None:
        return default
    return raw


def _yes(prompt: str, default: bool = True) -> bool:
    hint = "O/n" if default else "o/N"
    raw = input(f"{prompt} ({hint}) : ").strip().lower()
    if not raw:
        return default
    return raw in ("o", "oui", "y", "yes")


def _menu(title: str, choices: list[tuple[str, str]]) -> str:
    print()
    print(title)
    for key, label in choices:
        print(f"  {key}) {label}")
    keys = {k for k, _ in choices}
    while True:
        choice = input("> ").strip().lower()
        if choice in keys:
            return choice
        print(f"Choix invalide. Options : {', '.join(sorted(keys))}")


def ensure_venv_hint() -> None:
    import sys

    in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix or bool(
        os_environ_venv()
    )
    if not in_venv:
        print(
            "Astuce : activez le venv avant de lancer l'outil :\n"
            "  ./scripts/setup.sh   # une fois\n"
            "  source .venv/bin/activate\n"
            "  tutosvideo\n"
            "ou : ./scripts/tutosvideo\n"
        )


def os_environ_venv() -> bool:
    import os

    return bool(os.environ.get("VIRTUAL_ENV"))


def configure_env_interactive() -> None:
    from tutosvideo.envfile import iter_env_groups, read_env_map, write_env_map

    print("\n=== Configuration (.env) ===")
    print(f"Projet : {rel_display(ROOT) if ROOT == Path.cwd() else ROOT}")
    env_path = ROOT / ".env"
    if env_path.is_file() and not _yes("Modifier .env existant ?", default=False):
        return

    existing = read_env_map()

    def ask_key(key: str, label: str, *, secret: bool = False, default: str = "") -> str:
        current = existing.get(key, default)
        shown = "••••" if secret and current else current
        value = _ask(label, default=shown if shown else default, secret=secret)
        if secret and (value == "••••" or not value.strip()):
            return current
        return value

    values: dict[str, str] = {}
    for _gid, title, fields in iter_env_groups():
        print(f"\n— {title} —")
        for field in fields:
            values[field.key] = ask_key(
                field.key,
                field.label,
                secret=field.secret,
                default=field.default,
            )

    path = write_env_map(values)
    print(f".env enregistré → {rel_display(path)}")


def list_scenarios() -> list[str]:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        p.name for p in SCENARIOS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def pick_scenario() -> Path:
    names = list_scenarios()
    print("\n=== Scénario ===")
    if names:
        for i, name in enumerate(names, start=1):
            print(f"  {i}) {name}")
        print(f"  n) Nouveau scénario")
        raw = input("> ").strip().lower()
        if raw == "n":
            name = _ask("Identifiant (slug)", default="mgc-essentiel")
            sdir = scenario_dir(name, create=True)
            prompt = sdir / "prompt.md"
            if not prompt.is_file():
                prompt.write_text(
                    f"# Prompt — {name}\n\nObjectif à préciser avec l'assistant.\n",
                    encoding="utf-8",
                )
            return sdir
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return scenario_dir(names[int(raw) - 1])
        if raw in names:
            return scenario_dir(raw)
    name = _ask("Identifiant du scénario", default="mgc-essentiel")
    return scenario_dir(name, create=True)


def parse_script_paragraphs(script_text: str) -> list[str]:
    paras: list[str] = []
    for line in script_text.splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", line.strip())
        if m:
            paras.append(m.group(2).strip())
    return paras


def write_script_md(
    path: Path,
    title: str,
    paragraphs: list[str],
    *,
    status: str = "brouillon",
) -> None:
    if status in ("validé", "valide", "approved"):
        status_line = "Statut : validé."
    else:
        status_line = "Statut : brouillon — à valider."
    lines = [f"# Script — {title}", "", status_line, ""]
    for i, text in enumerate(paragraphs, start=1):
        lines.append(f"{i}. {text}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        from tutosvideo.db import refresh_scenario_status

        refresh_scenario_status(path.parent.name)
    except Exception:
        pass


def sync_yaml_narrates(scenario_yaml: Path, paragraphs: list[str]) -> None:
    if not scenario_yaml.is_file():
        return
    data = yaml.safe_load(scenario_yaml.read_text(encoding="utf-8")) or {}
    steps = data.get("steps") or []
    for i, step in enumerate(steps):
        if i < len(paragraphs):
            step["narrate"] = paragraphs[i]
    data["steps"] = steps
    data["status"] = "draft"
    scenario_yaml.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    # invalider validation
    val = scenario_yaml.parent / "validation.json"
    if val.is_file():
        val.unlink()


def review_script_interactive(sdir: Path) -> bool:
    """Relecture paragraphe par paragraphe. Retourne True si validé à la fin."""
    script_path = sdir / SCRIPT_NAME
    yaml_path = sdir / "scenario.yaml"
    if not script_path.is_file():
        print("Aucun script.md — lancez d'abord la rédaction.")
        return False

    title = "Tutoriel"
    first = script_path.read_text(encoding="utf-8").splitlines()
    if first and first[0].startswith("# Script"):
        title = first[0].replace("# Script —", "").strip() or title

    paragraphs = parse_script_paragraphs(script_path.read_text(encoding="utf-8"))
    if not paragraphs and yaml_path.is_file():
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        paragraphs = [str(s.get("narrate") or "").strip() for s in data.get("steps") or []]
        paragraphs = [p for p in paragraphs if p]

    print("\n=== Relecture du script (texte entendu) ===")
    print("Pour chaque paragraphe : [Entrée]=garder, e=éditer, s=supprimer, q=quitter sans valider")

    kept: list[str] = []
    i = 0
    while i < len(paragraphs):
        text = paragraphs[i]
        print(f"\n--- {i + 1}/{len(paragraphs)} ---")
        print(text)
        action = input("[Entrée]/e/s/q > ").strip().lower()
        if action == "q":
            print("Relecture interrompue (non validé).")
            write_script_md(script_path, title, kept + paragraphs[i:])
            sync_yaml_narrates(yaml_path, kept + paragraphs[i:])
            return False
        if action == "s":
            i += 1
            continue
        if action == "e":
            print("Nouveau texte (une ligne) :")
            new = input("> ").strip()
            if new:
                text = new
        kept.append(text)
        i += 1

    write_script_md(script_path, title, kept)
    sync_yaml_narrates(yaml_path, kept)
    print(f"\n{len(kept)} paragraphe(s) conservés.")

    if _yes("Valider ce script maintenant (approve) ?", default=True):
        out = approve(sdir)
        print(f"Validé → {rel_display(out)}")
        return True
    print("Script enregistré en brouillon (pas encore approuvé).")
    return False


def run_preview(sdir: Path, until: str = "before_submit") -> None:
    settings = Settings.from_env()
    scenario = load_scenario(sdir, settings)
    from tutosvideo.audio_pipeline import load_durations_ms

    try:
        durations = load_durations_ms(scenario)
    except Exception:
        durations = {}
    print(f"\nPreview jusqu'à « {until} » (navigateur visible)…")
    Runner(
        scenario,
        settings,
        headless=False,
        debug_overlay=True,
        until=until,
        record_output=None,
        audio_durations_ms=durations,
    ).run()
    print("Preview terminée.")


def run_render_via_cli(sdir: Path, *, dry_run: bool, headed: bool) -> None:
    # Délègue à la commande render pour éviter la duplication
    from tutosvideo import cli as cli_mod

    argv = ["render", sdir.name]
    if dry_run:
        argv.append("--dry-run")
    if headed:
        argv.append("--headed")
    cli_mod.main(argv)


def assistant_loop() -> None:
    ensure_venv_hint()
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("╔══════════════════════════════════════╗")
    print("║     TutosVideo — assistant           ║")
    print("╚══════════════════════════════════════╝")
    print(f"Répertoire projet : {ROOT}")

    while True:
        choice = _menu(
            "Que voulez-vous faire ?",
            [
                ("1", "Configurer les secrets (.env)"),
                ("2", "Créer / choisir un scénario"),
                ("3", "Découvrir le parcours web (sans soumettre)"),
                ("4", "Rédiger le script (IA ou local)"),
                ("5", "Relire et valider le script (interactif)"),
                ("6", "Prévisualiser (avant inscription)"),
                ("7", "Filmer (render — peut créer un essai MGC)"),
                ("8", "Filmer en dry-run (silence, stop avant submit)"),
                ("9", "État du scénario"),
                ("0", "Quitter"),
            ],
        )
        if choice == "0":
            print("À bientôt.")
            return
        if choice == "1":
            configure_env_interactive()
            continue

        sdir = pick_scenario()
        print(f"Scénario : {rel_display(sdir)}")

        if choice == "2":
            continue
        if choice == "3":
            url = _ask("URL de départ", default=DEFAULT_START)
            headed = _yes("Navigateur visible ?", default=False)
            out = discover(sdir, start_url=url, headless=not headed)
            print(f"Catalogue → {rel_display(out)}")
        elif choice == "4":
            if not (sdir / "process.json").is_file():
                if _yes("process.json absent — lancer discover maintenant ?", default=True):
                    out = discover(sdir, headless=True)
                    print(f"Catalogue → {rel_display(out)}")
                else:
                    continue
            objective = _ask(
                "Objectif du tuto",
                default="Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud",
            )
            settings = Settings.from_env()
            use_llm = bool(settings.mistral_api_key) and _yes(
                "Utiliser Mistral pour rédiger ?", default=bool(settings.mistral_api_key)
            )
            script, yml, source = author(sdir, settings, objective=objective, use_llm=use_llm)
            print(f"Script → {rel_display(script)}")
            print(f"YAML   → {rel_display(yml)}")
            print(f"Source → {source}")
            if _yes("Passer à la relecture interactive ?", default=True):
                review_script_interactive(sdir)
        elif choice == "5":
            review_script_interactive(sdir)
        elif choice == "6":
            until = _ask("Arrêt (--until)", default="before_submit")
            run_preview(sdir, until=until)
        elif choice == "7":
            ok, msg = is_approved(sdir)
            if not ok:
                print(msg)
                if _yes("Relire et valider maintenant ?", default=True):
                    if not review_script_interactive(sdir):
                        continue
                else:
                    continue
            if not _yes(
                "ATTENTION : un render complet peut créer une instance MGC. Continuer ?",
                default=False,
            ):
                continue
            headed = _yes("Navigateur visible ?", default=True)
            run_render_via_cli(sdir, dry_run=False, headed=headed)
        elif choice == "8":
            ok, msg = is_approved(sdir)
            if not ok:
                print(msg)
                if _yes("Valider le script d'abord ?", default=True):
                    if not review_script_interactive(sdir):
                        continue
                else:
                    continue
            run_render_via_cli(sdir, dry_run=True, headed=False)
        elif choice == "9":
            ok, msg = is_approved(sdir)
            print("Validation :", "OK" if ok else "non", "—", msg)
            for name in ("process.json", "script.md", "scenario.yaml", "validation.json"):
                p = sdir / name
                print(f"  {'✓' if p.is_file() else '·'} {name}")
            out = OUTPUT_DIR / sdir.name
            if out.is_dir():
                mp4 = list(out.glob("*.mp4"))
                print(f"  Sortie : {rel_display(out)}" + (f" ({mp4[0].name})" if mp4 else ""))
