"""Écrans de l'interface graphique TutosVideo."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from tutosvideo.approve import SCRIPT_NAME, approve, is_approved
from tutosvideo.assistant import (
    list_scenarios,
    parse_script_paragraphs,
    sync_yaml_narrates,
    write_script_md,
)
from tutosvideo.author import author
from tutosvideo.browser import Runner
from tutosvideo.config import OUTPUT_DIR, ROOT, Settings, rel_display, scenario_dir
from tutosvideo.discover import DEFAULT_START, discover
from tutosvideo.envfile import iter_env_groups, read_env_map, write_env_map
from tutosvideo.gui.widgets import (
    append_log,
    clear_log,
    labeled_entry,
    make_scroll_text,
    make_scrollable,
    run_in_thread,
    set_status_badge,
    status_badge,
)
from tutosvideo.scenario_prompts import (
    ensure_prompt_files,
    extract_objective_title,
    load_author_prompt,
    load_default_author_prompt,
    load_default_discover_prompt,
    load_discover_prompt,
    save_author_prompt,
    save_default_author_prompt,
    save_default_discover_prompt,
    save_discover_prompt,
)
from tutosvideo.db import get_scenario_status, init_db
from tutosvideo.setup_ops import run_setup, setup_status


class BaseScreen(ttk.Frame):
    title = "Écran"

    def __init__(self, master: tk.Misc, app: "AppProtocol") -> None:
        super().__init__(master, padding=12)
        self.app = app
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def on_show(self) -> None:
        pass


class AppProtocol:
    """Protocole minimal pour typer l'app sans import circulaire."""

    root: tk.Tk
    scenario_var: tk.StringVar

    def set_status(self, text: str) -> None: ...
    def refresh_scenario_lists(self) -> None: ...
    def current_scenario_dir(self) -> Path: ...


class HomeScreen(BaseScreen):
    title = "Accueil"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="TutosVideo", font=("TkDefaultFont", 16, "bold")).pack(
            side="left", anchor="w"
        )
        self.badge = status_badge(head)
        self.badge.pack(side="right")
        ttk.Label(
            self,
            text="Découvrir → rédiger → valider → filmer (rythme voix-d'abord).",
        ).pack(anchor="w", pady=(0, 12))
        self.info = ttk.Label(self, justify="left")
        self.info.pack(anchor="w", fill="x")
        ttk.Button(self, text="Actualiser l'état", command=self.on_show).pack(anchor="w", pady=8)

    def on_show(self) -> None:
        init_db()
        st = setup_status()
        sdir = self.app.current_scenario_dir()
        status = get_scenario_status(sdir.name)
        ok, msg = is_approved(sdir) if (sdir / "script.md").is_file() else (False, "pas de script")
        lines = [
            f"Projet : {st['root']}",
            f"Python : {st['python']}",
            f"venv   : {'oui' if st['venv'] else 'non — passez par Setup'}",
            f".env   : {'oui' if st['env_file'] else 'non — passez par Configuration'}",
            f"ffmpeg : {'oui' if st['ffmpeg'] else 'non'}",
            f"BDD    : data/tutosvideo.db (prompts)",
            f"Scénario : {self.app.scenario_var.get()}",
            f"Script : {status.upper()} — {msg}",
        ]
        self.info.configure(text="\n".join(lines))
        if hasattr(self, "badge"):
            set_status_badge(self.badge, status)


class SetupScreen(BaseScreen):
    title = "Setup"

    def build(self) -> None:
        ttk.Label(
            self,
            text="Installation locale (venv + package + Chromium Playwright).",
        ).pack(anchor="w")
        self.playwright_var = tk.BooleanVar(value=True)
        self.dev_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Installer Chromium (Playwright)", variable=self.playwright_var).pack(
            anchor="w", pady=2
        )
        ttk.Checkbutton(self, text="Extras dev (pytest)", variable=self.dev_var).pack(anchor="w", pady=2)
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=8)
        self.run_btn = ttk.Button(btns, text="Lancer le setup", command=self._run)
        self.run_btn.pack(side="left")
        ttk.Button(btns, text="Vérifier", command=self._check).pack(side="left", padx=6)
        frame, self.log = make_scroll_text(self, height=18)
        frame.pack(fill="both", expand=True, pady=8)
        clear_log(self.log)

    def _check(self) -> None:
        st = setup_status()
        clear_log(self.log)
        append_log(self.log, f"venv={st['venv']}  .env={st['env_file']}  ffmpeg={st['ffmpeg']}")
        append_log(self.log, f"root={st['root']}")

    def _run(self) -> None:
        self.run_btn.configure(state="disabled")
        clear_log(self.log)
        self.app.set_status("Setup en cours…")

        def work() -> None:
            def log(msg: str) -> None:
                self.after(0, lambda m=msg: append_log(self.log, m))

            run_setup(
                log,
                with_playwright=self.playwright_var.get(),
                with_dev=self.dev_var.get(),
            )

        def done(err: Exception | None) -> None:
            self.run_btn.configure(state="normal")
            if err:
                append_log(self.log, f"ERREUR : {err}")
                self.app.set_status("Setup échoué")
                messagebox.showerror("Setup", str(err))
            else:
                self.app.set_status("Setup terminé")
                messagebox.showinfo("Setup", "Installation terminée.")

        run_in_thread(work, lambda e: self.after(0, lambda: done(e)))


class ConfigScreen(BaseScreen):
    title = "Configuration"

    def build(self) -> None:
        ttk.Label(
            self,
            text=(
                "Secrets et paramètres (.env) — jamais envoyés au navigateur.\n"
                "Les nouvelles variables ajoutées au catalogue ou au .env apparaissent ici "
                "automatiquement, regroupées par thème."
            ),
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        self.vars: dict[str, tk.StringVar] = {}
        btns = ttk.Frame(self)
        btns.pack(side="bottom", fill="x", pady=10)
        ttk.Button(btns, text="Recharger", command=self.on_show).pack(side="left")
        ttk.Button(btns, text="Enregistrer .env", command=self._save).pack(side="left", padx=6)
        self.form, self._refresh_scroll = make_scrollable(self)
        self._rebuild_form()

    def _rebuild_form(self) -> None:
        for child in self.form.winfo_children():
            child.destroy()
        self.vars.clear()
        values = read_env_map()
        for _gid, title, fields in iter_env_groups():
            box = ttk.LabelFrame(self.form, text=title, padding=8)
            box.pack(fill="x", pady=(0, 8), padx=2)
            for field in fields:
                var = tk.StringVar(value=values.get(field.key, field.default))
                self.vars[field.key] = var
                labeled_entry(box, field.label, var, secret=field.secret)
        self._refresh_scroll()

    def on_show(self) -> None:
        self._rebuild_form()

    def _save(self) -> None:
        values = {k: v.get().strip() for k, v in self.vars.items()}
        path = write_env_map(values)
        self.app.set_status(f".env enregistré ({rel_display(path)})")
        messagebox.showinfo("Configuration", f"Enregistré : {rel_display(path)}")
        self.on_show()


class ScenarioScreen(BaseScreen):
    title = "Scénario"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Choisir ou créer le scénario de travail.").pack(
            side="left", anchor="w"
        )
        self.badge = status_badge(head)
        self.badge.pack(side="right")

        row = ttk.Frame(self)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Scénario actif").pack(side="left")
        self.combo = ttk.Combobox(row, textvariable=self.app.scenario_var, width=36)
        self.combo.pack(side="left", padx=8)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self.on_show())
        ttk.Button(row, text="Actualiser", command=self.on_show).pack(side="left")

        create = ttk.LabelFrame(self, text="Nouveau (copie les prompts par défaut)", padding=8)
        create.pack(fill="x", pady=8)
        self.new_var = tk.StringVar(value="mgc-essentiel")
        labeled_entry(create, "Identifiant (slug)", self.new_var, width=30)
        ttk.Button(create, text="Créer / ouvrir", command=self._create).pack(anchor="w", pady=4)

        rename = ttk.LabelFrame(self, text="Renommer le scénario actif", padding=8)
        rename.pack(fill="x", pady=4)
        self.rename_var = tk.StringVar()
        labeled_entry(rename, "Nouveau identifiant", self.rename_var, width=30)
        ttk.Button(rename, text="Renommer", command=self._rename).pack(anchor="w", pady=4)

        self.detail = ttk.Label(self, justify="left")
        self.detail.pack(anchor="w", fill="x", pady=8)

        voice = ttk.LabelFrame(
            self,
            text="Voix TTS du scénario (API Mistral — langue / humeur / voix)",
            padding=8,
        )
        voice.pack(fill="x", pady=8)
        self._voices: list = []
        self._voice_by_label: dict[str, object] = {}
        row1 = ttk.Frame(voice)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Langue", width=12).pack(side="left")
        self.lang_var = tk.StringVar()
        self.lang_combo = ttk.Combobox(row1, textvariable=self.lang_var, width=18, state="readonly")
        self.lang_combo.pack(side="left", padx=4)
        self.lang_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_lang_change())
        ttk.Label(row1, text="Humeur", width=10).pack(side="left", padx=(12, 0))
        self.mood_var = tk.StringVar()
        self.mood_combo = ttk.Combobox(row1, textvariable=self.mood_var, width=18, state="readonly")
        self.mood_combo.pack(side="left", padx=4)
        self.mood_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_mood_change())
        row2 = ttk.Frame(voice)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="Voix", width=12).pack(side="left")
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(row2, textvariable=self.voice_var, width=56, state="readonly")
        self.voice_combo.pack(side="left", padx=4, fill="x", expand=True)
        row3 = ttk.Frame(voice)
        row3.pack(fill="x", pady=4)
        ttk.Button(row3, text="Charger les voix (API)", command=self._load_voices).pack(side="left")
        ttk.Button(row3, text="Enregistrer pour ce scénario", command=self._save_voice).pack(
            side="left", padx=6
        )
        ttk.Button(row3, text="Utiliser la voix config (défaut)", command=self._use_default_voice).pack(
            side="left"
        )
        self.voice_info = ttk.Label(voice, text="", justify="left")
        self.voice_info.pack(anchor="w", pady=2)

        defaults = ttk.LabelFrame(
            self,
            text="Prompts par défaut (BDD) — utilisés à la création d'un nouveau scénario",
            padding=8,
        )
        defaults.pack(fill="both", expand=True, pady=8)
        ttk.Label(defaults, text="Brief découverte par défaut").pack(anchor="w")
        dframe, self.default_discover = make_scroll_text(defaults, height=6)
        dframe.pack(fill="both", expand=True)
        self.default_discover.configure(state="normal")
        ttk.Label(defaults, text="Prompt authoring par défaut").pack(anchor="w", pady=(6, 0))
        aframe, self.default_author = make_scroll_text(defaults, height=6)
        aframe.pack(fill="both", expand=True)
        self.default_author.configure(state="normal")
        ttk.Button(
            defaults, text="Enregistrer les prompts par défaut", command=self._save_defaults
        ).pack(anchor="w", pady=6)

    def on_show(self) -> None:
        init_db()
        names = list_scenarios()
        self.combo["values"] = names
        if names and self.app.scenario_var.get() not in names:
            self.app.scenario_var.set(names[0])
        self._show_detail()
        self.default_discover.delete("1.0", "end")
        self.default_discover.insert("1.0", load_default_discover_prompt())
        self.default_author.delete("1.0", "end")
        self.default_author.insert("1.0", load_default_author_prompt())
        self._load_voice_from_yaml()

    def _load_voice_from_yaml(self) -> None:
        sdir = self.app.current_scenario_dir()
        yml = sdir / "scenario.yaml"
        info = "Voix : (défaut configuration MISTRAL_VOICE_ID)"
        if yml.is_file():
            import yaml

            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            v = data.get("voice") or {}
            vid = str(v.get("voice_id") or "")
            if vid and not vid.startswith("env:"):
                lang = str(v.get("language") or "")
                mood = str(v.get("mood") or "")
                name = str(v.get("name") or vid)
                if lang:
                    self.lang_var.set(lang)
                if mood:
                    self.mood_var.set(mood)
                info = f"Voix scénario : {name} · {lang} · {mood} · id={vid}"
            else:
                info = "Voix : défaut configuration (MISTRAL_VOICE_ID)"
        self.voice_info.configure(text=info)

    def _load_voices(self) -> None:
        from tutosvideo.voices import (
            filter_voices,
            languages_from_voices,
            list_mistral_voices,
            moods_for_language,
        )

        try:
            settings = Settings.from_env()
            settings.require_mistral()
            self._voices = list_mistral_voices(settings)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Voix", str(exc))
            return
        langs = languages_from_voices(self._voices)
        self.lang_combo["values"] = langs
        if langs and self.lang_var.get() not in langs:
            # préférer fr_fr
            prefer = next((L for L in langs if L.startswith("fr")), langs[0])
            self.lang_var.set(prefer)
        self._on_lang_change()
        self.voice_info.configure(text=f"{len(self._voices)} voix chargées depuis Mistral.")

    def _on_lang_change(self) -> None:
        from tutosvideo.voices import moods_for_language

        lang = self.lang_var.get()
        moods = moods_for_language(self._voices, lang) if self._voices else []
        self.mood_combo["values"] = moods
        if moods and self.mood_var.get() not in moods:
            prefer = "neutral" if "neutral" in moods else moods[0]
            self.mood_var.set(prefer)
        self._on_mood_change()

    def _on_mood_change(self) -> None:
        from tutosvideo.voices import filter_voices

        filtered = filter_voices(
            self._voices,
            language=self.lang_var.get() or None,
            mood=self.mood_var.get() or None,
        )
        labels = [v.label for v in filtered]
        self._voice_by_label = {v.label: v for v in filtered}
        self.voice_combo["values"] = labels
        if labels:
            cur = self.voice_var.get()
            if cur not in labels:
                self.voice_var.set(labels[0])

    def _save_voice(self) -> None:
        from tutosvideo.voices import update_scenario_voice_yaml, voice_selection_dict

        label = self.voice_var.get()
        voice = self._voice_by_label.get(label)
        if voice is None:
            messagebox.showwarning("Voix", "Chargez les voix et choisissez une entrée.")
            return
        sdir = self.app.current_scenario_dir()
        yml = sdir / "scenario.yaml"
        if not yml.is_file():
            messagebox.showwarning(
                "Voix",
                "scenario.yaml absent — générez d'abord un script pour ce scénario.",
            )
            return
        settings = Settings.from_env()
        update_scenario_voice_yaml(
            yml,
            voice_selection_dict(
                voice_id=voice.voice_id,
                language=self.lang_var.get(),
                mood=self.mood_var.get(),
                name=voice.name,
                model=settings.mistral_tts_model or "voxtral-mini-tts-2603",
            ),
        )
        self._load_voice_from_yaml()
        messagebox.showinfo("Voix", f"Voix enregistrée pour « {sdir.name} » : {voice.name}")

    def _use_default_voice(self) -> None:
        from tutosvideo.voices import update_scenario_voice_yaml

        sdir = self.app.current_scenario_dir()
        yml = sdir / "scenario.yaml"
        if not yml.is_file():
            messagebox.showwarning("Voix", "scenario.yaml absent.")
            return
        update_scenario_voice_yaml(
            yml,
            {
                "provider": "mistral",
                "model": Settings.from_env().mistral_tts_model or "voxtral-mini-tts-2603",
                "voice_id": "env:MISTRAL_VOICE_ID",
                "language": "",
                "mood": "",
                "name": "",
            },
        )
        self._load_voice_from_yaml()
        messagebox.showinfo("Voix", "Ce scénario utilisera la voix par défaut de la configuration.")

    def _save_defaults(self) -> None:
        save_default_discover_prompt(self.default_discover.get("1.0", "end"))
        save_default_author_prompt(self.default_author.get("1.0", "end"))
        messagebox.showinfo(
            "Scénario",
            "Prompts par défaut enregistrés en BDD.\n"
            "Ils seront copiés à la création des prochains scénarios.",
        )

    def _create(self) -> None:
        name = self.new_var.get().strip()
        if not name:
            messagebox.showwarning("Scénario", "Identifiant requis.")
            return
        sdir = scenario_dir(name, create=True)
        ensure_prompt_files(sdir)  # copie depuis BDD defaults
        self.app.scenario_var.set(name)
        self.app.refresh_scenario_lists()
        self.on_show()
        self.app.set_status(f"Scénario {name}")

    def _rename(self) -> None:
        from tutosvideo.scenario_ops import rename_scenario

        old = self.app.scenario_var.get().strip()
        new = self.rename_var.get().strip()
        if not old:
            messagebox.showwarning("Scénario", "Aucun scénario actif.")
            return
        if not new:
            messagebox.showwarning("Scénario", "Nouveau identifiant requis.")
            return
        if not messagebox.askyesno(
            "Renommer",
            f"Renommer « {old} » en « {new} » ?\n"
            "(dossier scenarios/, id YAML, BDD, dossier output/)",
        ):
            return
        try:
            sdir = rename_scenario(old, new)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Renommer", str(exc))
            return
        self.app.scenario_var.set(new)
        self.rename_var.set("")
        self.app.refresh_scenario_lists()
        self.on_show()
        self.app.set_status(f"Renommé → {new}")
        messagebox.showinfo("Scénario", f"Renommé :\n{rel_display(sdir)}")

    def _show_detail(self) -> None:
        sdir = self.app.current_scenario_dir()
        status = get_scenario_status(sdir.name)
        set_status_badge(self.badge, status)
        files = [
            "process.json",
            "script.md",
            "scenario.yaml",
            "validation.json",
            "prompt.md",
            "discover_prompt.md",
        ]
        lines = [
            f"Dossier : {rel_display(sdir)}",
            f"Validation script : {status.upper()}",
        ]
        for name in files:
            lines.append(f"  {'✓' if (sdir / name).is_file() else '·'} {name}")
        self.detail.configure(text="\n".join(lines))


class DiscoverScreen(BaseScreen):
    title = "Découverte"

    def build(self) -> None:
        ttk.Label(
            self,
            text=(
                "Analyse automatique du site (catalogue d'actions, sans soumettre).\n"
                "Le brief ci-dessous est stocké en BDD (et miroir discover_prompt.md) "
                "pour ce scénario — indépendant des autres."
            ),
            justify="left",
        ).pack(anchor="w")
        self.url_var = tk.StringVar(value=DEFAULT_START)
        labeled_entry(self, "URL de départ", self.url_var)
        prompt_box = ttk.LabelFrame(self, text="Brief de découverte (prompt)", padding=8)
        prompt_box.pack(fill="both", expand=True, pady=8)
        frame, self.prompt_editor = make_scroll_text(prompt_box, height=12)
        frame.pack(fill="both", expand=True)
        self.prompt_editor.configure(state="normal")
        tools = ttk.Frame(self)
        tools.pack(fill="x")
        self.headed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="Navigateur visible", variable=self.headed_var).pack(
            side="left"
        )
        ttk.Button(tools, text="Enregistrer le brief", command=self._save_prompt).pack(
            side="left", padx=6
        )
        self.btn = ttk.Button(tools, text="Lancer discover", command=self._run)
        self.btn.pack(side="right")
        log_frame, self.log = make_scroll_text(self, height=8)
        log_frame.pack(fill="both", expand=True, pady=6)

    def on_show(self) -> None:
        sdir = self.app.current_scenario_dir()
        ensure_prompt_files(sdir)
        text = load_discover_prompt(sdir)
        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", text)

    def _save_prompt(self) -> None:
        sdir = self.app.current_scenario_dir()
        path = save_discover_prompt(sdir, self.prompt_editor.get("1.0", "end"))
        self.app.set_status(f"Brief enregistré ({rel_display(path)})")
        messagebox.showinfo("Découverte", f"Brief enregistré : {rel_display(path)}")

    def _run(self) -> None:
        sdir = self.app.current_scenario_dir()
        url = self.url_var.get().strip() or DEFAULT_START
        headed = self.headed_var.get()
        brief = self.prompt_editor.get("1.0", "end").strip()
        save_discover_prompt(sdir, brief)
        self.btn.configure(state="disabled")
        clear_log(self.log)
        append_log(self.log, f"Scénario {sdir.name} — {url}")

        def work() -> None:
            out = discover(sdir, start_url=url, headless=not headed, brief=brief)
            aliases = []
            try:
                import json

                data = json.loads(out.read_text(encoding="utf-8"))
                aliases = list(data.get("view_aliases") or [])
            except Exception:
                pass

            def report() -> None:
                append_log(self.log, f"OK → {rel_display(out)}")
                if aliases:
                    append_log(
                        self.log,
                        f"{len(aliases)} vue(s) en double ignorée(s) / aliasées.",
                    )
                    for a in aliases[:8]:
                        append_log(
                            self.log,
                            f"  · {a.get('id') or '?'} → {a.get('same_as')} ({a.get('url', '')[:80]})",
                        )

            self.after(0, report)

        def done(err: Exception | None) -> None:
            self.btn.configure(state="normal")
            if err:
                append_log(self.log, f"ERREUR : {err}")
                messagebox.showerror("Discover", str(err))
            else:
                self.app.set_status("Discover terminé")

        run_in_thread(work, lambda e: self.after(0, lambda: done(e)))


class ScriptScreen(BaseScreen):
    title = "Script"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Script", font=("TkDefaultFont", 14, "bold")).pack(side="left")
        self.badge = status_badge(head)
        self.badge.pack(side="right")
        self.status_label = ttk.Label(self, text="", justify="left")
        self.status_label.pack(anchor="w", pady=(4, 0))

        top = ttk.LabelFrame(self, text="Rédaction (prompts)", padding=8)
        top.pack(fill="both", expand=False, pady=(8, 4))
        self.objective_var = tk.StringVar(
            value="Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud"
        )
        labeled_entry(top, "Titre / objectif court", self.objective_var)
        ttk.Label(
            top,
            text="Prompt détaillé (BDD / prompt.md) — parcours et contraintes pour ce scénario :",
        ).pack(anchor="w", pady=(6, 2))
        pframe, self.prompt_editor = make_scroll_text(top, height=8)
        pframe.pack(fill="both", expand=True)
        self.prompt_editor.configure(state="normal")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=4)
        self.llm_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Utiliser Mistral (sinon brouillon local)", variable=self.llm_var).pack(
            side="left"
        )
        ttk.Button(row, text="Enregistrer le prompt", command=self._save_prompt).pack(
            side="left", padx=6
        )
        ttk.Button(row, text="Générer le script", command=self._author).pack(side="right")

        opts = ttk.Frame(top)
        opts.pack(fill="x", pady=(0, 4))
        self.auto_approve_var = tk.BooleanVar(value=False)
        self.chain_film_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts,
            text="Valider automatiquement après génération",
            variable=self.auto_approve_var,
            command=self._on_auto_opts,
        ).pack(side="left")
        ttk.Checkbutton(
            opts,
            text="Enchaîner le filmage complet",
            variable=self.chain_film_var,
            command=self._on_auto_opts,
        ).pack(side="left", padx=12)
        self.chain_dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Filmage dry-run", variable=self.chain_dry_var
        ).pack(side="left")

        review = ttk.LabelFrame(self, text="Relecture (texte entendu)", padding=8)
        review.pack(fill="both", expand=True, pady=8)
        self.idx_label = ttk.Label(review, text="—")
        self.idx_label.pack(anchor="w")
        frame, self.editor = make_scroll_text(review, height=6)
        frame.pack(fill="both", expand=True)
        self.editor.configure(state="normal")
        nav = ttk.Frame(review)
        nav.pack(fill="x", pady=4)
        ttk.Button(nav, text="⟵ Précédent", command=self._prev).pack(side="left")
        ttk.Button(nav, text="Suivant ⟶", command=self._next).pack(side="left", padx=4)
        ttk.Button(nav, text="Supprimer", command=self._delete).pack(side="left", padx=4)
        ttk.Button(nav, text="Enregistrer brouillon", command=self._save_draft).pack(side="left", padx=4)
        ttk.Button(nav, text="Valider (approve)", command=self._approve).pack(side="right")

        self._paragraphs: list[str] = []
        self._index = 0
        self._title = "Tutoriel"

    def on_show(self) -> None:
        sdir = self.app.current_scenario_dir()
        ensure_prompt_files(sdir)
        prompt = load_author_prompt(sdir)
        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", prompt)
        title = extract_objective_title(prompt, self.objective_var.get())
        if title:
            self.objective_var.set(title)
        self._load()

    def _refresh_status(self) -> None:
        sdir = self.app.current_scenario_dir()
        status = get_scenario_status(sdir.name)
        ok, msg = is_approved(sdir) if (sdir / SCRIPT_NAME).is_file() else (False, "pas de script")
        set_status_badge(self.badge, status)
        detail = f"Script du scénario « {sdir.name} » : {status.upper()}"
        if ok:
            detail += " — prêt pour le filmage."
        else:
            detail += f" — {msg}"
        self.status_label.configure(text=detail)
        self.app.set_status(f"Script — {status}")

    def _save_prompt(self) -> None:
        sdir = self.app.current_scenario_dir()
        path = save_author_prompt(sdir, self.prompt_editor.get("1.0", "end"))
        self.app.set_status(f"Prompt enregistré ({rel_display(path)})")
        messagebox.showinfo("Script", f"Prompt enregistré : {rel_display(path)}")

    def _load(self) -> None:
        sdir = self.app.current_scenario_dir()
        script = sdir / SCRIPT_NAME
        yaml_path = sdir / "scenario.yaml"
        self._title = self.objective_var.get().strip() or "Tutoriel"
        self._paragraphs = []
        if script.is_file():
            text = script.read_text(encoding="utf-8")
            lines = text.splitlines()
            if lines and lines[0].startswith("# Script"):
                self._title = lines[0].replace("# Script —", "").strip() or self._title
            self._paragraphs = parse_script_paragraphs(text)
        if not self._paragraphs and yaml_path.is_file():
            import yaml

            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            self._paragraphs = [
                str(s.get("narrate") or "").strip()
                for s in data.get("steps") or []
                if str(s.get("narrate") or "").strip()
            ]
        self._index = 0
        self._show_current()
        self._refresh_status()

    def _show_current(self) -> None:
        self.editor.delete("1.0", "end")
        if not self._paragraphs:
            self.idx_label.configure(text="Aucun paragraphe — générez un script.")
            return
        self._index = max(0, min(self._index, len(self._paragraphs) - 1))
        self.idx_label.configure(text=f"Paragraphe {self._index + 1} / {len(self._paragraphs)}")
        self.editor.insert("1.0", self._paragraphs[self._index])

    def _commit_editor(self) -> None:
        if not self._paragraphs:
            return
        self._paragraphs[self._index] = self.editor.get("1.0", "end").strip()

    def _prev(self) -> None:
        self._commit_editor()
        self._index = max(0, self._index - 1)
        self._show_current()

    def _next(self) -> None:
        self._commit_editor()
        if self._paragraphs:
            self._index = min(len(self._paragraphs) - 1, self._index + 1)
        self._show_current()

    def _delete(self) -> None:
        if not self._paragraphs:
            return
        self._commit_editor()
        del self._paragraphs[self._index]
        if self._index >= len(self._paragraphs):
            self._index = max(0, len(self._paragraphs) - 1)
        self._show_current()

    def _save_draft(self) -> None:
        self._commit_editor()
        sdir = self.app.current_scenario_dir()
        write_script_md(
            sdir / SCRIPT_NAME, self._title, self._paragraphs, status="brouillon"
        )
        sync_yaml_narrates(sdir / "scenario.yaml", self._paragraphs)
        # invalider validation
        v = sdir / "validation.json"
        if v.is_file():
            v.unlink()
        self._refresh_status()
        self.app.set_status("Brouillon script enregistré")
        messagebox.showinfo("Script", "Brouillon enregistré (statut : brouillon).")

    def _approve(self) -> None:
        self._commit_editor()
        sdir = self.app.current_scenario_dir()
        write_script_md(
            sdir / SCRIPT_NAME, self._title, self._paragraphs, status="brouillon"
        )
        sync_yaml_narrates(sdir / "scenario.yaml", self._paragraphs)
        if not self._paragraphs:
            messagebox.showwarning("Script", "Rien à valider.")
            return
        out = approve(sdir)
        self._refresh_status()
        self.app.set_status("Script validé")
        messagebox.showinfo("Script", f"Validé : {rel_display(out)}")

    def _on_auto_opts(self) -> None:
        # Enchaîner le filmage implique une validation auto
        if self.chain_film_var.get():
            self.auto_approve_var.set(True)

    def _author(self) -> None:
        sdir = self.app.current_scenario_dir()
        if not (sdir / "process.json").is_file():
            if not messagebox.askyesno(
                "Script", "process.json absent. Continuer quand même (recommandé : Discover avant) ?"
            ):
                return
        save_author_prompt(sdir, self.prompt_editor.get("1.0", "end"))
        settings = Settings.from_env()
        objective = self.objective_var.get().strip()
        use_llm = self.llm_var.get() and bool(settings.mistral_api_key)
        auto_approve = bool(self.auto_approve_var.get() or self.chain_film_var.get())
        chain_film = bool(self.chain_film_var.get())
        chain_dry = bool(self.chain_dry_var.get())

        if chain_film and not chain_dry:
            if not messagebox.askyesno(
                "Filmage",
                "Le filmage complet peut créer une instance / modifier des données. Continuer ?",
            ):
                return

        def work() -> str:
            _path_script, _path_yaml, source = author(
                sdir,
                settings,
                objective=objective,
                use_llm=use_llm,
                auto_approve=auto_approve,
            )
            return source

        def done(err: Exception | None, source: str | None = None) -> None:
            if err:
                messagebox.showerror("Rédaction", str(err))
                return
            self._load()
            src = source or "?"
            bits = []
            if str(src).startswith("llm"):
                bits.append("Script généré avec Mistral.")
            else:
                bits.append(f"Script généré en brouillon LOCAL ({src}).")
            if auto_approve:
                bits.append("Validé automatiquement.")
            if "dedupe" in str(src):
                bits.append("Vues redondantes fusionnées.")
            self.app.set_status(
                "Script généré" + (" + validé" if auto_approve else " (brouillon)")
            )
            if chain_film:
                messagebox.showinfo(
                    "Rédaction",
                    "\n".join(bits) + "\n\nEnchaînement vers le filmage…",
                )
                self._start_chained_film(dry_run=chain_dry)
            else:
                if str(src).startswith("llm"):
                    messagebox.showinfo(
                        "Rédaction",
                        "\n".join(bits)
                        + (
                            ""
                            if auto_approve
                            else "\n\nRelisez puis validez (ou cochez « Valider automatiquement »)."
                        ),
                    )
                else:
                    messagebox.showwarning(
                        "Rédaction",
                        "\n".join(bits)
                        + "\n\nVérifiez la case « Utiliser Mistral » et la clé API si besoin.",
                    )

        self.app.set_status("Rédaction…")

        def wrapper() -> None:
            err = None
            source = None
            try:
                source = work()
            except Exception as exc:  # noqa: BLE001
                err = exc
            self.after(0, lambda: done(err, source))

        import threading

        threading.Thread(target=wrapper, daemon=True).start()

    def _start_chained_film(self, *, dry_run: bool) -> None:
        """Bascule sur l'écran Filmage et lance render (voix + capture + mux)."""
        try:
            self.app.show_screen("Filmage")
        except Exception:
            pass
        film = getattr(self.app, "_screens", {}).get("Filmage")
        if film is None or not hasattr(film, "_render_chained"):
            messagebox.showwarning(
                "Filmage",
                "Script prêt. Ouvrez l'écran Filmage et lancez le filmage manuellement.",
            )
            return
        film.after(200, lambda: film._render_chained(dry_run=dry_run))


class FilmScreen(BaseScreen):
    title = "Filmage"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(
            head,
            text="1) Générer / écouter / régénérer les voix  ·  2) Filmer (un seul onglet navigateur).",
        ).pack(side="left", anchor="w")
        self.badge = status_badge(head)
        self.badge.pack(side="right")
        self.status_line = ttk.Label(self, text="")
        self.status_line.pack(anchor="w", pady=(4, 6))

        audio_box = ttk.LabelFrame(self, text="Voix par paragraphe", padding=8)
        audio_box.pack(fill="both", expand=True, pady=6)
        tools = ttk.Frame(audio_box)
        tools.pack(fill="x")
        ttk.Button(tools, text="Générer toutes les voix", command=self._gen_all).pack(
            side="left"
        )
        ttk.Button(tools, text="Actualiser la liste", command=self._refresh_audio_list).pack(
            side="left", padx=4
        )
        self.silent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="Silence (dry TTS)", variable=self.silent_var).pack(
            side="left", padx=8
        )

        list_frame = ttk.Frame(audio_box)
        list_frame.pack(fill="both", expand=True, pady=4)
        self.audio_list = tk.Listbox(list_frame, height=10, exportselection=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.audio_list.yview)
        self.audio_list.configure(yscrollcommand=scroll.set)
        self.audio_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.audio_list.bind("<<ListboxSelect>>", self._on_select_audio)

        detail = ttk.Frame(audio_box)
        detail.pack(fill="x", pady=4)
        self.audio_detail = ttk.Label(detail, text="Aucun audio sélectionné", wraplength=640)
        self.audio_detail.pack(anchor="w", fill="x")
        row = ttk.Frame(audio_box)
        row.pack(fill="x")
        ttk.Button(row, text="▶ Écouter", command=self._play_selected).pack(side="left")
        ttk.Button(row, text="⟳ Régénérer", command=self._regen_selected).pack(
            side="left", padx=4
        )

        film = ttk.LabelFrame(self, text="Vidéo", padding=8)
        film.pack(fill="x", pady=6)
        self.until_var = tk.StringVar(value="before_submit")
        labeled_entry(film, "Arrêt preview (--until)", self.until_var, width=24)
        self.headed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            film, text="Navigateur visible (render)", variable=self.headed_var
        ).pack(anchor="w")
        btns = ttk.Frame(film)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Preview", command=self._preview).pack(side="left")
        ttk.Button(
            btns, text="Filmer (réutilise les voix)", command=lambda: self._render(False)
        ).pack(side="left", padx=4)
        ttk.Button(
            btns, text="Filmer dry-run", command=lambda: self._render(True)
        ).pack(side="left", padx=4)

        frame, self.log = make_scroll_text(self, height=8)
        frame.pack(fill="both", expand=True)
        clear_log(self.log)
        self._beats: list = []
        self._player = None

    def on_show(self) -> None:
        sdir = self.app.current_scenario_dir()
        status = get_scenario_status(sdir.name)
        set_status_badge(self.badge, status)
        ok, msg = is_approved(sdir) if (sdir / SCRIPT_NAME).is_file() else (False, "pas de script")
        if ok:
            self.status_line.configure(
                text=f"Scénario « {sdir.name} » : VALIDÉ — filmage autorisé."
            )
        else:
            self.status_line.configure(
                text=f"Scénario « {sdir.name} » : {status.upper()} — {msg}"
            )
        try:
            from tutosvideo.schema import load_scenario
            from tutosvideo.voices import resolve_scenario_voice_id

            settings = Settings.from_env()
            sc = load_scenario(sdir, settings)
            vid = resolve_scenario_voice_id(sc, settings)
            vmeta = sc.voice or {}
            vname = vmeta.get("name") or vid or "(aucune)"
            self.status_line.configure(
                text=self.status_line.cget("text")
                + f"  ·  Voix : {vname}"
                + (f" / {vmeta.get('mood')}" if vmeta.get("mood") else "")
            )
        except Exception:
            pass
        self._refresh_audio_list()

    def _scenario(self):
        from tutosvideo.schema import load_scenario

        return load_scenario(self.app.current_scenario_dir(), Settings.from_env())

    def _refresh_audio_list(self) -> None:
        from tutosvideo.audio_pipeline import list_audio_beats

        try:
            scenario = self._scenario()
        except Exception as exc:  # noqa: BLE001
            self.audio_list.delete(0, "end")
            self.audio_detail.configure(text=str(exc))
            return
        self._beats = list_audio_beats(scenario)
        self.audio_list.delete(0, "end")
        for b in self._beats:
            status = b.path.name if b.path else "MANQUANT"
            preview = b.narrate.replace("\n", " ")[:70]
            self.audio_list.insert(
                "end", f"{b.step_id}  [{status}]  {b.duration_ms}ms  — {preview}"
            )

    def _selected_beat(self):
        sel = self.audio_list.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        if idx < 0 or idx >= len(self._beats):
            return None
        return self._beats[idx]

    def _on_select_audio(self, _evt=None) -> None:
        beat = self._selected_beat()
        if not beat:
            self.audio_detail.configure(text="Aucun audio sélectionné")
            return
        path = rel_display(beat.path) if beat.path else "(aucun fichier)"
        self.audio_detail.configure(
            text=f"{beat.step_id}\n{path}\n{beat.narrate}"
        )

    def _gen_all(self) -> None:
        sdir = self.app.current_scenario_dir()
        ok, msg = is_approved(sdir)
        if not ok:
            messagebox.showwarning("Voix", msg)
            return
        clear_log(self.log)
        append_log(self.log, "Génération TTS…")
        silent = self.silent_var.get()

        def work() -> None:
            from tutosvideo.audio_pipeline import synthesize_all

            settings = Settings.from_env()
            scenario = self._scenario()

            def on_progress(step_id: str, path) -> None:
                self.after(
                    0,
                    lambda: append_log(
                        self.log, f"OK {step_id} → {rel_display(path) if path else '?'}"
                    ),
                )
                self.after(0, self._refresh_audio_list)

            synthesize_all(
                scenario,
                settings,
                allow_silent=silent,
                reuse_existing=False,
                on_progress=on_progress,
                log=lambda m: self.after(0, lambda: append_log(self.log, m)),
            )

        def done(err: Exception | None) -> None:
            self._refresh_audio_list()
            if err:
                append_log(self.log, f"ERREUR : {err}")
                messagebox.showerror("Voix", str(err))
            else:
                append_log(self.log, "Voix prêtes — écoutez / régénérez puis filmez.")
                self.app.set_status("Voix générées")

        run_in_thread(work, lambda e: self.after(0, lambda: done(e)))

    def _play_selected(self) -> None:
        from tutosvideo.audio_pipeline import play_audio

        beat = self._selected_beat()
        if not beat or not beat.path:
            messagebox.showinfo("Voix", "Générez d'abord cet audio.")
            return
        try:
            if self._player and self._player.poll() is None:
                self._player.terminate()
            self._player = play_audio(beat.path)
            append_log(self.log, f"Lecture {rel_display(beat.path)}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Lecture", str(exc))

    def _regen_selected(self) -> None:
        from tutosvideo.audio_pipeline import synthesize_step

        beat = self._selected_beat()
        if not beat:
            return
        silent = self.silent_var.get()
        append_log(self.log, f"Régénération {beat.step_id}…")

        def work() -> None:
            settings = Settings.from_env()
            scenario = self._scenario()
            return synthesize_step(
                scenario, beat.step_id, settings, allow_silent=silent
            )

        def done(err: Exception | None, result=None) -> None:
            self._refresh_audio_list()
            if err:
                append_log(self.log, f"ERREUR : {err}")
                messagebox.showerror("Voix", str(err))
            else:
                append_log(self.log, f"Régénéré {beat.step_id}")
                # resélectionner
                for i, b in enumerate(self._beats):
                    if b.step_id == beat.step_id:
                        self.audio_list.selection_clear(0, "end")
                        self.audio_list.selection_set(i)
                        self.audio_list.see(i)
                        self._on_select_audio()
                        break

        def wrapper() -> None:
            err = None
            result = None
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                err = exc
            self.after(0, lambda: done(err, result))

        import threading

        threading.Thread(target=wrapper, daemon=True).start()

    def _preview(self) -> None:
        sdir = self.app.current_scenario_dir()
        until = self.until_var.get().strip() or "before_submit"
        clear_log(self.log)
        append_log(self.log, f"Preview {sdir.name} until={until}")

        def work() -> None:
            settings = Settings.from_env()
            from tutosvideo.audio_pipeline import load_durations_ms
            from tutosvideo.schema import load_scenario

            scenario = load_scenario(sdir, settings)
            try:
                durations = load_durations_ms(scenario)
            except Exception:
                durations = {}
            Runner(
                scenario,
                settings,
                headless=False,
                debug_overlay=True,
                until=until,
                record_output=None,
                audio_durations_ms=durations,
            ).run()

        def done(err: Exception | None) -> None:
            if err:
                append_log(self.log, f"ERREUR : {err}")
                messagebox.showerror("Preview", str(err))
            else:
                append_log(self.log, "Preview terminée.")
                self.app.set_status("Preview OK")

        run_in_thread(work, lambda e: self.after(0, lambda: done(e)))

    def _render(self, dry_run: bool) -> None:
        from tutosvideo.audio_pipeline import list_audio_beats
        from tutosvideo.cli import run_render

        sdir = self.app.current_scenario_dir()
        ok, msg = is_approved(sdir)
        if not ok:
            messagebox.showwarning("Render", msg)
            return
        scenario = self._scenario()
        missing = [b.step_id for b in list_audio_beats(scenario) if b.path is None]
        if missing:
            messagebox.showwarning(
                "Render",
                "Voix manquantes : "
                + ", ".join(missing[:6])
                + ("…" if len(missing) > 6 else "")
                + "\nCliquez d'abord sur « Générer toutes les voix ».",
            )
            return
        if not dry_run and not messagebox.askyesno(
            "Render",
            "Un render complet peut créer une instance / modifier des données. Continuer ?",
        ):
            return
        self._run_render_pipeline(dry_run=dry_run, reuse_audio=True)

    def _render_chained(self, *, dry_run: bool) -> None:
        """Lancé depuis Script : génère les voix manquantes puis filme."""
        sdir = self.app.current_scenario_dir()
        ok, msg = is_approved(sdir)
        if not ok:
            messagebox.showwarning("Render", msg)
            return
        clear_log(self.log)
        append_log(self.log, "Enchaînement filmage — génération des voix si besoin…")
        self.app.set_status("Enchaînement filmage…")
        silent = dry_run or self.silent_var.get()

        def work() -> Path:
            from tutosvideo.audio_pipeline import list_audio_beats, synthesize_all
            from tutosvideo.cli import run_render

            settings = Settings.from_env()
            scenario = self._scenario()
            missing = [b.step_id for b in list_audio_beats(scenario) if b.path is None]
            if missing or dry_run:
                synthesize_all(
                    scenario,
                    settings,
                    allow_silent=silent,
                    reuse_existing=True,
                    log=lambda m: self.after(0, lambda msg=m: append_log(self.log, msg)),
                )
                self.after(0, self._refresh_audio_list)
            return run_render(
                sdir.name,
                dry_run=dry_run,
                headed=self.headed_var.get(),
                reuse_audio=True,
                until=(self.until_var.get().strip() or None) if dry_run else None,
                log=lambda m: self.after(0, lambda msg=m: self._chained_ui_log(msg)),
            )

        def done(err: Exception | None, result: Path | None = None) -> None:
            if err:
                append_log(self.log, f"ERREUR : {err}")
                self.app.set_status("Render en erreur")
                messagebox.showerror("Render", str(err))
            else:
                out = result or (OUTPUT_DIR / sdir.name / f"{sdir.name}.mp4")
                append_log(self.log, f"OK — {rel_display(out)}")
                self.app.set_status("Render OK")
                self.status_line.configure(text=f"Vidéo prête : {rel_display(out)}")
                try:
                    self.app.root.lift()
                    self.app.root.attributes("-topmost", True)
                    self.app.root.after(
                        200, lambda: self.app.root.attributes("-topmost", False)
                    )
                    self.app.root.focus_force()
                except Exception:
                    pass
                messagebox.showinfo("Render", f"Vidéo prête :\n{rel_display(out)}")

        def wrapper() -> None:
            err = None
            result = None
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                err = exc
            self.after(0, lambda: done(err, result))

        import threading

        threading.Thread(target=wrapper, daemon=True).start()

    def _chained_ui_log(self, msg: str) -> None:
        append_log(self.log, msg)
        self.app.set_status(msg[:90])
        self.status_line.configure(text=msg[:120])

    def _run_render_pipeline(self, *, dry_run: bool, reuse_audio: bool) -> None:
        from tutosvideo.cli import run_render

        sdir = self.app.current_scenario_dir()
        clear_log(self.log)
        mode = "dry-run" if dry_run else "complet"
        append_log(self.log, f"Filmage {mode} démarré…")
        self.app.set_status(f"Filmage {mode}…")
        headed = self.headed_var.get()
        until = self.until_var.get().strip() or None

        def ui_log(msg: str) -> None:
            self.after(0, lambda m=msg: append_log(self.log, m))
            self.after(0, lambda m=msg: self.app.set_status(m[:90]))
            self.after(0, lambda m=msg: self.status_line.configure(text=m[:120]))

        def work() -> Path:
            return run_render(
                sdir.name,
                dry_run=dry_run,
                headed=headed,
                reuse_audio=reuse_audio,
                until=until if dry_run else None,
                log=ui_log,
            )

        def done(err: Exception | None, result: Path | None = None) -> None:
            if err:
                append_log(self.log, f"ERREUR : {err}")
                self.app.set_status("Render en erreur")
                self.status_line.configure(text=f"Erreur : {err}")
                messagebox.showerror("Render", str(err))
            else:
                out = result or (OUTPUT_DIR / sdir.name / f"{sdir.name}.mp4")
                append_log(self.log, f"OK — {rel_display(out)}")
                self.app.set_status("Render OK")
                self.status_line.configure(text=f"Vidéo prête : {rel_display(out)}")
                try:
                    self.app.root.lift()
                    self.app.root.attributes("-topmost", True)
                    self.app.root.after(
                        200, lambda: self.app.root.attributes("-topmost", False)
                    )
                    self.app.root.focus_force()
                except Exception:
                    pass
                messagebox.showinfo("Render", f"Vidéo prête :\n{rel_display(out)}")

        def wrapper() -> None:
            err = None
            result = None
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                err = exc
            self.after(0, lambda: done(err, result))

        import threading

        threading.Thread(target=wrapper, daemon=True).start()


def _help_text() -> str:
    lines = [
        "TutosVideo — aide",
        "=================",
        "",
        "Parcours recommandé",
        "-------------------",
        "1. Setup — créer le venv, installer le package et Chromium Playwright.",
        "2. Configuration — renseigner le .env (clés API, compte d'essai, etc.).",
        "3. Scénario — choisir, créer ou renommer le scénario de travail.",
        "4. Découverte — explorer le site réel et produire un catalogue d'actions.",
        "5. Script — générer / relire le texte, puis valider (approve).",
        "   Options : « Valider automatiquement » et « Enchaîner le filmage ».",
        "6. Filmage — générer les voix, prévisualiser, puis filmer (mux MP4).",
        "",
        "CLI rapide",
        "---------",
        "  tutosvideo author <scénario> --approve",
        "  tutosvideo author <scénario> --film [--dry-run] [--headed]",
        "  tutosvideo pipeline <scénario> [--skip-discover] [--film]",
        "",
        "Vues en double",
        "--------------",
        "Discover ignore les URLs / pages qui mènent à la même vue (hash SPA).",
        "Author fusionne les steps de navigation redondantes (ex. Pédagogique",
        "+ Formations → une seule étape).",
        "",
        "Principe voix-d'abord",
        "--------------------",
        "Chaque paragraphe = un beat. Avant la narration, le runner centre la cible",
        "(prep). La voix démarre seulement quand le contrôle est visible ; l'action",
        "intervient vers le milieu de la phrase. La vidéo attend la fin réelle de",
        "l'audio (+ marge + settle). Au mux, un silence de tête (= prep) aligne",
        "la piste audio sur cette chorégraphie.",
        "",
        "Comment les étapes sont déterminées",
        "-----------------------------------",
        "1. Découverte (discover_prompt.md + URL) → process.json",
        "   Catalogue des contrôles réels du site (sans soumettre).",
        "2. Rédaction (prompt.md + titre court + process.json) → script.md",
        "   + scenario.yaml (narration + actions Playwright par beat).",
        "3. Validation humaine → statut « validé » (sinon « brouillon »).",
        "4. Filmage lit scenario.yaml : pour chaque step, saute au contrôle,",
        "   joue le rythme voix-d'abord, exécute les actions.",
        "",
        "Changer le parcours SANS modifier le code",
        "----------------------------------------",
        "• Écran Découverte : éditer le brief (BDD + discover_prompt.md), relancer discover.",
        "• Écran Script : éditer le prompt détaillé (BDD + prompt.md), régénérer, valider.",
        "• Écran Scénario : éditer les prompts PAR DÉFAUT (BDD) pour les nouveaux scénarios.",
        "• Chaque scénario a ses propres prompts en base (data/tutosvideo.db).",
        "",
        "Statut de validation",
        "--------------------",
        "Badge coloré (sidebar + Accueil / Scénario / Script / Filmage) :",
        "  ● VALIDÉ (vert)  ·  ○ BROUILLON (orange)  ·  — PAS DE SCRIPT (gris).",
        "",
        "Filmage — position à l'écran",
        "----------------------------",
        "Avant chaque action, le runner saute directement le viewport au contrôle",
        "(centrage immédiat), sans défilement progressif.",
        "",
        "Captcha (code de sécurité image)",
        "--------------------------------",
        "Playwright capture l'image (#img_securitycode / antispamimage.php), puis",
        "Mistral vision (MISTRAL_VISION_MODEL) lit le texte et le saisit dans le",
        "champ code. Intégré dans login_dolibarr ; action dédiée : fill_captcha.",
        "Secours OCR : mistral-ocr-latest. Relance auto si le login échoue.",
        "",
        "Configuration (.env)",
        "--------------------",
        "Les secrets restent dans le fichier .env local — jamais envoyés au",
        "navigateur ni injectés dans une page web.",
        "",
        "L'écran Configuration affiche les variables par blocs logiques.",
        "Blocs actuels et variables :",
        "",
    ]
    for _gid, title, fields in iter_env_groups():
        lines.append(f"• {title}")
        for field in fields:
            secret = " (secret)" if field.secret else ""
            default = f"  [défaut : {field.default}]" if field.default else ""
            lines.append(f"    - {field.key} — {field.label}{secret}{default}")
        lines.append("")

    lines += [
        "Ajouter des variables (autres outils)",
        "-------------------------------------",
        "Deux façons — l'UI les reprend au prochain « Recharger » :",
        "",
        "1. Catalogue officiel (recommandé) : ajouter un EnvField dans",
        "   src/tutosvideo/envfile.py → ENV_CATALOG, avec group =",
        "   ia | compte | dolibarr | imap | autre (ou un nouveau groupe",
        "   déclaré dans ENV_GROUPS).",
        "",
        "2. Découverte auto : écrire la clé dans .env ou .env.example.",
        "   Le groupe et le caractère secret sont déduits du nom :",
        "     MISTRAL_* / *_API_KEY / *VOICE_ID*  → IA",
        "     *DOLIBARR*                          → Dolibarr",
        "     TUTO_*                              → Compte d'essai",
        "     IMAP_* / VERIFY_*                   → E-mail (IMAP)",
        "     sinon                              → Autres",
        "   Mot de passe / API_KEY / SECRET / TOKEN → champ masqué.",
        "",
        "Les secrets laissés vides à l'enregistrement conservent la valeur",
        "déjà stockée dans le .env.",
        "",
        "Filmage",
        "-------",
        "• Générer toutes les voix, puis écouter / régénérer un beat si besoin.",
        "• Preview s'arrête avant la soumission (until=before_submit) par défaut.",
        "• Un render complet peut créer une vraie instance (compte d'essai).",
        "",
        f"Projet : {ROOT}",
    ]
    return "\n".join(lines)


class HelpScreen(BaseScreen):
    title = "Aide"

    def build(self) -> None:
        ttk.Label(self, text="Aide", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text="Parcours, configuration et extension à d'autres outils.",
        ).pack(anchor="w", pady=(0, 8))
        frame, self.body = make_scroll_text(self, height=28)
        frame.pack(fill="both", expand=True)
        self.body.configure(state="normal", font=("TkFixedFont", 10))
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Actualiser", command=self.on_show).pack(side="left")

    def on_show(self) -> None:
        text = _help_text()
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", text)
        self.body.configure(state="disabled")
