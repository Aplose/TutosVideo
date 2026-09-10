"""CLI tutosvideo : assistant interactif (défaut) + sous-commandes."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from tutosvideo import __version__
from tutosvideo.approve import approve, is_approved, require_approved
from tutosvideo.assistant import assistant_loop
from tutosvideo.author import author
from tutosvideo.browser import Runner
from tutosvideo.capture import capture_start, capture_status, capture_stop
from tutosvideo.config import OUTPUT_DIR, SCENARIOS_DIR, Settings, rel_display, scenario_dir
from tutosvideo.discover import discover
from tutosvideo.gui.app import run_gui
from tutosvideo.schema import load_scenario


def cmd_assist(_args: argparse.Namespace) -> None:
    assistant_loop()


def cmd_gui(_args: argparse.Namespace) -> None:
    run_gui()


def cmd_discover(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    sdir = scenario_dir(args.scenario, create=True)
    login = getattr(args, "login", None) or settings.tuto_dolibarr_login or None
    password = getattr(args, "password", None) or None
    if password is None and getattr(args, "use_tuto_password", False):
        password = settings.tuto_password or None
    # Mot de passe Dolibarr dédié (local) si présent
    if password is None:
        import os

        password = os.getenv("TUTO_DOLIBARR_PASSWORD", "").strip() or None
    visits = list(getattr(args, "visit", None) or [])
    out = discover(
        sdir,
        start_url=args.url,
        headless=not args.headed,
        login=login if password else None,
        password=password,
        visit_urls=visits or None,
        skip_mgc_heuristics=bool(getattr(args, "generic", False) or visits or password),
    )
    print(f"Catalogue écrit : {rel_display(out)}")


def cmd_author(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    sdir = scenario_dir(args.scenario)
    auto_approve = bool(getattr(args, "approve", False) or getattr(args, "film", False))
    script, yaml_path, source = author(
        sdir,
        settings,
        objective=args.objective,
        use_llm=not args.no_llm,
        auto_approve=auto_approve,
    )
    print(f"Script brouillon : {rel_display(script)}")
    print(f"Scénario YAML    : {rel_display(yaml_path)}")
    print(f"Source           : {source}")
    if auto_approve:
        print("Script validé automatiquement.")
    else:
        print("Relisez via `tutosvideo` (menu relecture) ou `tutosvideo approve`,", sdir.name)
    if getattr(args, "film", False):
        print("Enchaînement filmage…")
        run_render(
            sdir.name,
            dry_run=bool(getattr(args, "dry_run", False)),
            headed=bool(getattr(args, "headed", False)),
            reuse_audio=bool(getattr(args, "reuse_audio", False)),
            until=getattr(args, "until", None),
            no_burn_subs=bool(getattr(args, "no_burn_subs", False)),
            log=print,
        )


def cmd_pipeline(args: argparse.Namespace) -> None:
    """discover → author → approve → (optionnel) render."""
    settings = Settings.from_env()
    sdir = scenario_dir(args.scenario, create=True)

    if not getattr(args, "skip_discover", False):
        login = getattr(args, "login", None) or settings.tuto_dolibarr_login or None
        password = getattr(args, "password", None) or None
        if password is None and getattr(args, "use_tuto_password", False):
            password = settings.tuto_password or None
        if password is None:
            import os

            password = os.getenv("TUTO_DOLIBARR_PASSWORD", "").strip() or None
        visits = list(getattr(args, "visit", None) or [])
        out = discover(
            sdir,
            start_url=args.url,
            headless=not args.headed,
            login=login if password else None,
            password=password,
            visit_urls=visits or None,
            skip_mgc_heuristics=bool(getattr(args, "generic", False) or visits or password),
        )
        print(f"Discover OK → {rel_display(out)}")

    script, yaml_path, source = author(
        sdir,
        settings,
        objective=args.objective,
        use_llm=not args.no_llm,
        auto_approve=True,
    )
    print(f"Author OK → {rel_display(script)} ({source})")
    print(f"YAML → {rel_display(yaml_path)} (validé)")

    if getattr(args, "film", False):
        print("Enchaînement filmage…")
        run_render(
            sdir.name,
            dry_run=bool(args.dry_run),
            headed=bool(args.headed),
            reuse_audio=bool(getattr(args, "reuse_audio", False)),
            until=getattr(args, "until", None),
            no_burn_subs=bool(getattr(args, "no_burn_subs", False)),
            log=print,
        )
    else:
        print("Pipeline terminé (script validé). Ajoutez --film pour filmer.")


def cmd_rename(args: argparse.Namespace) -> None:
    from tutosvideo.scenario_ops import rename_scenario

    try:
        out = rename_scenario(args.old, args.new)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    print(f"Scénario renommé : {args.old} → {args.new}")
    print(f"Dossier : {rel_display(out)}")


def cmd_approve(args: argparse.Namespace) -> None:
    sdir = scenario_dir(args.scenario)
    path = approve(sdir)
    print(f"Script validé → {rel_display(path)}")


def cmd_status(args: argparse.Namespace) -> None:
    sdir = scenario_dir(args.scenario)
    ok, msg = is_approved(sdir)
    print("approved" if ok else "not-approved", "—", msg)
    cap = capture_status()
    if cap:
        print("capture:", cap)


def cmd_preview(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    sdir = scenario_dir(args.scenario)
    scenario = load_scenario(sdir, settings)
    from tutosvideo.audio_pipeline import load_durations_ms

    try:
        durations = load_durations_ms(scenario)
    except Exception:
        durations = {}
    runner = Runner(
        scenario,
        settings,
        headless=False,
        debug_overlay=True,
        until=args.until,
        record_output=None,
        audio_durations_ms=durations,
    )
    runner.run()
    print("Preview terminée.")


def run_render(
    scenario_name: str,
    *,
    dry_run: bool = False,
    headed: bool = False,
    reuse_audio: bool = False,
    until: str | None = None,
    no_burn_subs: bool = False,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Pipeline TTS (opt.) + capture + mux. ``log`` reçoit les messages de progression."""
    from tutosvideo.audio_pipeline import (
        apply_capture_slots,
        audio_segments_for_mux,
        load_durations_ms,
        refresh_timeline,
        synthesize_all,
    )
    from tutosvideo.mux import concat_audio_to_slots, mux

    _log = log or print
    settings = Settings.from_env()
    sdir = scenario_dir(scenario_name)
    require_approved(sdir)
    if not dry_run:
        settings.require_tuto_account()

    scenario = load_scenario(sdir, settings)
    out_dir = OUTPUT_DIR / scenario.id
    out_dir.mkdir(parents=True, exist_ok=True)

    _log("1/5 — Voix TTS…")
    synthesize_all(
        scenario,
        settings,
        allow_silent=dry_run,
        reuse_existing=reuse_audio,
        log=_log,
    )
    _log("2/5 — Re-mesure des durées audio (ffprobe)…")
    durations_ms = refresh_timeline(scenario, log=_log)
    if not durations_ms:
        durations_ms = load_durations_ms(scenario)

    raw_video = out_dir / "raw.webm"
    stop_at = until
    if dry_run and stop_at is None:
        stop_at = "before_submit"
    n_steps = len(scenario.steps)
    _log(
        f"3/5 — Capture Playwright ({'navigateur visible' if headed else 'headless'}"
        + (f", until={stop_at}" if stop_at else "")
        + f", {n_steps} beats)…"
    )

    def on_beat(step, timeline) -> None:  # noqa: ANN001
        idx = next(
            (i for i, s in enumerate(scenario.steps, start=1) if s.id == step.id),
            "?",
        )
        preview = (step.narrate or "").replace("\n", " ").strip()
        if len(preview) > 70:
            preview = preview[:67] + "…"
        _log(f"   ▶ [{idx}/{n_steps}] {step.id} — {timeline.audio_ms}ms — {preview}")

    runner = Runner(
        scenario,
        settings,
        headless=not headed,
        debug_overlay=False,
        until=stop_at,
        record_output=raw_video,
        audio_durations_ms=durations_ms,
        on_beat_start=on_beat,
    )
    video_path = runner.run()
    if video_path is None or not video_path.is_file():
        raise SystemExit("Vidéo brute absente après capture.")
    _log(f"   Capture OK → {rel_display(video_path)}")

    _log("4/5 — Recalage audio sur les durées de capture…")
    slots = apply_capture_slots(scenario, runner.timeline_log)
    captured_ids = [str(row["step_id"]) for row in runner.timeline_log if row.get("step_id")]
    preps = {
        str(row["step_id"]): int(row.get("prep_ms") or 0)
        for row in runner.timeline_log
        if row.get("step_id")
    }
    segments = audio_segments_for_mux(
        scenario, slots, only_step_ids=captured_ids, prep_ms=preps
    )
    _log(f"   {len(segments)} segments audio à muxer…")
    audio_track = concat_audio_to_slots(segments, out_dir / "narration")

    srt_path = out_dir / "subtitles.srt"
    final_mp4 = out_dir / f"{scenario.id}.mp4"
    _log("5/5 — Mux ffmpeg (vidéo + audio + sous-titres)…")
    mux(video_path, audio_track, srt_path, final_mp4, burn_subtitles=not no_burn_subs)
    _log(f"✓ Vidéo prête : {rel_display(final_mp4)}")
    return final_mp4


def cmd_render(args: argparse.Namespace) -> None:
    run_render(
        args.scenario,
        dry_run=bool(args.dry_run),
        headed=bool(args.headed),
        reuse_audio=bool(getattr(args, "reuse_audio", False)),
        until=getattr(args, "until", None),
        no_burn_subs=bool(getattr(args, "no_burn_subs", False)),
        log=print,
    )


def cmd_capture(args: argparse.Namespace) -> None:
    if args.capture_command == "start":
        state = capture_start(
            backend=args.backend,
            output=Path(args.output),
            size=args.size,
            display=args.display,
        )
        print(json.dumps({"status": "started", **state.__dict__}, indent=2))
    elif args.capture_command == "stop":
        out = capture_stop()
        print(f"Capture arrêtée → {rel_display(out)}")
    elif args.capture_command == "status":
        state = capture_status()
        print(json.dumps(state.__dict__ if state else {"status": "idle"}, indent=2))
    else:
        raise SystemExit("Sous-commande capture : start | stop | status")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tutosvideo",
        description=(
            "Tutoriels vidéo (voix d'abord). Sans argument : interface graphique "
            "(ou assistant terminal si pas d'affichage)."
        ),
    )
    p.add_argument("--version", action="version", version=f"tutosvideo {__version__}")
    sub = p.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help="Interface graphique (écrans Setup / Config / Script / Filmage)")
    gui.set_defaults(func=cmd_gui)

    assist = sub.add_parser("assist", help="Assistant interactif terminal")
    assist.set_defaults(func=cmd_assist)

    d = sub.add_parser("discover", help="Analyser le site et écrire process.json (sans soumettre)")
    d.add_argument("scenario", nargs="?", default="mgc-essentiel")
    d.add_argument("--url", default="https://www.ma-gestion-cloud.fr/")
    d.add_argument("--headed", action="store_true")
    d.add_argument("--login", default=None, help="Login Dolibarr (sinon TUTO_DOLIBARR_LOGIN)")
    d.add_argument(
        "--password",
        default=None,
        help="Mot de passe Dolibarr (sinon TUTO_DOLIBARR_PASSWORD)",
    )
    d.add_argument(
        "--use-tuto-password",
        action="store_true",
        help="Utiliser TUTO_PASSWORD comme mot de passe Dolibarr",
    )
    d.add_argument(
        "--visit",
        action="append",
        default=[],
        help="URL additionnelle à cataloguer (répétable, hash Angular OK)",
    )
    d.add_argument(
        "--generic",
        action="store_true",
        help="Désactiver les heuristiques MGC (Tarifs / inscription)",
    )
    d.set_defaults(func=cmd_discover)

    a = sub.add_parser("author", help="Générer script.md + scenario.yaml (brouillon)")
    a.add_argument("scenario", nargs="?", default="mgc-essentiel")
    a.add_argument(
        "--objective",
        default="Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud",
    )
    a.add_argument("--no-llm", action="store_true", help="Brouillon local sans appeler Mistral")
    a.add_argument(
        "--approve",
        action="store_true",
        help="Valider automatiquement le script après génération",
    )
    a.add_argument(
        "--film",
        action="store_true",
        help="Après génération (+ approve), enchaîner le filmage complet (render)",
    )
    a.add_argument("--headed", action="store_true", help="Navigateur visible (avec --film)")
    a.add_argument("--dry-run", action="store_true", help="Render dry-run (avec --film)")
    a.add_argument("--reuse-audio", action="store_true", help="Réutiliser les mp3 (avec --film)")
    a.add_argument("--until", default=None, help="Checkpoint d'arrêt (avec --film --dry-run)")
    a.add_argument("--no-burn-subs", action="store_true")
    a.set_defaults(func=cmd_author)

    pipe = sub.add_parser(
        "pipeline",
        help="discover → author → approve → (optionnel --film) render",
    )
    pipe.add_argument("scenario", nargs="?", default="mgc-essentiel")
    pipe.add_argument("--url", default="https://www.ma-gestion-cloud.fr/")
    pipe.add_argument("--headed", action="store_true")
    pipe.add_argument("--login", default=None)
    pipe.add_argument("--password", default=None)
    pipe.add_argument("--use-tuto-password", action="store_true")
    pipe.add_argument("--visit", action="append", default=[])
    pipe.add_argument("--generic", action="store_true")
    pipe.add_argument(
        "--objective",
        default="Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud",
    )
    pipe.add_argument("--no-llm", action="store_true")
    pipe.add_argument(
        "--skip-discover",
        action="store_true",
        help="Réutiliser process.json existant",
    )
    pipe.add_argument(
        "--film",
        action="store_true",
        help="Enchaîner le filmage après validation du script",
    )
    pipe.add_argument("--dry-run", action="store_true")
    pipe.add_argument("--reuse-audio", action="store_true")
    pipe.add_argument("--until", default=None)
    pipe.add_argument("--no-burn-subs", action="store_true")
    pipe.set_defaults(func=cmd_pipeline)

    rn = sub.add_parser("rename", help="Renommer un scénario (dossier + YAML id + BDD + output)")
    rn.add_argument("old", help="Identifiant actuel")
    rn.add_argument("new", help="Nouvel identifiant (slug)")
    rn.set_defaults(func=cmd_rename)

    ap = sub.add_parser("approve", help="Valider script.md (hash) avant TTS/vidéo")
    ap.add_argument("scenario", nargs="?", default="mgc-essentiel")
    ap.set_defaults(func=cmd_approve)

    st = sub.add_parser("status", help="État validation / capture")
    st.add_argument("scenario", nargs="?", default="mgc-essentiel")
    st.set_defaults(func=cmd_status)

    pr = sub.add_parser("preview", help="Jouer le scénario sans TTS/mux (overlay rythme)")
    pr.add_argument("scenario", nargs="?", default="mgc-essentiel")
    pr.add_argument("--until", default="before_submit", help="Checkpoint d'arrêt (défaut: before_submit)")
    pr.set_defaults(func=cmd_preview)

    r = sub.add_parser("render", help="TTS + capture + mux (exige script validé)")
    r.add_argument("scenario", nargs="?", default="mgc-essentiel")
    r.add_argument("--headed", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="Silence TTS + stop before_submit")
    r.add_argument("--reuse-audio", action="store_true", help="Ne régénère pas les mp3 déjà présents")
    r.add_argument("--until", default=None)
    r.add_argument("--no-burn-subs", action="store_true")
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("capture", help="Utilitaire de capture vidéo CLI")
    c_sub = c.add_subparsers(dest="capture_command", required=True)
    cs = c_sub.add_parser("start")
    cs.add_argument("--backend", choices=["playwright", "ffmpeg"], default="playwright")
    cs.add_argument("--output", default="output/raw.webm")
    cs.add_argument("--size", default="1920x1080")
    cs.add_argument("--display", default=None)
    cs.set_defaults(func=cmd_capture)
    c_sub.add_parser("stop").set_defaults(func=cmd_capture)
    c_sub.add_parser("status").set_defaults(func=cmd_capture)

    return p


def main(argv: list[str] | None = None) -> None:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        import os

        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            run_gui()
        else:
            assistant_loop()
        return
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        run_gui()
        return
    args.func(args)


if __name__ == "__main__":
    main()
