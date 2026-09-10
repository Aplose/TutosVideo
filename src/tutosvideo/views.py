"""Normalisation et déduplication des vues (URLs / SPA hash)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_view_url(url: str) -> str:
    """Clé de vue : même écran Angular / même page → même clé.

    Ex. ``…/index.php#/trainings`` et ``…/#/trainings`` → équivalents.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw == "{{start_url}}":
        return "{{start_url}}"
    parsed = urlparse(raw)
    path = parsed.path or "/"
    # /custom/foo/index.php → /custom/foo
    if path.endswith("/index.php"):
        path = path[: -len("/index.php")] or "/"
    path = path.rstrip("/") or "/"
    frag = (parsed.fragment or "").split("?", 1)[0].strip()
    if frag.startswith("/"):
        # Hash routing Angular : la vue = fragment
        return f"{parsed.scheme}://{parsed.netloc}{path}#{frag}".lower()
    if frag:
        return f"{parsed.scheme}://{parsed.netloc}{path}#{frag}".lower()
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()


def page_fingerprint(url: str, actions: list[dict[str, Any]] | None = None) -> str:
    """Empreinte de vue : URL normalisée (+ signatures contrôles si fournies)."""
    key = normalize_view_url(url)
    if not actions:
        return key
    # Signature légère des contrôles métier (ignore menus Dolibarr génériques)
    skip = re.compile(
        r"^(item-\d+|accueil|tiers|produits|gpao|projets|commerce|facturation|"
        r"banques|comptabilité|grh|documents|agenda|dolibarr)",
        re.I,
    )
    sig: list[str] = []
    for a in actions:
        name = str(a.get("name") or "").strip()
        if not name or skip.match(name.split()[0] if name else ""):
            continue
        if len(name) > 80:
            continue
        sig.append(f"{a.get('type')}:{name[:60]}")
    sig = sorted(set(sig))[:40]
    if not sig:
        return key
    return key + "|" + ",".join(sig)


def dedupe_visit_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """Déduplique une liste d'URL de visite. Retourne (uniques, ignorées)."""
    seen: set[str] = set()
    unique: list[str] = []
    skipped: list[str] = []
    for u in urls:
        key = normalize_view_url(u)
        if not key:
            continue
        if key in seen:
            skipped.append(u)
            continue
        seen.add(key)
        unique.append(u)
    return unique, skipped


def dedupe_catalog_pages(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fusionne les pages catalogue qui représentent la même vue.

    Retourne (pages_uniques, aliases) où aliases = [{id, same_as, url}].
    """
    out: list[dict[str, Any]] = []
    aliases: list[dict[str, str]] = []
    by_fp: dict[str, str] = {}
    for page in pages:
        url = str(page.get("url") or "")
        fp = page_fingerprint(url, list(page.get("actions") or []))
        # Si seule l'URL compte et qu'on a déjà cette URL normalisée
        url_key = normalize_view_url(url)
        existing = by_fp.get(fp) or by_fp.get(url_key)
        if existing:
            aliases.append(
                {
                    "id": str(page.get("id") or ""),
                    "same_as": existing,
                    "url": url,
                }
            )
            continue
        pid = str(page.get("id") or f"page-{len(out)}")
        by_fp[fp] = pid
        by_fp[url_key] = pid
        out.append(page)
    return out, aliases


def _step_nav_target(actions: list[dict[str, Any]]) -> str | None:
    """Cible de navigation d'une step, si purement navigante."""
    if not actions:
        return None
    gotos = [a.get("goto") for a in actions if isinstance(a, dict) and "goto" in a]
    clicks = [a for a in actions if isinstance(a, dict) and "click" in a]
    special = [
        a.get("action")
        for a in actions
        if isinstance(a, dict) and a.get("action") in ("login_dolibarr", "wait_instance")
    ]
    # Step mixte (type/fill + goto) : on ne fusionne pas
    heavy = [
        a
        for a in actions
        if isinstance(a, dict)
        and any(k in a for k in ("type", "select", "highlight", "wait", "wait_for"))
    ]
    if special:
        return f"__{special[0]}__"
    if gotos and not heavy:
        return normalize_view_url(str(gotos[-1]))
    if clicks and gotos and not heavy:
        return normalize_view_url(str(gotos[-1]))
    return None


def dedupe_scenario_steps(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Supprime les steps de navigation redondantes (même vue que la précédente).

    - Deux ``goto`` vers la même URL normalisée → garde la 1re, fusionne la narration.
    - Clic menu seul suivi d'une step avec ``goto`` → fusionne (évite Pédagogique + Formations).
    """
    if not steps:
        return [], []

    # Passe 1 : fusion clic-menu → step suivante avec goto
    pass1: list[dict[str, Any]] = []
    removed: list[str] = []
    i = 0
    while i < len(steps):
        cur = dict(steps[i])
        cur_actions = list(cur.get("actions") or [])
        only_click = (
            len(cur_actions) >= 1
            and all(isinstance(a, dict) and "click" in a for a in cur_actions)
            and not any(
                isinstance(a, dict) and any(k in a for k in ("goto", "type", "action", "wait"))
                for a in cur_actions
            )
        )
        if only_click and i + 1 < len(steps):
            nxt = dict(steps[i + 1])
            nxt_actions = list(nxt.get("actions") or [])
            if any(isinstance(a, dict) and "goto" in a for a in nxt_actions):
                n1 = str(cur.get("narrate") or "").strip()
                n2 = str(nxt.get("narrate") or "").strip()
                nxt["actions"] = cur_actions + nxt_actions
                if n1 and n2 and n1 not in n2:
                    nxt["narrate"] = f"{n1} {n2}"
                elif n1 and not n2:
                    nxt["narrate"] = n1
                removed.append(str(cur.get("id") or ""))
                pass1.append(nxt)
                i += 2
                continue
        pass1.append(cur)
        i += 1

    # Passe 2 : mêmes cibles goto consécutives
    out: list[dict[str, Any]] = []
    last_view: str | None = None
    for step in pass1:
        actions = list(step.get("actions") or [])
        target = _step_nav_target(actions)
        sid = str(step.get("id") or "")
        if target and last_view and target == last_view:
            removed.append(sid)
            if out and step.get("narrate"):
                prev = out[-1]
                prev_n = str(prev.get("narrate") or "").rstrip()
                extra = str(step.get("narrate") or "").strip()
                if extra and extra not in prev_n:
                    prev["narrate"] = f"{prev_n} {extra}".strip()
            continue
        out.append(step)
        if target:
            last_view = target
        else:
            for a in actions:
                if isinstance(a, dict) and "goto" in a:
                    last_view = normalize_view_url(str(a["goto"]))
                    break
    return out, removed
