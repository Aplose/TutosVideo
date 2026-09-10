"""Attente de l'instance Dolibarr et connexion."""

from __future__ import annotations

import time
from urllib.parse import urljoin

import httpx
from playwright.sync_api import Page

from tutosvideo.config import Settings

MGC_HOST_SUFFIXES = (
    ".mgc01.ma-gestion-cloud.fr",
    ".avec.ma-gestion-cloud.fr",
    ".ma-gestion-cloud.fr",
)


def instance_candidates(subdomain: str) -> list[str]:
    subdomain = subdomain.strip().lower().rstrip(".")
    urls = []
    for suffix in MGC_HOST_SUFFIXES:
        if subdomain.endswith(suffix.lstrip(".")):
            urls.append(f"https://{subdomain}")
        else:
            urls.append(f"https://{subdomain}{suffix}")
    # de-dup preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def probe_instance_url(url: str) -> str | None:
    """Retourne l'URL canonique si l'instance répond (<500), sinon None."""
    base = url.rstrip("/") + "/"
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0, max_redirects=8) as client:
            r = client.get(base)
            if r.status_code >= 500:
                return None
            login = urljoin(str(r.url), "/index.php?mainmenu=home")
            r2 = client.get(login)
            if r2.status_code < 500:
                return str(r.url).rstrip("/") + "/"
            return str(r.url).rstrip("/") + "/"
    except Exception:
        # Redirect loop / TLS / timeout pendant le provisionnement → pas prêt
        return None


def wait_for_instance(
    subdomain: str,
    timeout_s: int = 600,
    poll_s: int = 10,
    *,
    preferred_url: str | None = None,
) -> str:
    if not subdomain and not preferred_url:
        raise SystemExit("Sous-domaine d'instance manquant (TUTO_SUBDOMAIN / vars.subdomain).")
    candidates: list[str] = []
    if preferred_url:
        candidates.append(preferred_url.rstrip("/") + "/")
    if subdomain:
        candidates.extend(instance_candidates(subdomain))
    seen: set[str] = set()
    ordered: list[str] = []
    for u in candidates:
        key = u.rstrip("/")
        if key not in seen:
            seen.add(key)
            ordered.append(key + "/")
    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        for base in ordered:
            try:
                ready = probe_instance_url(base)
                if ready:
                    return ready
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
        time.sleep(poll_s)
    raise SystemExit(
        f"Instance non joignable pour '{subdomain or preferred_url}' après {timeout_s}s. "
        f"Dernière erreur : {last_err}"
    )


def _still_on_login(page: Page) -> bool:
    url = (page.url or "").lower()
    if "login" in url or "index.php" in url:
        try:
            if page.locator("input[type='password']").count():
                return True
        except Exception:
            pass
    try:
        err = page.locator(".error, .login_error, #login_error, .jnotify-notification-error")
        if err.count() and err.first.is_visible():
            return True
    except Exception:
        pass
    return False


def login_dolibarr(
    page: Page,
    base_url: str,
    login: str,
    password: str,
    settings: Settings | None = None,
    *,
    max_attempts: int = 3,
) -> None:
    from tutosvideo.browser import prepare_for_action, wait_page_ready
    from tutosvideo.captcha import captcha_present, solve_captcha_on_page

    page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
    wait_page_ready(page)
    page.wait_for_timeout(1000)

    for attempt in range(max_attempts):
        user_sels = [
            "input[name='username']",
            "input#username",
            "input[name='login']",
            "input[type='text']",
        ]
        pass_sels = ["input[name='password']", "input#password", "input[type='password']"]
        for sel in user_sels:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    prepare_for_action(page, loc, pointer_ms=180)
                    loc.click(timeout=3000)
                    loc.fill(login)
                    break
                except Exception:
                    continue
        for sel in pass_sels:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    prepare_for_action(page, loc, pointer_ms=180)
                    loc.click(timeout=3000)
                    loc.fill(password)
                    break
                except Exception:
                    continue

        if settings is not None and captcha_present(page):
            try:
                solve_captcha_on_page(page, settings, retries=3)
            except Exception as exc:  # noqa: BLE001
                print(f"[login] captcha: {exc}")
                if attempt + 1 >= max_attempts:
                    raise
                page.reload(wait_until="domcontentloaded")
                wait_page_ready(page)
                continue

        submitted = False
        for sel in [
            "input[type='submit']",
            "button[type='submit']",
            "button:has-text('Connexion')",
            "button:has-text('Login')",
            "input[name='button']",
        ]:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    prepare_for_action(page, loc, pointer_ms=200)
                    loc.click(timeout=5000)
                    submitted = True
                    break
                except Exception:
                    continue
        if not submitted:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        wait_page_ready(page)
        page.wait_for_timeout(2200)
        if not _still_on_login(page):
            return
        print(f"[login] encore sur la page login (essai {attempt + 1}/{max_attempts})")
        if attempt + 1 < max_attempts:
            try:
                page.reload(wait_until="domcontentloaded")
                wait_page_ready(page)
                page.wait_for_timeout(800)
            except Exception:
                pass
