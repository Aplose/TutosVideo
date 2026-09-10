"""Application graphique TutosVideo (tkinter) — menus / écrans par fonction."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from tutosvideo import __version__
from tutosvideo.assistant import list_scenarios
from tutosvideo.config import SCENARIOS_DIR, scenario_dir
from tutosvideo.db import get_scenario_status, init_db
from tutosvideo.gui.screens import (
    ConfigScreen,
    DiscoverScreen,
    FilmScreen,
    HelpScreen,
    HomeScreen,
    ScenarioScreen,
    ScriptScreen,
    SetupScreen,
)
from tutosvideo.gui.widgets import set_status_badge, status_badge


class TutosVideoApp:
    NAV = [
        ("Accueil", HomeScreen),
        ("Setup", SetupScreen),
        ("Configuration", ConfigScreen),
        ("Scénario", ScenarioScreen),
        ("Découverte", DiscoverScreen),
        ("Script", ScriptScreen),
        ("Filmage", FilmScreen),
        ("Aide", HelpScreen),
    ]

    def __init__(self) -> None:
        init_db()
        self.root = tk.Tk()
        self.root.title(f"TutosVideo {__version__}")
        self.root.minsize(900, 600)
        self.root.geometry("980x720")

        SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
        names = list_scenarios()
        self.scenario_var = tk.StringVar(value=names[0] if names else "mgc-essentiel")

        self._status = tk.StringVar(value="Prêt")
        self._build_layout()
        self.show_screen("Accueil")

    def _build_layout(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, padding=8)
        sidebar.grid(row=0, column=0, sticky="nsw")
        ttk.Label(sidebar, text="TutosVideo", font=("TkDefaultFont", 12, "bold")).pack(
            anchor="w", pady=(0, 8)
        )
        self._nav_buttons: dict[str, ttk.Button] = {}
        for label, _cls in self.NAV:
            btn = ttk.Button(sidebar, text=label, command=lambda l=label: self.show_screen(l))
            btn.pack(fill="x", pady=2)
            self._nav_buttons[label] = btn

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(sidebar, text="Scénario").pack(anchor="w")
        self._side_combo = ttk.Combobox(
            sidebar, textvariable=self.scenario_var, values=list_scenarios(), width=22
        )
        self._side_combo.pack(fill="x", pady=4)
        self._side_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_scenario_change())
        self.side_badge = status_badge(sidebar, width=16)
        self.side_badge.pack(fill="x", pady=6)
        self._refresh_side_badge()

        self.content = ttk.Frame(self.root, padding=4)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        status = ttk.Label(self.root, textvariable=self._status, relief="sunken", anchor="w")
        status.grid(row=1, column=0, columnspan=2, sticky="ew")

        self._screens: dict[str, ttk.Frame] = {}
        for label, cls in self.NAV:
            screen = cls(self.content, self)  # type: ignore[arg-type]
            screen.grid(row=0, column=0, sticky="nsew")
            self._screens[label] = screen
            screen.grid_remove()

    def _on_scenario_change(self) -> None:
        self._refresh_side_badge()
        # Rafraîchir l'écran visible
        for label, screen in self._screens.items():
            if screen.winfo_ismapped() and hasattr(screen, "on_show"):
                screen.on_show()
                break

    def _refresh_side_badge(self) -> None:
        name = self.scenario_var.get().strip() or "mgc-essentiel"
        set_status_badge(self.side_badge, get_scenario_status(name))

    def current_scenario_dir(self) -> Path:
        name = self.scenario_var.get().strip() or "mgc-essentiel"
        return scenario_dir(name, create=True)

    def set_status(self, text: str) -> None:
        self._status.set(text)
        self._refresh_side_badge()

    def refresh_scenario_lists(self) -> None:
        names = list_scenarios()
        self._side_combo["values"] = names
        self._refresh_side_badge()
        for screen in self._screens.values():
            if isinstance(screen, ScenarioScreen):
                screen.on_show()

    def show_screen(self, name: str) -> None:
        self._refresh_side_badge()
        for label, screen in self._screens.items():
            if label == name:
                screen.grid()
                if hasattr(screen, "on_show"):
                    screen.on_show()
            else:
                screen.grid_remove()
        self.set_status(name)

    def run(self) -> None:
        self.root.mainloop()


def run_gui() -> None:
    app = TutosVideoApp()
    app.run()
