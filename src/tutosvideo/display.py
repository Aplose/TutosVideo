"""Détection moniteur pour le filmage headed (écran externe vs intégré)."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    name: str
    width: int
    height: int
    x: int
    y: int
    primary: bool

    @property
    def is_external(self) -> bool:
        upper = self.name.upper()
        if upper.startswith("EDP") or upper.startswith("LVDS") or "EDP-" in upper:
            return False
        return True


def _parse_xrandr(text: str) -> list[Monitor]:
    monitors: list[Monitor] = []
    # HDMI-1 connected 1680x1050+1600+0 ... primary may appear
    pat = re.compile(
        r"^(?P<name>\S+)\s+connected(?:\s+primary)?\s+"
        r"(?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+)",
        re.MULTILINE,
    )
    primary_pat = re.compile(r"^(\S+)\s+connected\s+primary\b", re.MULTILINE)
    primaries = {m.group(1) for m in primary_pat.finditer(text)}
    for m in pat.finditer(text):
        name = m.group("name")
        monitors.append(
            Monitor(
                name=name,
                width=int(m.group("w")),
                height=int(m.group("h")),
                x=int(m.group("x")),
                y=int(m.group("y")),
                primary=name in primaries,
            )
        )
    return monitors


def list_monitors() -> list[Monitor]:
    try:
        out = subprocess.check_output(
            ["xrandr", "--query"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_xrandr(out)


def pick_film_monitor(monitors: list[Monitor] | None = None) -> Monitor | None:
    """Choisit l'écran de filmage.

    ``TUTO_FILM_MONITOR`` :
    - ``external`` / ``hdmi`` / ``rtk`` (défaut) → premier non-eDP (HDMI…)
    - ``primary`` → écran primaire
    - nom exact (ex. ``HDMI-1``)
    - ``auto`` → external si présent, sinon primary
    """
    mons = monitors if monitors is not None else list_monitors()
    if not mons:
        return None

    pref = (os.getenv("TUTO_FILM_MONITOR") or "external").strip().lower()
    if pref in ("external", "hdmi", "rtk", "auto", ""):
        externals = [m for m in mons if m.is_external]
        if externals:
            # Préférer HDMI / DisplayPort
            for key in ("HDMI", "DP-", "DISPLAYPORT", "DVI"):
                for m in externals:
                    if key in m.name.upper():
                        return m
            return externals[0]
        if pref == "auto":
            for m in mons:
                if m.primary:
                    return m
            return mons[0]
        return None

    if pref == "primary":
        for m in mons:
            if m.primary:
                return m
        return mons[0]

    for m in mons:
        if m.name.lower() == pref or pref in m.name.lower():
            return m
    return None


def headed_launch_args(
    viewport_w: int,
    viewport_h: int,
    *,
    monitor: Monitor | None = None,
) -> tuple[list[str], dict[str, int] | None]:
    """Args Chromium + viewport pour remplir le moniteur de filmage (maximisé).

    Retourne ``(args, viewport_override_or_None)``.
    """
    mon = monitor if monitor is not None else pick_film_monitor()
    args: list[str] = []
    vp_override: dict[str, int] | None = None

    # Position manuelle : TUTO_WINDOW_POSITION=1600,0
    pos = (os.getenv("TUTO_WINDOW_POSITION") or "").strip()
    if pos and re.fullmatch(r"-?\d+,-?\d+", pos):
        args.append(f"--window-position={pos}")
    elif mon is not None:
        args.append(f"--window-position={mon.x},{mon.y}")

    # Sous Wayland, --window-position est ignoré en ozone-wayland → forcer X11
    if os.getenv("WAYLAND_DISPLAY") and not os.getenv("TUTO_ALLOW_WAYLAND_OZONE"):
        args.append("--ozone-platform=x11")

    # Fenêtre = taille moniteur (puis CDP maximize). Viewport = zone utile.
    chrome_h = 88  # barre d'onglets / chrome UI
    if mon is not None:
        args.append(f"--window-size={mon.width},{mon.height}")
        usable_w = mon.width
        usable_h = max(600, mon.height - chrome_h)
        vp_override = {
            "width": usable_w,
            "height": usable_h,
        }
    else:
        args.append(f"--window-size={viewport_w},{viewport_h + chrome_h}")

    # Aide le WM à maximiser sur le moniteur où la fenêtre a été positionnée
    args.append("--start-maximized")
    return args, vp_override


def maximize_page_on_monitor(page: object, monitor: Monitor | None) -> None:
    """Maximise la fenêtre Playwright sur le moniteur cible (CDP)."""
    if monitor is None:
        return
    try:
        session = page.context.new_cdp_session(page)
        info = session.send("Browser.getWindowForTarget")
        window_id = info.get("windowId")
        if window_id is None:
            return
        # Placer sur le moniteur puis maximiser (maximize = écran courant de la fenêtre)
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": monitor.x,
                    "top": monitor.y,
                    "width": monitor.width,
                    "height": monitor.height,
                    "windowState": "normal",
                },
            },
        )
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {"windowState": "maximized"},
            },
        )
    except Exception:
        # Fallback JS (souvent bloqué hors user-gesture, mais tente)
        try:
            page.evaluate(
                """([x, y, w, h]) => {
                  try { window.moveTo(x, y); } catch (e) {}
                  try { window.resizeTo(w, h); } catch (e) {}
                }""",
                [monitor.x, monitor.y, monitor.width, monitor.height],
            )
        except Exception:
            pass

