"""Lecture de captchas texte (image) via Playwright + vision/OCR Mistral."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
from playwright.sync_api import Locator, Page

from tutosvideo.config import Settings

OCR_URL = "https://api.mistral.ai/v1/ocr"
CHAT_URL = "https://api.mistral.ai/v1/chat/completions"

CAPTCHA_IMG_SELECTORS = (
    "#img_securitycode",
    "img[src*='antispamimage']",
    "img[src*='captcha' i]",
    "img[id*='securitycode' i]",
    "img[id*='captcha' i]",
    "img[alt*='captcha' i]",
    "img[alt*='sécurité' i]",
    "img[alt*='security' i]",
)

CAPTCHA_INPUT_SELECTORS = (
    "input[name='code']",
    "input#securitycode",
    "input#code",
    "input[name*='captcha' i]",
    "input[id*='captcha' i]",
    "input[name*='security' i]",
    "input[id*='securitycode' i]",
    "input[placeholder*='code' i]",
    "input[placeholder*='captcha' i]",
)

REFRESH_SELECTORS = (
    "#captcha_refresh_img",
    "a[href*='captcha' i]",
    "img[src*='refresh' i]",
    "button:has-text('Rafraîchir')",
)


def normalize_captcha_text(raw: str) -> str:
    """Ne garde que les caractères alphanumériques du premier token lisible."""
    text = (raw or "").strip()
    if not text:
        return ""
    # Réponses du type {"code":"Ab12"} ou markdown
    m = re.search(r"\{[^}]*\"(?:code|text|captcha)\"\s*:\s*\"([^\"]+)\"", text, re.I)
    if m:
        text = m.group(1)
    for line in text.splitlines():
        first = line.strip().strip("`\"' ").strip()
        if not first or set(first) <= {"`", "-", "=", "~"}:
            continue
        cleaned = re.sub(r"[^A-Za-z0-9]", "", first)
        if cleaned:
            return cleaned
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text)
    return cleaned


def find_captcha_image(page: Page, hint: str | None = None) -> Locator | None:
    selectors = ((hint,) if hint else ()) + CAPTCHA_IMG_SELECTORS
    for sel in selectors:
        if not sel:
            continue
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def find_captcha_input(page: Page, hint: str | None = None) -> Locator | None:
    selectors = ((hint,) if hint else ()) + CAPTCHA_INPUT_SELECTORS
    for sel in selectors:
        if not sel:
            continue
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def refresh_captcha(page: Page) -> None:
    for sel in REFRESH_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(600)
                return
        except Exception:
            continue
    # Recharger l'image Dolibarr en forçant un nouveau src
    img = find_captcha_image(page)
    if img is None:
        return
    try:
        img.evaluate(
            """el => {
              const src = el.getAttribute('src') || '';
              const u = src.split('?')[0];
              el.setAttribute('src', u + '?t=' + Date.now());
            }"""
        )
        page.wait_for_timeout(600)
    except Exception:
        pass


def _ocr_mistral(settings: Settings, png: bytes) -> str:
    b64 = base64.b64encode(png).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            OCR_URL,
            headers={
                "Authorization": f"Bearer {settings.mistral_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "mistral-ocr-latest",
                "document": {"type": "image_url", "image_url": data_url},
            },
        )
        r.raise_for_status()
        payload = r.json()
    parts: list[str] = []
    for page in payload.get("pages") or []:
        md = page.get("markdown") or page.get("text") or ""
        if md:
            parts.append(str(md))
    return "\n".join(parts).strip()


def _vision_mistral(settings: Settings, png: bytes) -> str:
    b64 = base64.b64encode(png).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    model = settings.mistral_vision_model or "mistral-small-latest"
    prompt = (
        "Lis le code captcha (texte anti-spam) visible sur cette image. "
        "Réponds UNIQUEMENT avec les caractères du code, sans espaces ni ponctuation "
        "ni explication. Exemple de réponse valide : Ab3K"
    )
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            CHAT_URL,
            headers={
                "Authorization": f"Bearer {settings.mistral_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": data_url},
                        ],
                    }
                ],
            },
        )
        r.raise_for_status()
        payload = r.json()
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content).strip()


def read_captcha_from_image(settings: Settings, png: bytes) -> str:
    """OCR/vision → texte captcha normalisé."""
    if not settings.mistral_api_key:
        raise SystemExit("MISTRAL_API_KEY manquante pour lire le captcha (vision/OCR).")
    errors: list[str] = []
    # Vision d'abord (meilleur sur petites images type Dolibarr), OCR en secours
    for reader, name in ((_vision_mistral, "vision"), (_ocr_mistral, "ocr")):
        try:
            raw = reader(settings, png)
            code = normalize_captcha_text(raw)
            if code:
                return code
            errors.append(f"{name}: réponse vide ({raw!r})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    raise RuntimeError("Impossible de lire le captcha — " + " | ".join(errors))


def solve_captcha_on_page(
    page: Page,
    settings: Settings,
    *,
    image_hint: str | None = None,
    input_hint: str | None = None,
    retries: int = 3,
) -> str | None:
    """
    Localise l'image captcha, la lit via Mistral, remplit le champ.
    Retourne le code saisi, ou None s'il n'y a pas de captcha.
    """
    from tutosvideo.browser import ensure_centered, hide_film_overlays, prepare_for_action

    img = find_captcha_image(page, image_hint)
    field = find_captcha_input(page, input_hint)
    if img is None or field is None:
        return None

    last_err = ""
    for attempt in range(retries):
        try:
            if attempt:
                refresh_captcha(page)
                img = find_captcha_image(page, image_hint) or img
                field = find_captcha_input(page, input_hint) or field
            # Centrer pour le film, puis MASQUER overlays avant le screenshot OCR
            try:
                ensure_centered(page, img)
                prepare_for_action(page, img, pointer_ms=160, highlight_target=True)
                page.wait_for_timeout(350)
            except Exception:
                pass
            hide_film_overlays(page)
            page.wait_for_timeout(120)
            # Capture élément seule, sans pointeur / cadre orange par-dessus
            png = img.screenshot(type="png", animations="disabled")
            code = read_captcha_from_image(settings, png)
            if not code:
                last_err = "code vide"
                continue
            prepare_for_action(page, field, pointer_ms=220, highlight_target=True)
            field.click(timeout=5000)
            field.fill("")
            field.fill(code)
            page.wait_for_timeout(200)
            print(f"[captcha] code lu (essai {attempt + 1}): {code}")
            return code
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            print(f"[captcha] échec essai {attempt + 1}: {exc}")
            hide_film_overlays(page)
            page.wait_for_timeout(400)
    raise RuntimeError(f"Échec lecture captcha après {retries} essais : {last_err}")


def captcha_present(page: Page) -> bool:
    return find_captcha_image(page) is not None and find_captcha_input(page) is not None
