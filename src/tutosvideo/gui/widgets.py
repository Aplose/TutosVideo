"""Widgets communs pour l'interface graphique."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


def make_scroll_text(parent: tk.Misc, height: int = 12) -> tuple[ttk.Frame, tk.Text]:
    frame = ttk.Frame(parent)
    text = tk.Text(frame, height=height, wrap="word", font=("TkDefaultFont", 10))
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    return frame, text


def append_log(widget: tk.Text, message: str) -> None:
    widget.configure(state="normal")
    widget.insert("end", message.rstrip() + "\n")
    widget.see("end")
    widget.configure(state="disabled")


def clear_log(widget: tk.Text) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.configure(state="disabled")


def make_scrollable(parent: tk.Misc) -> tuple[ttk.Frame, Callable[[], None]]:
    """Cadre scrollable vertical. Retourne (inner_frame, update_scrollregion)."""
    outer = ttk.Frame(parent)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = ttk.Frame(canvas)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_configure(_event=None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    def _on_mousewheel(event) -> None:
        # Linux: Button-4/5 ; Windows/mac: MouseWheel
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            canvas.yview_scroll(-1, "units")
        else:
            canvas.yview_scroll(1, "units")

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _bind_wheel(_event=None) -> None:
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

    def _unbind_wheel(_event=None) -> None:
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    canvas.bind("<Enter>", _bind_wheel)
    canvas.bind("<Leave>", _unbind_wheel)

    def refresh() -> None:
        _on_inner_configure()

    return inner, refresh


def status_badge(parent: tk.Misc, *, width: int = 18) -> tk.Label:
    """Badge de statut script (validé / brouillon / absent) — très visible."""
    lbl = tk.Label(
        parent,
        text="STATUT : —",
        width=width,
        font=("TkDefaultFont", 11, "bold"),
        relief="ridge",
        padx=10,
        pady=4,
        bg="#e8e8e8",
        fg="#333333",
    )
    return lbl


def set_status_badge(lbl: tk.Label, status: str) -> None:
    status = (status or "absent").lower()
    if status in ("validé", "valide", "approved", "ok"):
        lbl.configure(text="● VALIDÉ", bg="#1b7f3a", fg="#ffffff")
    elif status in ("brouillon", "draft"):
        lbl.configure(text="○ BROUILLON", bg="#c47a00", fg="#ffffff")
    else:
        lbl.configure(text="— PAS DE SCRIPT", bg="#6c757d", fg="#ffffff")


def labeled_entry(
    parent: tk.Misc,
    label: str,
    variable: tk.Variable,
    *,
    show: str | None = None,
    width: int = 48,
    secret: bool = False,
) -> ttk.Entry:
    """Champ libellé. Si secret=True, bouton œil pour afficher / masquer."""
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk.Label(row, text=label, width=22, anchor="w").pack(side="left")
    mask = "•" if secret else (show or "")
    entry = ttk.Entry(row, textvariable=variable, width=width, show=mask)
    entry.pack(side="left", fill="x", expand=True)
    if secret:
        # 👁 = masqué ; 👁̸ = visible (œil barré, U+0338)
        icon_masked = "👁"
        icon_visible = "👁\u0338"
        state = {"visible": False}

        def toggle() -> None:
            state["visible"] = not state["visible"]
            if state["visible"]:
                entry.configure(show="")
                btn.configure(text=icon_visible)
            else:
                entry.configure(show="•")
                btn.configure(text=icon_masked)

        btn = ttk.Button(row, text=icon_masked, width=3, command=toggle)
        btn.pack(side="left", padx=(4, 0))
    return entry


def run_in_thread(target: Callable[[], None], on_done: Callable[[Exception | None], None]) -> None:
    import threading

    def wrapper() -> None:
        err: Exception | None = None
        try:
            target()
        except Exception as exc:  # noqa: BLE001
            err = exc
        on_done(err)

    threading.Thread(target=wrapper, daemon=True).start()
