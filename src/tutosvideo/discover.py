"""Analyse automatique du parcours (catalogue d'actions, sans soumettre)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page, sync_playwright

DEFAULT_START = "https://www.ma-gestion-cloud.fr/"
PLAN_TITLES = ["Essentiel", "Professionnel", "Formation", "Véto", "Ferme", "Premium"]


def _slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9àâäéèêëïîôùûüç]+", "-", text, flags=re.I)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "item"


def dismiss_overlays(page: Page) -> None:
    candidates = [
        "button:has-text('Accepter')",
        "button:has-text('Accept')",
        "button:has-text('Tout accepter')",
        "button:has-text('J\\'accepte')",
        "[aria-label='Accepter']",
        "#tarteaucitronPersonalize2",
        ".tarteaucitronAllowAll",
        "button.cookie-accept",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=500):
                loc.click(timeout=2000)
                page.wait_for_timeout(300)
        except Exception:
            continue


def _collect_interactive(page: Page, page_id: str) -> list[dict[str, Any]]:
    script = """
    () => {
      const items = [];
      const seen = new Set();
      const push = (el, type) => {
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return;
        let role = el.getAttribute('role') || '';
        let name = (el.getAttribute('aria-label')
          || el.innerText
          || el.getAttribute('placeholder')
          || el.getAttribute('name')
          || el.getAttribute('id')
          || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
        if (!name || /^mat-input-\\d+$/i.test(name) || /^mat-mdc-/i.test(name)) {
          const id = el.getAttribute('id');
          if (id) {
            const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
            if (lab) name = lab.innerText.trim().slice(0, 120);
          }
          // Angular Material : mat-label dans le mat-form-field parent
          if (!name || /^mat-/i.test(name)) {
            const field = el.closest('mat-form-field, .mat-mdc-form-field');
            const ml = field && field.querySelector('mat-label, .mat-mdc-floating-label');
            if (ml && (ml.innerText || '').trim()) name = ml.innerText.trim().slice(0, 120);
          }
          if (!name) name = el.getAttribute('formcontrolname') || el.getAttribute('id') || '';
        }
        const key = type + '|' + name + '|' + Math.round(rect.top) + '|' + Math.round(rect.left);
        if (seen.has(key)) return;
        seen.add(key);
        items.push({
          type,
          role: role || el.tagName.toLowerCase(),
          name,
          tag: el.tagName.toLowerCase(),
          inputType: el.getAttribute('type') || '',
          href: el.getAttribute('href') || '',
          idAttr: el.getAttribute('id') || '',
          nameAttr: el.getAttribute('name') || '',
          placeholder: el.getAttribute('placeholder') || '',
          bbox: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
        });
      };
      document.querySelectorAll('a[href]').forEach(el => push(el, 'link'));
      document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]').forEach(el => push(el, 'button'));
      document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select').forEach(el => {
        const t = (el.getAttribute('type') || el.tagName.toLowerCase());
        let type = 'textbox';
        if (t === 'radio') type = 'radio';
        else if (t === 'checkbox') type = 'checkbox';
        else if (el.tagName === 'SELECT') type = 'select';
        else if (t === 'password') type = 'textbox';
        push(el, type);
      });
      return items;
    }
    """
    raw_items: list[dict[str, Any]] = []
    last_exc: BaseException | None = None
    for attempt in range(3):
        try:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8_000)
            except Exception:
                pass
            raw_items = page.evaluate(script)
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if "execution context was destroyed" not in str(exc).lower():
                raise
            page.wait_for_timeout(250 + attempt * 150)
    if last_exc is not None:
        raise last_exc
    actions: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        name = item.get("name") or item.get("placeholder") or item.get("nameAttr") or f"item-{idx}"
        action_id = f"{page_id}.{item['type']}.{_slug(name)}"
        selector_hint: dict[str, Any]
        if item.get("idAttr"):
            selector_hint = {"css": f"#{item['idAttr']}"}
        elif item["type"] in ("textbox", "select", "checkbox", "radio") and item.get("nameAttr"):
            selector_hint = {"css": f"[name='{item['nameAttr']}']"}
        elif item["type"] == "link" and name:
            selector_hint = {"role": "link", "name": name[:80]}
        elif item["type"] == "button" and name:
            selector_hint = {"role": "button", "name": name[:80]}
        elif name:
            selector_hint = {"text": name[:80]}
        else:
            selector_hint = {"css": item["tag"]}
        actions.append(
            {
                "id": action_id,
                "type": item["type"],
                "role": item.get("role"),
                "name": name,
                "selector_hint": selector_hint,
                "href": item.get("href") or None,
                "bbox": item.get("bbox"),
            }
        )
    return actions


def _detect_plans(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plans = []
    for title in PLAN_TITLES:
        slug = _slug(title)
        related = [
            a
            for a in actions
            if title.lower() in (a.get("name") or "").lower()
            or title.lower() in (a.get("href") or "").lower()
        ]
        plans.append(
            {
                "id": f"plans.{slug}",
                "title": title,
                "action_ids": [a["id"] for a in related],
            }
        )
    return plans


def _find_register_url(page: Page, actions: list[dict[str, Any]]) -> str | None:
    for a in actions:
        href = a.get("href") or ""
        if "register" in href.lower() or "myaccount" in href.lower():
            return urljoin(page.url, href)
    # CTA text
    for a in actions:
        name = (a.get("name") or "").lower()
        if "essai" in name or "démarrer" in name or "demarrer" in name:
            href = a.get("href")
            if href:
                return urljoin(page.url, href)
    return None


def _proposed_path(home_actions: list[dict[str, Any]], form_actions: list[dict[str, Any]]) -> dict[str, Any]:
    happy: list[str] = []
    optional: list[str] = []

    # hero / tarifs
    for a in home_actions:
        n = (a.get("name") or "").lower()
        if "tarif" in n or "essai" in n or "essentiel" in n:
            if a["id"] not in happy:
                happy.append(a["id"])

    for title in PLAN_TITLES:
        for a in home_actions:
            if title.lower() in (a.get("name") or "").lower():
                if title == "Essentiel":
                    if a["id"] not in happy:
                        happy.append(a["id"])
                else:
                    if a["id"] not in optional:
                        optional.append(a["id"])

    field_order = [
        "email",
        "mail",
        "société",
        "societe",
        "company",
        "organization",
        "téléphone",
        "telephone",
        "phone",
        "password",
        "mot de passe",
        "repeat",
        "confirm",
        "country",
        "pays",
        "domain",
        "adresse",
        "subdomain",
    ]
    for key in field_order:
        for a in form_actions:
            blob = " ".join(
                filter(
                    None,
                    [
                        a.get("name"),
                        str(a.get("selector_hint")),
                    ],
                )
            ).lower()
            if key in blob and a["id"] not in happy:
                happy.append(a["id"])
                break

    return {
        "objective": "creer-instance-essentiel",
        "happy_path": happy,
        "optional": optional,
        "notes": (
            "Ne pas soumettre le formulaire pendant discover. "
            "Montrer les autres plans puis revenir sur Essentiel."
        ),
    }


def discover(
    scenario_dir: Path,
    start_url: str = DEFAULT_START,
    headless: bool = True,
    brief: str | None = None,
    *,
    login: str | None = None,
    password: str | None = None,
    visit_urls: list[str] | None = None,
    skip_mgc_heuristics: bool = False,
) -> Path:
    """Catalogue les contrôles interactifs.

    Mode générique (Dolibarr / SPA) :
    - login + password optionnels (formulaire Dolibarr)
    - visit_urls : pages additionnelles à cataloguer (hash Angular OK)
    - skip_mgc_heuristics : ne pas chercher Tarifs / inscription MGC
    """
    from tutosvideo.scenario_prompts import load_discover_prompt, save_discover_prompt

    scenario_dir.mkdir(parents=True, exist_ok=True)
    if brief is None:
        brief = load_discover_prompt(scenario_dir)
    else:
        save_discover_prompt(scenario_dir, brief)

    if visit_urls is None:
        visit_urls = _parse_visit_urls_from_brief(brief, start_url)

    from tutosvideo.views import dedupe_catalog_pages, dedupe_visit_urls, normalize_view_url

    visit_urls, skipped_visits = dedupe_visit_urls(list(visit_urls or []))
    if skipped_visits:
        print(
            "[discover] Visites ignorées (même vue déjà prévue) : "
            + ", ".join(skipped_visits)
        )

    # Auto : parcours Dolibarr / Angular → pas d'heuristiques MGC
    if visit_urls or login or password:
        skip_mgc_heuristics = True

    pages_data: list[dict[str, Any]] = []
    all_actions: list[dict[str, Any]] = []
    form_actions: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    seen_views: set[str] = set()
    skipped_live: list[dict[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="fr-FR")
        page = context.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        dismiss_overlays(page)

        if login and password:
            _maybe_login_dolibarr(page, login, password)
            dismiss_overlays(page)

        home_actions = _collect_interactive(page, "home")
        pages_data.append(
            {
                "id": "home",
                "url": page.url,
                "title": page.title(),
                "actions": home_actions,
            }
        )
        seen_views.add(normalize_view_url(page.url))
        all_actions.extend(home_actions)

        if not skip_mgc_heuristics:
            plans = _detect_plans(home_actions)
            try:
                page.locator("text=Tarifs").first.scroll_into_view_if_needed(timeout=3000)
                page.wait_for_timeout(500)
            except Exception:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.45)")
                page.wait_for_timeout(500)

            register_url = _find_register_url(page, home_actions)
            if register_url:
                page.goto(register_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(1500)
                dismiss_overlays(page)
                form_actions = _collect_interactive(page, "register")
                pages_data.append(
                    {
                        "id": "register",
                        "url": page.url,
                        "title": page.title(),
                        "actions": form_actions,
                    }
                )
                seen_views.add(normalize_view_url(page.url))
                all_actions.extend(form_actions)

        for idx, visit in enumerate(visit_urls or []):
            page_id = _page_id_from_url(visit, idx)
            visit_key = normalize_view_url(visit)
            if visit_key and visit_key in seen_views:
                skipped_live.append(
                    {"id": page_id, "url": visit, "reason": "même vue déjà cataloguée"}
                )
                print(f"[discover] Skip {visit} (vue déjà vue)")
                continue
            try:
                page.goto(visit, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:  # noqa: BLE001
                print(f"[discover] goto {visit} : {exc}")
                continue
            # SPA Angular : laisser le rendu
            page.wait_for_timeout(2500)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            dismiss_overlays(page)
            actual_key = normalize_view_url(page.url)
            if actual_key and actual_key in seen_views:
                skipped_live.append(
                    {
                        "id": page_id,
                        "url": page.url,
                        "reason": "après navigation, même vue qu'une page déjà cataloguée",
                    }
                )
                print(f"[discover] Skip catalogue {visit} → {page.url} (doublon)")
                continue
            actions = _collect_interactive(page, page_id)
            pages_data.append(
                {
                    "id": page_id,
                    "url": page.url,
                    "title": page.title(),
                    "actions": actions,
                }
            )
            if actual_key:
                seen_views.add(actual_key)
            all_actions.extend(actions)

        browser.close()

    pages_data, page_aliases = dedupe_catalog_pages(pages_data)
    view_aliases = list(page_aliases)
    for row in skipped_live:
        view_aliases.append(
            {
                "id": row["id"],
                "same_as": "(déjà cataloguée)",
                "url": row["url"],
                "reason": row.get("reason", ""),
            }
        )
    if skipped_visits:
        for u in skipped_visits:
            view_aliases.append(
                {
                    "id": "",
                    "same_as": "(visite prévue en double)",
                    "url": u,
                    "reason": "URL normalisée déjà dans --visit / brief",
                }
            )
    if view_aliases:
        print(f"[discover] {len(view_aliases)} vue(s) en double détectée(s)")

    if skip_mgc_heuristics:
        proposed = {
            "objective": scenario_dir.name,
            "happy_path": [a["id"] for a in all_actions[:40]],
            "optional": [],
            "notes": (brief or "")[:2000],
        }
    else:
        proposed = _proposed_path(
            pages_data[0]["actions"] if pages_data else [],
            form_actions,
        )
        if brief and brief.strip():
            proposed["notes"] = brief.strip()[:2000]

    process = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_url": start_url,
        "discover_brief": (brief or "").strip(),
        "visit_urls": list(visit_urls or []),
        "logged_in": bool(login and password),
        "pages": pages_data,
        "view_aliases": view_aliases,
        "plans": plans,
        "proposed_path": proposed,
        "safety": {
            "submitted_registration": False,
            "note": "discover catalogue sans soumettre les formulaires critiques",
        },
    }
    out = scenario_dir / "process.json"
    out.write_text(json.dumps(process, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _parse_visit_urls_from_brief(brief: str, start_url: str) -> list[str]:
    """Extrait les URLs / routes hash listées dans le brief (lignes « - … »)."""
    urls: list[str] = []
    base = start_url.rstrip("/")
    for line in (brief or "").splitlines():
        raw = line.strip()
        if not raw.startswith("-"):
            continue
        item = raw.lstrip("- ").strip().strip("`")
        if not item:
            continue
        looks_url = (
            item.startswith("http")
            or item.startswith("#")
            or item.startswith("/")
            or item.startswith("custom/")
            or "index.php" in item
            or "#/" in item
        )
        if not looks_url:
            continue
        if item.startswith("#"):
            urls.append(f"{base}{item}")
        elif item.startswith("http"):
            urls.append(item)
        elif item.startswith("/"):
            parsed = urlparse(start_url)
            urls.append(f"{parsed.scheme}://{parsed.netloc}{item}")
        else:
            parsed = urlparse(start_url)
            urls.append(f"{parsed.scheme}://{parsed.netloc}/{item.lstrip('/')}")
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _page_id_from_url(url: str, idx: int) -> str:
    if "#/" in url:
        frag = url.split("#/", 1)[1].split("?", 1)[0].strip("/")
        if frag:
            return _slug(frag.replace("/", "-")) or f"page-{idx}"
    path = urlparse(url).path.strip("/").replace("/", "-")
    return _slug(path) or f"page-{idx}"


def _maybe_login_dolibarr(page: Page, login: str, password: str) -> None:
    """Connexion Dolibarr si un formulaire login est visible."""
    user = page.locator("input[name='username'], input#username, input[name='login']").first
    pwd = page.locator("input[name='password'], input#password, input[type='password']").first
    try:
        if not user.count() or not pwd.count():
            return
        if not user.is_visible():
            return
    except Exception:
        return
    try:
        user.fill(login)
        pwd.fill(password)
        for sel in [
            "input[type='submit']",
            "button[type='submit']",
            "button:has-text('Connexion')",
            "button:has-text('Login')",
        ]:
            btn = page.locator(sel).first
            if btn.count():
                btn.click(timeout=5000)
                break
        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        print(f"[discover] login OK → {page.url}")
    except Exception as exc:  # noqa: BLE001
        print(f"[discover] login échoué : {exc}")
