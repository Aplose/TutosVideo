"""Exécution Playwright chorégraphiée (pointer / highlight / actions)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Locator, Page, sync_playwright

from tutosvideo.capture import PlaywrightCaptureSession, parse_size
from tutosvideo.config import Settings
from tutosvideo.discover import dismiss_overlays
from tutosvideo.display import headed_launch_args, maximize_page_on_monitor, pick_film_monitor
from tutosvideo.mailbox import wait_for_verify_code
from tutosvideo.pacing import BeatTimeline, estimate_actions_ms, plan_beat
from tutosvideo.provision import login_dolibarr, probe_instance_url, wait_for_instance
from tutosvideo.schema import Scenario, Step

HIGHLIGHT_CSS = """
#tutosvideo-spotlight {
  position: fixed; pointer-events: none; z-index: 2147483646;
  display: none;
  box-sizing: border-box;
  border: 3px solid #ff6a00; border-radius: 8px;
  box-shadow: 0 0 0 9999px rgba(0,0,0,0.35);
  /* Pas de transition : évite un second cadre fantôme pendant le repositionnement */
  transition: none;
  outline: none;
}
#tutosvideo-pointer {
  position: fixed; width: 18px; height: 18px; border-radius: 50%;
  background: #ff6a00; border: 2px solid #fff; z-index: 2147483647;
  pointer-events: none; transform: translate(-50%, -50%);
  /* Pas de transition : le curseur ne doit pas glisser pendant un scroll */
  transition: none;
}
#tutosvideo-debug {
  position: fixed; bottom: 12px; right: 12px; z-index: 2147483647;
  background: rgba(0,0,0,0.75); color: #0f0; font: 12px monospace;
  padding: 8px 10px; border-radius: 6px; max-width: 360px;
  white-space: pre-wrap;
}
"""


def _is_nav_context_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "execution context was destroyed",
            "most likely because of a navigation",
            "target closed",
            "frame was detached",
            "navigating",
        )
    )


def wait_page_ready(page: Page, *, timeout_ms: int = 15_000) -> None:
    """Attend que la page soit utilisable après une navigation éventuelle."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        page.wait_for_function(
            "() => document.readyState === 'interactive' || document.readyState === 'complete'",
            timeout=min(5000, timeout_ms),
        )
    except Exception:
        pass


def safe_page_evaluate(page: Page, expression: str, arg: Any = None, *, retries: int = 3) -> Any:
    """page.evaluate résilient aux navigations concurrentes."""
    last: BaseException | None = None
    for attempt in range(retries):
        try:
            wait_page_ready(page, timeout_ms=8_000 if attempt else 2_000)
            if arg is None:
                return page.evaluate(expression)
            return page.evaluate(expression, arg)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_nav_context_error(exc) or attempt >= retries - 1:
                raise
            page.wait_for_timeout(200 + attempt * 150)
    if last:
        raise last
    return None


def inject_chrome(page: Page) -> None:
    try:
        wait_page_ready(page)
        safe_page_evaluate(
            page,
            """(css) => {
      if (!document.body) return;
      let style = document.getElementById('tutosvideo-chrome-css');
      if (!style) {
        style = document.createElement('style');
        style.id = 'tutosvideo-chrome-css';
        document.documentElement.appendChild(style);
      }
      style.textContent = css;
      // Un seul overlay : purger les doublons éventuels (SPA / ré-inject)
      const keepOne = (id) => {
        const all = Array.from(document.querySelectorAll('#' + id));
        all.slice(1).forEach(n => n.remove());
        if (!all.length) {
          const el = document.createElement('div');
          el.id = id;
          document.body.appendChild(el);
        }
      };
      keepOne('tutosvideo-spotlight');
      keepOne('tutosvideo-pointer');
    }""",
            HIGHLIGHT_CSS,
        )
    except Exception as exc:  # noqa: BLE001
        # Overlay optionnel : ne pas faire échouer le filmage mid-navigation
        if not _is_nav_context_error(exc):
            print(f"[runner] inject_chrome: {exc}")



def set_debug(page: Page, text: str, enabled: bool) -> None:
    if not enabled:
        return
    try:
        safe_page_evaluate(
            page,
            """({ text }) => {
      if (!document.body) return;
      let el = document.getElementById('tutosvideo-debug');
      if (!el) {
        el = document.createElement('div');
        el.id = 'tutosvideo-debug';
        document.body.appendChild(el);
      }
      el.textContent = text;
    }""",
            {"text": text},
        )
    except Exception:
        pass


def _element_target_score(locator: Locator, *, prefer: str | None = None) -> float:
    """Score pour préférer le contenu utile (titres / tarifs) au nav sticky.

    ``prefer="testimonial"`` inverse la pénalité témoignages (lecture d'un avis).
    ``prefer="nav"|"menu"|"sidebar"`` privilégie le menu latéral (mat-tree, aside).
    """
    prefer_key = (prefer or "").lower()
    prefer_tm = prefer_key in ("testimonial", "temoignage", "témoignage")
    prefer_nav = prefer_key in ("nav", "menu", "sidebar", "aside")
    try:
        return float(
            locator.evaluate(
                """(el, opts) => {
                  const preferTm = !!opts.preferTm;
                  const preferNav = !!opts.preferNav;
                  const text = (el.innerText || el.textContent || '').trim();
                  let score = 0;
                  const tag = el.tagName || '';
                  const inSideNav = !!el.closest(
                    'mat-tree, mat-sidenav, mat-drawer, aside, [class*="sidebar" i], '
                    + '[class*="side-nav" i], .menu-expandable-row, .cdk-tree'
                  );
                  if (preferNav) {
                    if (inSideNav) score += 200;
                    if (tag === 'BUTTON' || tag === 'A') score += 30;
                    if (/^H[1-6]$/.test(tag)) score -= 80;
                    if (el.closest('main, [role="main"], .page-title, h1, h2')) score -= 100;
                    const r = el.getBoundingClientRect();
                    if (r.x < 420) score += 40;
                    if (r.width < 8 || r.height < 8) score -= 50;
                    return score;
                  }
                  if (/^H[1-6]$/.test(tag)) score += 60;
                  if (tag === 'BUTTON' || tag === 'A') score += 5;
                  if (el.closest('header, nav, [role="banner"], footer, [role="contentinfo"]')) {
                    score -= 120;
                  }
                  if (inSideNav) score -= 80;
                  const inTm = !!el.closest(
                    '[class*="testimonial" i], [class*="temoignage" i], [id*="testimonial" i], '
                    + '[id*="temoignage" i], [class*="review" i], blockquote, .swiper, .carousel'
                  );
                  if (preferTm) {
                    if (inTm) score += 120;
                    // Préférer le bloc/carte (plus de texte) au seul nom
                    if (text.length >= 80) score += 50;
                    if (text.length >= 160) score += 30;
                  } else if (inTm) {
                    score -= 100;
                  }
                  if (el.closest(
                    'main, [role="main"], #content, [class*="pricing" i], [class*="tarif" i], '
                    + '[id*="tarif" i], [class*="plan" i], [class*="offer" i]'
                  )) {
                    score += preferTm ? 10 : 45;
                  }
                  if (!preferTm && text.length > 0 && text.length <= 48) score += 25;
                  if (!preferTm && text.length > 140) score -= 40;
                  const r = el.getBoundingClientRect();
                  if (r.height >= 20 && r.height <= 220) score += 10;
                  if (preferTm && r.height >= 80) score += 25;
                  if (r.width < 8 || r.height < 8) score -= 50;
                  return score;
                }""",
                {"preferTm": prefer_tm, "preferNav": prefer_nav},
            )
        )
    except Exception:
        return -50.0


def _prefer_visible(loc: Locator, *, prefer: str | None = None) -> Locator:
    """Choisit le meilleur match visible (pas le premier : souvent le menu sticky)."""
    try:
        n = min(loc.count(), 24)
        best_i = None
        best_score = float("-inf")
        for i in range(n):
            nth = loc.nth(i)
            try:
                if not nth.is_visible():
                    continue
            except Exception:
                continue
            score = _element_target_score(nth, prefer=prefer)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is not None:
            return loc.nth(best_i)
    except Exception:
        pass
    return loc.first


def resolve_locator(page: Page, hint: dict[str, Any] | str) -> Locator:
    prefer: str | None = None
    if isinstance(hint, dict):
        prefer = hint.get("prefer") or hint.get("context")
    loc: Locator
    if isinstance(hint, str):
        if hint.startswith("text="):
            needle = hint[5:]
            exact = page.get_by_text(needle, exact=True)
            if exact.count():
                loc = _prefer_visible(exact, prefer=prefer)
            else:
                loc = _prefer_visible(page.get_by_text(needle, exact=False), prefer=prefer)
        else:
            loc = _prefer_visible(page.locator(hint), prefer=prefer)
        return loc
    css = hint.get("css") or hint.get("selector")
    if css:
        raw = page.locator(str(css))
        if "nth" in hint:
            loc = raw.nth(int(hint["nth"]))
        else:
            loc = _prefer_visible(raw, prefer=prefer)
        return _maybe_expand_to_card(loc, hint)
    if "role" in hint and "name" in hint:
        loc = _prefer_visible(
            page.get_by_role(hint["role"], name=re_compile(hint["name"])), prefer=prefer
        )
        return _maybe_expand_to_card(loc, hint)
    if "text" in hint:
        needle = str(hint["text"])
        exact = page.get_by_text(needle, exact=True)
        if exact.count():
            loc = _prefer_visible(exact, prefer=prefer)
        else:
            loc = _prefer_visible(page.get_by_text(needle, exact=False), prefer=prefer)
        return _maybe_expand_to_card(loc, hint)
    if "label" in hint:
        loc = _prefer_visible(page.get_by_label(hint["label"], exact=False), prefer=prefer)
        return _maybe_expand_to_card(loc, hint)
    if "target" in hint:
        return resolve_locator(page, hint["target"])
    raise ValueError(f"Sélecteur non reconnu : {hint}")


def _maybe_expand_to_card(locator: Locator, hint: dict[str, Any] | str) -> Locator:
    """Si ``expand: card`` / prefer témoignage : remonte au bloc lisible (carte avis)."""
    prefer = None
    expand = None
    if isinstance(hint, dict):
        prefer = str(hint.get("prefer") or hint.get("context") or "").lower()
        expand = str(hint.get("expand") or "").lower()
    want = expand in ("card", "block", "parent") or prefer in (
        "testimonial",
        "temoignage",
        "témoignage",
    )
    if not want:
        return locator
    try:
        locator.evaluate(
            """el => {
              document.querySelectorAll('[data-tutosvideo-card-tmp]').forEach(n => {
                n.removeAttribute('data-tutosvideo-card-tmp');
              });
              let best = el.closest(
                '.card, .card-block, [class*="testimonial" i], [class*="temoignage" i], blockquote'
              );
              if (!best) {
                let n = el;
                best = el;
                for (let i = 0; i < 10 && n && n !== document.body; i++) {
                  const t = (n.innerText || '').trim();
                  const r = n.getBoundingClientRect();
                  if (
                    t.length >= 100 && t.length <= 900
                    && r.height >= 90 && r.height <= 700
                    && r.width >= 200 && r.width <= 600
                  ) {
                    best = n;
                    break;
                  }
                  n = n.parentElement;
                }
              }
              best.setAttribute('data-tutosvideo-card-tmp', '1');
            }"""
        )
        page = locator.page
        card = page.locator('[data-tutosvideo-card-tmp="1"]').first
        if card.count():
            return card
    except Exception:
        pass
    return locator


def re_compile(name: str):
    import re

    return re.compile(re.escape(name), re.I)


def element_viewport_offset(page: Page, locator: Locator) -> dict[str, float] | None:
    """Position de l'élément vs viewport (négatif = au-dessus / à gauche)."""
    try:
        return locator.evaluate(
            """el => {
              const r = el.getBoundingClientRect();
              const vh = window.innerHeight || document.documentElement.clientHeight;
              const vw = window.innerWidth || document.documentElement.clientWidth;
              return {
                top: r.top,
                bottom: r.bottom,
                left: r.left,
                right: r.right,
                height: r.height,
                width: r.width,
                vh,
                vw,
              };
            }"""
        )
    except Exception:
        return None


def is_comfortably_visible(box: dict[str, float], *, margin: float = 80) -> bool:
    """Vrai si le centre de l'élément est dans une bande confortable du viewport."""
    mid_y = box["top"] + box["height"] / 2
    mid_x = box["left"] + box["width"] / 2
    return (
        margin < mid_y < box["vh"] - margin
        and 0 < mid_x < box["vw"]
        and box["bottom"] > margin * 0.5
        and box["top"] < box["vh"] - margin * 0.5
    )


def force_scroll_center(locator: Locator) -> None:
    """Place immédiatement l'élément au centre du viewport."""
    try:
        locator.evaluate(
            """el => {
              el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
            }"""
        )
    except Exception:
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass


def ensure_centered(
    page: Page,
    locator: Locator,
    *,
    margin: float = 70,
    settle_ms: int = 320,
    retries: int = 5,
) -> bool:
    """Centre la cible et attend la stabilisation — avant tout mouvement de curseur."""
    try:
        locator.wait_for(state="attached", timeout=8000)
    except Exception:
        return False
    for _ in range(retries):
        force_scroll_center(locator)
        page.wait_for_timeout(settle_ms)
        box = element_viewport_offset(page, locator)
        if box and is_comfortably_visible(box, margin=margin):
            # Court délai pour sticky headers / reflow avant lecture des coords
            page.wait_for_timeout(140)
            box2 = element_viewport_offset(page, locator)
            if box2 and is_comfortably_visible(box2, margin=margin):
                return True
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(settle_ms)
    # Dernier essai : on accepte même si marge imparfaite
    force_scroll_center(locator)
    page.wait_for_timeout(settle_ms)
    box = element_viewport_offset(page, locator)
    return bool(box and box["height"] > 0)


def scroll_into_view_gradual(
    page: Page,
    locator: Locator,
    *,
    step_px: int = 42,
    pause_ms: int = 45,
    margin: float = 120,
    max_steps: int = 200,
) -> None:
    """Amène la cible à l'écran (centrage stable), sans bouger le curseur."""
    del step_px, pause_ms, max_steps  # API stable
    ensure_centered(page, locator, margin=min(60, margin * 0.5), settle_ms=300)


def _viewport_point(page: Page, locator: Locator) -> tuple[float, float] | None:
    """Centre de l'élément en coords viewport, seulement s'il est à l'écran."""
    box = locator.bounding_box()
    if not box or box["width"] <= 0 or box["height"] <= 0:
        return None
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    vp = page.viewport_size or {}
    vh = float(vp.get("height") or 0)
    vw = float(vp.get("width") or 0)
    if vh and (y < 8 or y > vh - 8):
        return None
    if vw and (x < 8 or x > vw - 8):
        return None
    return x, y


def move_pointer_to(
    page: Page,
    locator: Locator,
    *,
    pointer_ms: int = 0,
    center_first: bool = True,
) -> bool:
    """Centre (si besoin) puis place le curseur — jamais l'inverse."""
    if center_first:
        if not ensure_centered(page, locator):
            return False
    point = _viewport_point(page, locator)
    if point is None:
        # Coords hors écran : recentrer une fois puis retenter
        ensure_centered(page, locator, settle_ms=400)
        point = _viewport_point(page, locator)
        if point is None:
            return False
    x, y = point
    try:
        safe_page_evaluate(
            page,
            """({x, y}) => {
      const p = document.getElementById('tutosvideo-pointer');
      if (p) {
        p.style.transition = 'none';
        p.style.left = x + 'px';
        p.style.top = y + 'px';
        p.style.display = 'block';
      }
    }""",
            {"x": x, "y": y},
        )
    except Exception:
        pass
    page.mouse.move(x, y)
    if pointer_ms > 0:
        page.wait_for_timeout(pointer_ms)
    return True


def pointer_to(page: Page, locator: Locator, pointer_ms: int) -> None:
    move_pointer_to(page, locator, pointer_ms=pointer_ms, center_first=True)


def prepare_for_action(
    page: Page,
    locator: Locator,
    *,
    pointer_ms: int = 280,
    highlight_target: bool = True,
) -> bool:
    """Séquence filmage : centrer → pause → curseur → highlight → pause lecture."""
    if not ensure_centered(page, locator):
        return False
    # Laisser voir le champ / bouton centré avant le curseur
    page.wait_for_timeout(420)
    if not move_pointer_to(page, locator, pointer_ms=pointer_ms, center_first=False):
        return False
    if highlight_target:
        highlight(page, locator)
        # Le bouton doit être clairement visible avant le clic
        page.wait_for_timeout(450)
    return True


def click_at_locator(
    page: Page,
    locator: Locator,
    *,
    timeout_ms: int = 8000,
    highlight_target: bool = False,
) -> None:
    """Clique uniquement si la cible est centrée dans le viewport (curseur visible).

    Par défaut ne re-pose pas le cadre orange : le PREP du beat l'a déjà affiché.
    """
    if not ensure_centered(page, locator, margin=80, settle_ms=350, retries=6):
        raise RuntimeError("Impossible de centrer la cible avant clic")
    move_pointer_to(page, locator, pointer_ms=160, center_first=False)
    if highlight_target:
        highlight(page, locator)
        page.wait_for_timeout(280)
    point = _viewport_point(page, locator)
    if point is None:
        # Dernier recours : recentrer puis coords bounding box
        ensure_centered(page, locator, settle_ms=400)
        box = locator.bounding_box()
        if not box:
            locator.click(timeout=timeout_ms)
            return
        point = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    x, y = point
    page.mouse.move(x, y)
    page.wait_for_timeout(280)
    page.mouse.click(x, y)
    page.wait_for_timeout(160)


def highlight(page: Page, locator: Locator) -> None:
    """Attache le spotlight à l'élément et le suit pendant les scrolls."""
    box = locator.bounding_box()
    if not box:
        return
    vp = page.viewport_size or {}
    vh = float(vp.get("height") or 1e9)
    if box["y"] + box["height"] < 0 or box["y"] > vh:
        return
    try:
        inject_chrome(page)
        # Marqueur sur l'élément cible + boucle de suivi (évite le cadre fantôme après scroll)
        locator.evaluate(
            """el => {
              document.querySelectorAll('[data-tutosvideo-hl]').forEach(n => {
                n.removeAttribute('data-tutosvideo-hl');
              });
              el.setAttribute('data-tutosvideo-hl', '1');
            }"""
        )
        safe_page_evaluate(
            page,
            """() => {
      // Purger doublons éventuels
      const all = Array.from(document.querySelectorAll('#tutosvideo-spotlight'));
      all.slice(1).forEach(n => n.remove());
      const s = document.getElementById('tutosvideo-spotlight');
      if (!s) return;

      window.__tutosvideoHlSync = () => {
        const spot = document.getElementById('tutosvideo-spotlight');
        if (!spot) return;
        const el = document.querySelector('[data-tutosvideo-hl="1"]');
        if (!el) { spot.style.display = 'none'; return; }
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) { spot.style.display = 'none'; return; }
        if (r.bottom < 0 || r.top > (window.innerHeight || 0)) {
          spot.style.display = 'none';
          return;
        }
        spot.style.left = (r.x - 4) + 'px';
        spot.style.top = (r.y - 4) + 'px';
        spot.style.width = (r.width + 8) + 'px';
        spot.style.height = (r.height + 8) + 'px';
        spot.style.display = 'block';
      };
      window.__tutosvideoHlSync();
      if (window.__tutosvideoHlTimer) clearInterval(window.__tutosvideoHlTimer);
      window.__tutosvideoHlTimer = setInterval(() => {
        if (typeof window.__tutosvideoHlSync === 'function') window.__tutosvideoHlSync();
      }, 50);
      if (!window.__tutosvideoHlScrollBound) {
        window.__tutosvideoHlScrollBound = true;
        const onScrollOrResize = () => {
          if (typeof window.__tutosvideoHlSync === 'function') window.__tutosvideoHlSync();
        };
        window.addEventListener('scroll', onScrollOrResize, true);
        window.addEventListener('resize', onScrollOrResize);
      }
    }""",
        )
    except Exception:
        pass


def clear_highlight(page: Page) -> None:
    try:
        safe_page_evaluate(
            page,
            """() => {
      if (window.__tutosvideoHlTimer) {
        clearInterval(window.__tutosvideoHlTimer);
        window.__tutosvideoHlTimer = null;
      }
      document.querySelectorAll('[data-tutosvideo-hl]').forEach(n => {
        n.removeAttribute('data-tutosvideo-hl');
      });
      document.querySelectorAll('#tutosvideo-spotlight').forEach(s => {
        s.style.display = 'none';
      });
      const p = document.getElementById('tutosvideo-pointer');
      if (p) p.style.display = 'none';
    }""",
        )
    except Exception:
        pass


def hide_film_overlays(page: Page) -> None:
    """Masque spotlight + pointeur (indispensable avant screenshot captcha / OCR)."""
    clear_highlight(page)
    try:
        safe_page_evaluate(
            page,
            """() => {
      const p = document.getElementById('tutosvideo-pointer');
      if (p) {
        p.style.display = 'none';
        p.style.left = '-100px';
        p.style.top = '-100px';
      }
      document.querySelectorAll('#tutosvideo-spotlight').forEach(s => {
        s.style.display = 'none';
      });
    }""",
        )
    except Exception:
        pass
    try:
        # Écarter le curseur souris Playwright hors zone utile
        page.mouse.move(0, 0)
    except Exception:
        pass



def force_same_tab(locator: Locator) -> None:
    """Empêche target=_blank / window.open sur le lien cliqué."""
    try:
        locator.evaluate(
            """el => {
              let node = el;
              for (let i = 0; i < 6 && node; i++) {
                if (node.tagName === 'A') {
                  node.removeAttribute('target');
                  node.setAttribute('target', '_self');
                  node.removeAttribute('rel');
                }
                node = node.parentElement;
              }
            }"""
        )
    except Exception:
        pass


def close_extra_pages(page: Page) -> Page:
    """Ne garde qu'un seul onglet (celui fourni) — corrige les multi-onglets d'inscription."""
    context = page.context
    keep = page
    for extra in list(context.pages):
        if extra is keep:
            continue
        try:
            # Si l'onglet principal est resté sur l'accueil et le nouvel onglet
            # a l'URL utile, on ramène l'URL sur keep avant de fermer.
            extra_url = extra.url or ""
            keep_url = keep.url or ""
            if (
                extra_url.startswith("http")
                and "register" in extra_url
                and "register" not in keep_url
            ):
                try:
                    keep.goto(extra_url, wait_until="domcontentloaded", timeout=60_000)
                except Exception:
                    pass
            extra.close()
        except Exception:
            continue
    return keep


class Runner:
    def __init__(
        self,
        scenario: Scenario,
        settings: Settings,
        *,
        headless: bool = False,
        debug_overlay: bool = False,
        until: str | None = None,
        record_output: Path | None = None,
        on_beat_start: Callable[[Step, BeatTimeline], None] | None = None,
        audio_durations_ms: dict[str, int] | None = None,
    ) -> None:
        self.scenario = scenario
        self.settings = settings
        self.headless = headless
        self.debug_overlay = debug_overlay
        self.until = until
        self.record_output = record_output
        self.on_beat_start = on_beat_start
        self.audio_durations_ms = audio_durations_ms or {}
        self.instance_url: str | None = None
        self.timeline_log: list[dict[str, Any]] = []

    def run(self) -> Path | None:
        width = self.scenario.viewport["width"]
        height = self.scenario.viewport["height"]
        capture_session: PlaywrightCaptureSession | None = None
        video_path: Path | None = None

        with sync_playwright() as p:
            launch_kwargs: dict[str, Any] = {"headless": self.headless}
            if not self.headless:
                mon = pick_film_monitor()
                chrome_args, vp_override = headed_launch_args(width, height, monitor=mon)
                if mon is not None:
                    print(
                        f"[runner] écran filmage: {mon.name} "
                        f"{mon.width}x{mon.height}+{mon.x}+{mon.y}"
                        f"{' (externe)' if mon.is_external else ''}"
                    )
                else:
                    print(
                        "[runner] aucun moniteur externe détecté "
                        "(TUTO_FILM_MONITOR / xrandr) — fenêtre sur DISPLAY courant"
                    )
                if chrome_args:
                    launch_kwargs["args"] = chrome_args
                if vp_override:
                    width, height = vp_override["width"], vp_override["height"]
                    print(f"[runner] viewport adapté à l'écran: {width}x{height}")
            browser = p.chromium.launch(**launch_kwargs)
            ctx_kwargs: dict[str, Any] = {
                "viewport": {"width": width, "height": height},
                "locale": "fr-FR",
            }
            if self.record_output:
                capture_session = PlaywrightCaptureSession(self.record_output, width, height)
                ctx_kwargs.update(capture_session.context_kwargs())
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            if capture_session:
                capture_session.bind_page(page)
            if not self.headless:
                mon = pick_film_monitor()
                maximize_page_on_monitor(page, mon)
                if mon is not None:
                    print(
                        f"[runner] fenêtre maximisée sur {mon.name} "
                        f"({mon.width}x{mon.height}+{mon.x}+{mon.y})"
                    )

            page.goto(self.scenario.start_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1000)
            dismiss_overlays(page)
            inject_chrome(page)

            for step in self.scenario.steps:
                if self.until and step.until == self.until:
                    # stop BEFORE executing this step's submit side-effects if until matches
                    # Convention: until marker means "stop before this step"
                    break

                page = close_extra_pages(page)
                wait_page_ready(page)
                clear_highlight(page)

                audio_ms = self._resolve_audio_ms(step)
                est_action = estimate_actions_ms(
                    step.actions, self.scenario.choreography.type_delay_ms
                )
                timeline = plan_beat(audio_ms, self.scenario.choreography, est_action)
                if self.on_beat_start:
                    self.on_beat_start(step, timeline)

                # 1) Prep silencieux : seulement si la cible est DÉJÀ sur la page
                #    (pas de highlight avant un goto / login — sinon voix et UI décalées).
                prep_t0 = time.monotonic()
                set_debug(
                    page,
                    f"beat={step.id} PREP\naudio={timeline.audio_ms}ms\n{step.narrate[:100]}",
                    self.debug_overlay,
                )
                target = self._prep_target(page, step.actions)
                if target is not None:
                    try:
                        prepare_for_action(
                            page,
                            target,
                            pointer_ms=min(timeline.pointer_ms, 180),
                            highlight_target=True,
                        )
                    except Exception:
                        page.wait_for_timeout(120)
                prep_ms = int((time.monotonic() - prep_t0) * 1000)

                # 2) Voix + action : action_at=0 → l'action part avec le début de la voix
                beat_t0 = time.monotonic()
                set_debug(
                    page,
                    f"beat={step.id}\naudio={timeline.audio_ms}ms\n"
                    f"action_at={timeline.action_at_ms}ms prep={prep_ms}ms\n"
                    f"{step.narrate[:100]}",
                    self.debug_overlay,
                )
                if timeline.action_at_ms > 0:
                    page.wait_for_timeout(timeline.action_at_ms)

                if target is not None:
                    try:
                        move_pointer_to(page, target, pointer_ms=60, center_first=True)
                    except Exception:
                        pass

                action_start = time.monotonic()
                page = self._run_actions(
                    page, step.actions, spotlight_ready=target is not None
                )
                page = close_extra_pages(page)
                action_elapsed = int((time.monotonic() - action_start) * 1000)

                # 3) Tenir jusqu'à la fin de la voix réelle (ffprobe), pas la longueur texte
                audio_ms = max(audio_ms, self._resolve_audio_ms(step))
                audio_tail_pad_ms = 200
                elapsed = int((time.monotonic() - beat_t0) * 1000)
                remain_audio = max(0, audio_ms + audio_tail_pad_ms - elapsed)
                page.wait_for_timeout(remain_audio)
                page.wait_for_timeout(max(0, timeline.settle_ms))
                clear_highlight(page)

                actual_ms = int((time.monotonic() - prep_t0) * 1000)
                min_slot = prep_ms + audio_ms + audio_tail_pad_ms + timeline.settle_ms
                if actual_ms < min_slot:
                    page.wait_for_timeout(min_slot - actual_ms)
                    actual_ms = int((time.monotonic() - prep_t0) * 1000)

                self.timeline_log.append(
                    {
                        "step_id": step.id,
                        "audio_ms": audio_ms,
                        "prep_ms": prep_ms,
                        "action_ms": action_elapsed,
                        "total_planned_ms": timeline.total_ms + prep_ms,
                        "actual_ms": actual_ms,
                        "narrate": step.narrate,
                    }
                )

                if self.until and any(
                    a.get("until") == self.until for a in step.actions if isinstance(a, dict)
                ):
                    break

            page = close_extra_pages(page)
            context.close()
            browser.close()
            if capture_session:
                video_path = capture_session.save_after_close()
        return video_path

    def _prep_target(self, page: Page, actions: list[dict[str, Any]]) -> Locator | None:
        """Cible à préparer avant la voix — None si une navigation réelle précède."""
        for action in actions:
            if "goto" in action:
                url = action["goto"]
                if url == "{{start_url}}":
                    url = self.scenario.start_url
                # Skip goto déjà satisfait : la cible suivante peut être prépable
                if self._already_on(page, url):
                    continue
                return None
            if action.get("action") in {
                "login_dolibarr",
                "fill_verify_code",
                "wait_registration_success",
                "wait_instance",
                "fill_captcha",
            }:
                return None
            if "scroll" in action:
                target = action["scroll"].get("target")
                if target:
                    try:
                        loc = resolve_locator(page, target)
                        ensure_centered(page, loc)
                    except Exception:
                        pass
                continue
            try:
                if "highlight" in action:
                    return resolve_locator(page, action["highlight"])
                if "click" in action:
                    return resolve_locator(page, action["click"])
                if "type" in action:
                    return resolve_locator(page, action["type"])
            except Exception:
                continue
        return None

    def _first_target(self, page: Page, actions: list[dict[str, Any]]) -> Locator | None:
        return self._prep_target(page, actions)

    @staticmethod
    def _norm_nav_url(url: str) -> str:
        """Normalise URL Angular/Dolibarr pour comparer sans recharger."""
        from urllib.parse import urlsplit, urlunsplit

        u = urlsplit((url or "").strip())
        path = u.path or "/"
        if path.endswith("/index.php"):
            path = path[: -len("/index.php")] or "/"
        path = path.rstrip("/") or "/"
        # garder le hash (route SPA)
        return urlunsplit((u.scheme, u.netloc, path, "", u.fragment))

    def _already_on(self, page: Page, url: str) -> bool:
        try:
            return self._norm_nav_url(page.url) == self._norm_nav_url(url)
        except Exception:
            return False

    def _resolve_audio_ms(self, step: Step) -> int:
        """Durée audio réelle (ffprobe fichier) prioritaire sur le cache."""
        try:
            from tutosvideo.audio_pipeline import find_audio_file, scenario_output_dir
            from tutosvideo.tts import audio_duration_seconds

            path = find_audio_file(scenario_output_dir(self.scenario) / "audio", step.id)
            if path is not None:
                ms = int(round(audio_duration_seconds(path) * 1000))
                if ms > 0:
                    return ms
        except Exception:
            pass
        cached = self.audio_durations_ms.get(step.id)
        if cached:
            return int(cached)
        # preview sans TTS : estime ~14 car/s FR
        return max(2500, int(len(step.narrate) / 14 * 1000))

    def _fill_verify_code(self, page: Page) -> Page:
        """Après submit inscription MGC : attendre le panneau code, IMAP, valider.

        Flux réel (JS magestioncloud) :
        1. submit intercepté → AJAX send verification code
        2. panneau ``#magestioncloud-email-verify-panel`` affiché
        3. saisie code + ``#magestioncloud-email-verify-btn``
        4. AJAX verify OK → ``submitRegistrationForm()`` (vraie création)
        """
        wait_page_ready(page)
        panel = page.locator("#magestioncloud-email-verify-panel")
        code_input = page.locator("#magestioncloud-email-verify-code")
        verify_btn = page.locator("#magestioncloud-email-verify-btn")

        def _panel_open() -> bool:
            try:
                return bool(
                    page.evaluate(
                        """() => {
                          const p = document.querySelector('#magestioncloud-email-verify-panel');
                          if (!p) return false;
                          return getComputedStyle(p).display !== 'none';
                        }"""
                    )
                )
            except Exception:
                return False

        # Attendre l'ouverture du panneau (après AJAX send) — max ~3 min
        deadline = time.time() + 180
        last_err = ""
        while time.time() < deadline:
            if _panel_open():
                break
            # Erreurs inline / rate-limit
            try:
                msg = page.evaluate(
                    """() => {
                      const el = document.querySelector(
                        '#magestioncloud-register-inline-errors, .mgc-register-inline-errors, '
                        + '#magestioncloud-email-verify-status'
                      );
                      return (el && (el.innerText || '').trim()) || '';
                    }"""
                )
                if msg:
                    last_err = msg
                    print(f"[runner] verify UI: {msg}")
                    if "trop de demandes" in msg.lower() or "réessayez" in msg.lower() or "reessayez" in msg.lower():
                        raise SystemExit(
                            "Rate-limit code e-mail MGC (« Trop de demandes »). "
                            "Attendez quelques minutes puis relancez le filmage."
                        )
            except SystemExit:
                raise
            except Exception:
                pass
            page.wait_for_timeout(500)
            wait_page_ready(page)
        else:
            print(
                "[runner] fill_verify_code: panneau code introuvable"
                + (f" ({last_err})" if last_err else "")
            )
            return close_extra_pages(page)

        # Centrer le panneau / champ
        try:
            ensure_centered(page, code_input.first)
        except Exception:
            try:
                ensure_centered(page, panel.first)
            except Exception:
                pass

        try:
            code = wait_for_verify_code(self.settings, timeout_s=180)
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] fill_verify_code IMAP: {exc}")
            raise

        print(f"[runner] code e-mail reçu, saisie…")
        try:
            prepare_for_action(page, code_input.first, pointer_ms=280, highlight_target=True)
            click_at_locator(page, code_input.first)
            code_input.first.fill("")
            code_input.first.press_sequentially(
                str(code), delay=self.scenario.choreography.type_delay_ms
            )
            page.wait_for_timeout(400)
            if verify_btn.count() and verify_btn.first.is_visible():
                prepare_for_action(page, verify_btn.first, pointer_ms=220, highlight_target=True)
                click_at_locator(page, verify_btn.first)
            else:
                page.locator("button:has-text('Valider le code'), button:has-text('Verify code')").first.click()
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] fill_verify_code saisie: {exc}")
            raise

        # Après verify OK le JS soumet le formulaire — attendre navigation / masque
        page.wait_for_timeout(1500)
        wait_page_ready(page)
        return close_extra_pages(page)

    def _wait_registration_success(self, page: Page) -> Page:
        """Attend l'écran de succès affichant l'URL d'instance — ne navigue pas avant."""
        wait_page_ready(page)
        deadline = time.time() + 600
        instance_url: str | None = None
        while time.time() < deadline:
            info = page.evaluate(
                """() => {
                  const t = document.body ? document.body.innerText : '';
                  const hrefs = [...document.querySelectorAll('a[href]')].map(a => a.href);
                  const textUrls = (t.match(/https?:\\/\\/[^\\s<>\"']+/gi) || []);
                  const all = [...hrefs, ...textUrls];
                  const instance = all.find(u =>
                    /https?:\\/\\/[a-z0-9-]+\\.(mgc\\d+|avec)\\.ma-gestion-cloud\\.fr/i.test(u)
                  ) || null;
                  const success = /succ[eè]s|f[eé]licitations|instance\\s+(est\\s+)?pr[eê]te|votre instance|connectez-vous/i.test(t);
                  return {
                    url: location.href,
                    instance,
                    success,
                    snippet: t.replace(/\\s+/g, ' ').slice(0, 240),
                  };
                }"""
            )
            if info and info.get("instance"):
                instance_url = str(info["instance"]).rstrip("/") + "/"
                print(f"[runner] URL instance sur écran succès : {instance_url}")
                break
            if info and info.get("success"):
                print(f"[runner] succès détecté, URL pas encore visible… {info.get('snippet','')[:120]}")
            page.wait_for_timeout(2000)
            wait_page_ready(page)
        else:
            raise SystemExit(
                "Écran de succès inscription introuvable (URL d'instance absente). "
                "Le filmage s'arrête sans redirection prématurée."
            )

        self.instance_url = instance_url
        # Mettre en évidence le lien (sans cliquer encore — wait_instance s'en charge)
        try:
            host = instance_url.rstrip("/").split("//")[-1].split("/")[0]
            link = page.locator(f'a[href*="{host}"]').first
            if link.count():
                prepare_for_action(page, link, pointer_ms=220, highlight_target=True)
                page.wait_for_timeout(1200)
        except Exception:
            pass
        return close_extra_pages(page)

    def _page_alive(self, page: Page) -> bool:
        try:
            return not page.is_closed() and bool(page.evaluate("() => true"))
        except Exception:
            return False

    def _open_instance(self, page: Page) -> Page:
        """Ouvre l'instance : clic filmé sur le lien succès, sinon goto."""
        url = (self.instance_url or "").rstrip("/") + "/"
        if not url.startswith("http"):
            raise SystemExit("Aucune URL d'instance à ouvrir.")

        host = url.split("//")[-1].split("/")[0]
        print(f"[runner] ouverture instance : {url}")

        # 1) Clic sur le lien visible de l'écran de succès (meilleur pour le film)
        try:
            if self._page_alive(page):
                link = page.locator(f'a[href*="{host}"]').first
                if link.count() and link.is_visible():
                    prepare_for_action(page, link, pointer_ms=260, highlight_target=True)
                    page.wait_for_timeout(700)
                    force_same_tab(link)
                    before = list(page.context.pages)
                    click_at_locator(page, link, timeout_ms=10_000, highlight_target=False)
                    wait_page_ready(page)
                    page.wait_for_timeout(800)
                    newcomers = [p for p in page.context.pages if p not in before]
                    if newcomers:
                        new = newcomers[0]
                        new_url = new.url or ""
                        for p in newcomers:
                            try:
                                p.close()
                            except Exception:
                                pass
                        if new_url.startswith("http"):
                            page.goto(new_url, wait_until="domcontentloaded", timeout=120_000)
                            wait_page_ready(page)
                    # Si on est toujours sur myaccount, forcer goto
                    if host not in (page.url or ""):
                        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
                        wait_page_ready(page)
                    inject_chrome(page)
                    print(f"[runner] instance ouverte (clic lien) → {page.url}")
                    return close_extra_pages(page)
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] clic lien instance échoué ({exc}) — goto direct")

        # 2) Fallback navigation directe
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        wait_page_ready(page)
        inject_chrome(page)
        print(f"[runner] instance ouverte (goto) → {page.url}")
        return close_extra_pages(page)

    def _wait_instance_ready(self, page: Page) -> Page:
        """Ouvre le lien d'instance affiché, puis attend que Dolibarr soit utilisable.

        Important : pendant le provisionnement MGC, le HTTP peut boucler en redirects
        alors que l'URL est déjà visible à l'écran. On clique / navigue d'abord,
        on n'attend PAS un probe HTTP « vert » avant le clic.
        """
        if not self.instance_url:
            page = self._wait_registration_success(page)
        preferred = self.instance_url
        print(f"[runner] ouverture du lien instance (url={preferred})…")

        # Laisser voir le lien cadré, puis clic / goto immédiatement
        try:
            host = (preferred or "").split("//")[-1].split("/")[0]
            if host and self._page_alive(page):
                link = page.locator(f'a[href*="{host}"]').first
                if link.count() and link.is_visible():
                    prepare_for_action(page, link, pointer_ms=280, highlight_target=True)
                    page.wait_for_timeout(900)
        except Exception:
            pass

        page = self._open_instance(page)

        # Sur l'instance : attendre page login / dashboard (rechargements si install)
        deadline = time.time() + 600
        n = 0
        while time.time() < deadline:
            n += 1
            if not self._page_alive(page):
                raise SystemExit("Navigateur fermé pendant l'attente Dolibarr sur l'instance.")
            try:
                ready = page.evaluate(
                    """() => {
                      const u = location.href || '';
                      const hasLogin = !!document.querySelector(
                        "input[type='password'], input[name='username'], input#username, input[name='login']"
                      );
                      const hasDash = !!document.querySelector(
                        '#id-right, #mainmenutop, .mainmenu, #topmenu-login-dropdown'
                      );
                      const installing = /install|provision|création|creation|patience|please wait|en cours/i.test(
                        document.body ? document.body.innerText : ''
                      );
                      return { u, hasLogin, hasDash, installing };
                    }"""
                )
            except Exception:
                ready = None
            if ready and (ready.get("hasLogin") or ready.get("hasDash")):
                print(f"[runner] instance prête (login/dashboard) → {ready.get('u')}")
                break
            if n == 1 or n % 4 == 0:
                print(
                    f"[runner] attente page Dolibarr (essai {n})… "
                    f"{(ready or {}).get('u', page.url)}"
                )
            time.sleep(6)
            try:
                if self._page_alive(page):
                    # Recharger périodiquement si page d'install / boucle
                    if n % 3 == 0:
                        page.reload(wait_until="domcontentloaded", timeout=60_000)
                    else:
                        page.evaluate("() => true")
                    wait_page_ready(page)
                    inject_chrome(page)
            except Exception:
                # Si reload échoue, retenter goto URL succès
                try:
                    if preferred:
                        page.goto(preferred, wait_until="domcontentloaded", timeout=60_000)
                except Exception:
                    pass
        else:
            print("[runner] timeout attente login Dolibarr — on continue quand même")

        try:
            page.wait_for_timeout(800)
        except Exception:
            pass
        return close_extra_pages(page)

    def _run_actions(
        self,
        page: Page,
        actions: list[dict[str, Any]],
        *,
        spotlight_ready: bool = False,
    ) -> Page:
        delay = self.scenario.choreography.type_delay_ms
        for action in actions:
            if "goto" in action:
                url = action["goto"]
                if url == "{{start_url}}":
                    url = self.scenario.start_url
                if self._already_on(page, url):
                    print(f"[runner] déjà sur l'écran — skip goto ({url})")
                    wait_page_ready(page)
                    inject_chrome(page)
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    wait_page_ready(page)
                    dismiss_overlays(page)
                    inject_chrome(page)
                    page = close_extra_pages(page)
                    page.wait_for_timeout(400)
                spotlight_ready = False
            elif "scroll" in action:
                target = action["scroll"].get("target")
                if spotlight_ready:
                    # Ne pas clear : le cadre PREP reste ; on recentre seulement
                    if target:
                        loc = resolve_locator(page, target)
                        ensure_centered(page, loc)
                        move_pointer_to(page, loc, pointer_ms=120, center_first=False)
                        highlight(page, loc)
                    else:
                        page.mouse.wheel(0, int(action["scroll"].get("dy", 600)))
                else:
                    clear_highlight(page)
                    if target:
                        loc = resolve_locator(page, target)
                        ensure_centered(page, loc)
                        move_pointer_to(page, loc, pointer_ms=200, center_first=False)
                    else:
                        page.mouse.wheel(0, int(action["scroll"].get("dy", 600)))
                page.wait_for_timeout(400)
            elif "highlight" in action:
                loc = resolve_locator(page, action["highlight"])
                if spotlight_ready:
                    # Cadre déjà posé au PREP : pas de clear/repose (évite le double)
                    ensure_centered(page, loc)
                    highlight(page, loc)
                    page.wait_for_timeout(280)
                else:
                    clear_highlight(page)
                    prepare_for_action(page, loc, pointer_ms=180, highlight_target=True)
                    page.wait_for_timeout(350)
                spotlight_ready = True
            elif "click" in action:
                try:
                    hint = action["click"]
                    loc = resolve_locator(page, hint)
                    # Checkbox dans un <label> : un click souris toggle 2× → reste décoché
                    try:
                        if loc.evaluate("el => el.tagName === 'INPUT' && el.type === 'checkbox'"):
                            if not spotlight_ready:
                                prepare_for_action(
                                    page, loc, pointer_ms=200, highlight_target=True
                                )
                            page.wait_for_timeout(500)
                            loc.evaluate(
                                """el => {
                                  el.checked = true;
                                  el.dispatchEvent(new Event('input', {bubbles: true}));
                                  el.dispatchEvent(new Event('change', {bubbles: true}));
                                }"""
                            )
                            page.wait_for_timeout(350)
                            inject_chrome(page)
                            spotlight_ready = False
                            continue
                    except Exception:
                        pass
                    if not spotlight_ready:
                        prepare_for_action(page, loc, pointer_ms=260, highlight_target=True)
                    # Bouton déjà cadré au PREP : pause lecture puis clic (sans 2e cadre)
                    page.wait_for_timeout(450)
                    same_tab = True
                    if isinstance(hint, dict) and hint.get("same_tab") is False:
                        same_tab = False
                    if same_tab:
                        force_same_tab(loc)
                    before = list(page.context.pages)
                    click_at_locator(page, loc, timeout_ms=8000, highlight_target=False)
                    wait_page_ready(page)
                    page.wait_for_timeout(250)
                    # Nouvel onglet ouvert malgré same_tab → récupérer l'URL puis fermer
                    newcomers = [p for p in page.context.pages if p not in before]
                    if newcomers:
                        new = newcomers[0]
                        new_url = new.url or ""
                        for p in newcomers:
                            try:
                                p.close()
                            except Exception:
                                pass
                        if new_url.startswith("http") and new_url != page.url:
                            page.goto(new_url, wait_until="domcontentloaded", timeout=60_000)
                            wait_page_ready(page)
                    page = close_extra_pages(page)
                    spotlight_ready = False
                except Exception as exc:
                    print(f"[runner] click échoué ({action.get('click')}): {exc}")
                    continue
                page.wait_for_timeout(180)
                inject_chrome(page)
            elif "type" in action:
                spec = action["type"]
                try:
                    loc = resolve_locator(page, spec)
                    loc.wait_for(state="attached", timeout=8000)
                    if not spotlight_ready:
                        prepare_for_action(page, loc, pointer_ms=280, highlight_target=True)
                    else:
                        page.wait_for_timeout(280)
                    click_at_locator(page, loc, timeout_ms=5000, highlight_target=False)
                    loc.fill("")
                    text = str(spec.get("text") or "")
                    if spec.get("human", True):
                        loc.press_sequentially(text, delay=delay)
                    else:
                        loc.fill(text)
                    page.wait_for_timeout(200)
                    spotlight_ready = False
                except Exception as exc:  # noqa: BLE001
                    print(f"[runner] type ignoré ({spec}): {exc}")
                    continue
            elif "select" in action:
                spec = action["select"]
                try:
                    loc = resolve_locator(page, spec)
                    if not spotlight_ready:
                        prepare_for_action(page, loc, pointer_ms=220, highlight_target=True)
                    loc.select_option(spec.get("value") or spec.get("label"))
                    spotlight_ready = False
                except Exception as exc:  # noqa: BLE001
                    print(f"[runner] select ignoré ({spec}): {exc}")
            elif "wait" in action:
                page.wait_for_timeout(int(action["wait"]))
            elif "wait_for" in action:
                page.wait_for_selector(action["wait_for"], timeout=60_000)
            elif action.get("action") == "fill_verify_code":
                page = self._fill_verify_code(page)
            elif action.get("action") == "wait_registration_success":
                page = self._wait_registration_success(page)
            elif action.get("action") == "wait_instance":
                page = self._wait_instance_ready(page)
                spotlight_ready = False
            elif action.get("action") == "login_dolibarr":
                spec = action if isinstance(action, dict) else {}
                url = self.instance_url or spec.get("url")
                if not url:
                    sub = self.scenario.vars.get("subdomain") or self.settings.tuto_subdomain
                    if sub and "localhost" not in (self.scenario.start_url or ""):
                        raise SystemExit(
                            "login_dolibarr : aucune URL d'instance (écran de succès manquant). "
                            "Ajoutez wait_registration_success avant login."
                        )
                    url = self.scenario.start_url
                # Si on n'est pas encore sur l'instance, y aller explicitement
                try:
                    host = str(url).split("//")[-1].split("/")[0]
                    if host and host not in (page.url or ""):
                        page = self._open_instance(page)
                        url = self.instance_url or url
                except Exception:
                    pass
                pwd = (
                    os.getenv("TUTO_DOLIBARR_PASSWORD", "").strip()
                    or self.settings.tuto_password
                )
                login_name = (
                    str(self.scenario.vars.get("dolibarr_login") or "").strip()
                    or self.settings.tuto_dolibarr_login
                )
                login_dolibarr(page, url, login_name, pwd, self.settings)
                inject_chrome(page)
                page = close_extra_pages(page)
                spotlight_ready = False
            elif action.get("action") == "fill_captcha":
                from tutosvideo.captcha import solve_captcha_on_page

                spec = action if isinstance(action, dict) else {}
                try:
                    solve_captcha_on_page(
                        page,
                        self.settings,
                        image_hint=spec.get("image") or spec.get("img"),
                        input_hint=spec.get("input") or spec.get("field"),
                        retries=int(spec.get("retries") or 3),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[runner] fill_captcha: {exc}")
                    raise
            elif action.get("action") == "assert_dashboard":
                page.wait_for_timeout(2000)
                # soft assert: look for typical dolibarr chrome
                try:
                    page.locator(
                        "#id-right, #mainmenutop, .mainmenu, #topmenu-login-dropdown"
                    ).first.wait_for(timeout=15_000)
                except Exception:
                    pass
            else:
                continue
        return close_extra_pages(page)
