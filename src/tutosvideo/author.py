"""Génération du script texte + YAML brouillon via Mistral (sans TTS ni capture)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from tutosvideo.config import Settings
from tutosvideo.schema import dump_scenario

AUTHORING_RULES = """
Tu rédiges un tutoriel vidéo produit.

Règles strictes :
1. Un paragraphe = UN seul geste à l'écran (un champ, un bouton, une carte tarif).
2. Nommer le contrôle visible tel qu'il apparaît (libellé exact).
3. Ne jamais décrire le champ N+1 pendant qu'on remplit le champ N.
4. Le prompt détaillé du scénario (prompt.md) est la source de vérité du parcours :
   applique les changements demandés dedans sans attendre une modification de code.
5. Chaque paragraphe doit référencer un action_id EXISTANT du catalogue process.json
   (ou un action_id synthétique du type plans.essentiel.card si pertinent).
6. Ton clair, professionnel, français.
7. Pas de jargon technique (pas de CSS, XPath, Playwright).
8. Avant chaque action UI, le runner saute directement au contrôle (pas de long scroll).
9. Si une captcha image (code de sécurité) est présente au login, login_dolibarr la lit
   automatiquement (vision Mistral) ; on peut aussi utiliser {\"action\": \"fill_captcha\"}.
10. Ne charge jamais deux fois la même vue : si deux menus (ex. Pédagogique et Formations)
    mènent à la même URL / même écran (voir pages.aliases ou urls normalisées dans
    process.json), une seule step de navigation suffit (goto direct ou un seul clic).
"""


def _script_md_from_steps(
    title: str, steps: list[dict[str, Any]], *, status: str = "brouillon"
) -> str:
    lines = [
        f"# Script — {title}",
        "",
        f"Statut : {status} — à valider." if status == "brouillon" else f"Statut : {status}.",
        "",
    ]
    for i, step in enumerate(steps, start=1):
        narrate = str(step.get("narrate") or "").strip()
        if narrate:
            lines.append(f"{i}. {narrate}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _apply_view_dedupe(
    scenario: dict[str, Any], script_md: str
) -> tuple[dict[str, Any], str, list[str]]:
    """Fusionne les steps de navigation redondantes ; régénère script.md si besoin."""
    from tutosvideo.views import dedupe_scenario_steps

    steps = list(scenario.get("steps") or [])
    new_steps, removed = dedupe_scenario_steps(steps)
    if not removed:
        return scenario, script_md, []
    scenario = dict(scenario)
    scenario["steps"] = new_steps
    title = str(scenario.get("title") or "Tutoriel")
    return scenario, _script_md_from_steps(title, new_steps), removed


def _fallback_script_and_yaml(process: dict[str, Any], objective: str) -> tuple[str, dict[str, Any]]:
    """Brouillon déterministe si Mistral est indisponible (offline / pas de clé)."""
    register_url = None
    for page in process.get("pages") or []:
        if page.get("id") == "register" and page.get("url"):
            register_url = page["url"]
            break

    plan_blurbs = {
        "Essentiel": (
            "Voici l'offre Essentiel : le tarif d'entrée pour un Dolibarr hébergé, "
            "sécurisé, avec utilisateurs illimités et essai de trente jours sans carte bancaire."
        ),
        "Professionnel": (
            "L'offre Professionnel ajoute davantage de capacités pour une équipe en croissance."
        ),
        "Formation": (
            "L'offre Formation est adaptée aux organismes et centres de formation."
        ),
        "Véto": "L'offre Véto cible les cliniques et cabinets vétérinaires.",
        "Ferme": "L'offre Ferme est conçue pour les exploitations agricoles.",
        "Premium": (
            "L'offre Premium regroupe le niveau le plus complet pour les besoins avancés."
        ),
    }

    paragraphs: list[tuple] = [
        (
            "home.intro",
            "Bienvenue sur Ma Gestion Cloud. Nous allons créer une instance Dolibarr "
            "hébergée, prête à l'emploi, en partant de la page d'accueil.",
            [{"goto": "{{start_url}}"}],
        ),
        (
            "home.tarifs",
            "Descendons jusqu'à la section Tarifs pour parcourir les offres disponibles.",
            [{"scroll": {"target": "text=Tarifs"}}, {"highlight": {"text": "Tarifs"}}],
        ),
    ]

    plans = list(process.get("plans") or [])
    if not plans:
        plans = [
            {"id": "plans.essentiel", "title": "Essentiel"},
            {"id": "plans.professionnel", "title": "Professionnel"},
            {"id": "plans.formation", "title": "Formation"},
            {"id": "plans.véto", "title": "Véto"},
            {"id": "plans.ferme", "title": "Ferme"},
            {"id": "plans.premium", "title": "Premium"},
        ]

    for plan in plans:
        title = str(plan.get("title") or "").strip()
        if not title:
            continue
        sid = str(plan.get("id") or f"plans.{title.lower()}")
        narrate = plan_blurbs.get(
            title,
            f"Voici l'offre {title}, disponible sur Ma Gestion Cloud.",
        )
        # Cible le titre de carte (pas le lien sticky du menu)
        needle = title.split()[0] if title else title
        paragraphs.append(
            (
                sid,
                narrate,
                [
                    {"scroll": {"target": f"text={needle}"}},
                    {"highlight": {"text": needle}},
                ],
            )
        )

    paragraphs.extend(
        [
            (
                "plans.essentiel.reprise",
                "Nous retenons l'offre Essentiel pour démarrer notre essai.",
                [{"scroll": {"target": "text=Essentiel"}}, {"highlight": {"text": "Essentiel"}}],
            ),
            (
                "plans.essentiel.cta",
                "Démarrons l'essai gratuit Essentiel.",
                (
                    [{"goto": register_url}]
                    if register_url
                    else [{"click": {"text": "Démarrer mon essai", "same_tab": True}}]
                ),
            ),
            (
                "register.plan-essentiel",
                "Sur la page d'inscription, sélectionnez l'onglet Essentiel.",
                [
                    {"click": {"text": "Essentiel"}},
                    {"click": {"role": "tab", "name": "Essentiel"}},
                ],
            ),
            (
                "register.email",
                "Indiquez votre adresse e-mail professionnelle.",
                [{"type": {"selector": "#username", "text": "{{vars.email}}", "human": True}}],
            ),
            (
                "register.company",
                "Saisissez le nom de votre société ou organisation.",
                [{"type": {"selector": "#orgName", "text": "{{vars.company}}", "human": True}}],
            ),
            (
                "register.phone",
                "Renseignez un numéro de téléphone de contact.",
                [{"type": {"selector": "#phone", "text": "{{vars.phone}}", "human": True}}],
            ),
            (
                "register.password",
                "Choisissez un mot de passe robuste, puis confirmez-le.",
                [
                    {"type": {"selector": "#password", "text": "{{vars.password}}", "human": True}},
                    {"type": {"selector": "#password2", "text": "{{vars.password}}", "human": True}},
                ],
            ),
            (
                "register.subdomain",
                "Choisissez l'adresse de votre application, le sous-domaine de votre instance.",
                [{"type": {"selector": "#sldAndSubdomain", "text": "{{vars.subdomain}}", "human": True}}],
            ),
            (
                "register.submit",
                "Validez le formulaire pour lancer la création de votre compte.",
                [{"click": {"css": "#newinstance"}}],
                "before_submit",
            ),
            (
                "register.verify",
                "Un code à six chiffres a été envoyé par e-mail. Saisissez-le pour confirmer votre adresse.",
                [{"action": "fill_verify_code"}],
            ),
            (
                "provision.wait",
                "Votre instance s'installe. Nous reprendrons dans quelques instants, une fois Dolibarr prêt.",
                [{"action": "wait_instance"}],
            ),
            (
                "dolibarr.login",
                "Connectez-vous à votre Dolibarr avec le compte administrateur "
                "(login admin, mot de passe, puis le code de sécurité affiché).",
                [{"action": "login_dolibarr"}],
            ),
            (
                "dolibarr.home",
                "Voici le tableau de bord : votre instance Essentiel est opérationnelle.",
                [{"action": "assert_dashboard"}],
            ),
        ]
    )

    lines = [f"# Script — {objective}", "", "Statut : brouillon — à valider.", ""]
    steps = []
    for i, item in enumerate(paragraphs, start=1):
        if len(item) == 4:
            sid, narrate, actions, until = item
        else:
            sid, narrate, actions = item
            until = None
        lines.append(f"{i}. {narrate}")
        lines.append("")
        step: dict[str, Any] = {
            "id": sid,
            "action_id": sid,
            "narrate": narrate,
            "actions": actions,
        }
        if until:
            step["until"] = until
        steps.append(step)

    script_md = "\n".join(lines).rstrip() + "\n"
    scenario = {
        "id": "mgc-essentiel",
        "title": "Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud",
        "locale": "fr",
        "status": "draft",
        "start_url": process.get("start_url") or "https://www.ma-gestion-cloud.fr/",
        "viewport": {"width": 1920, "height": 1080},
        "voice": {
            "provider": "mistral",
            "model": "voxtral-mini-tts-2603",
            "voice_id": "env:MISTRAL_VOICE_ID",
        },
        "choreography": {
            "action_at": 0.55,
            "min_action_at": 0.40,
            "type_delay_ms": 100,
            "settle_ms": 600,
            "pointer_ms": 450,
        },
        "vars": {},
        "steps": steps,
    }
    return script_md, scenario


def _fallback_trainingmanagement(process: dict[str, Any], objective: str) -> tuple[str, dict[str, Any]]:
    """Brouillon déterministe : créer une formation dans TrainingManagement (Dolibarr)."""
    start = process.get("start_url") or "http://localhost:8000/"
    tm_home = "http://localhost:8000/custom/trainingmanagement/index.php"
    trainings = f"{tm_home}#/trainings"
    training_new = f"{tm_home}#/training/new"

    paragraphs: list[tuple] = [
        (
            "dolibarr.login",
            "Connectez-vous à Dolibarr avec le compte administrateur.",
            [{"action": "login_dolibarr"}],
        ),
        (
            "tm.open",
            "Ouvrons le module TrainingManagement, puis la liste des formations.",
            [{"goto": trainings}],
        ),
        (
            "tm.trainings.list",
            "Voici la liste des formations. Créons une nouvelle fiche.",
            [{"highlight": {"role": "button", "name": "Nouvelle formation"}}],
        ),
        (
            "tm.trainings.new",
            "Cliquez sur Nouvelle formation.",
            [
                {"click": {"role": "button", "name": "Nouvelle formation"}},
                {"goto": training_new},
            ],
        ),
        (
            "tm.form.ref",
            "La référence est préremplie. Conservons-la ou ajustons-la si besoin.",
            [{"highlight": {"css": "#mat-input-0"}}, {"type": {"css": "#mat-input-0", "text": "{{vars.training_ref}}", "human": True}}],
        ),
        (
            "tm.form.title",
            "Saisissez le titre de la formation.",
            [{"type": {"css": "#mat-input-1", "text": "{{vars.training_title}}", "human": True}}],
        ),
        (
            "tm.form.duration",
            "Indiquez la durée en heures.",
            [{"type": {"css": "#mat-input-2", "text": "{{vars.training_duration}}", "human": False}}],
        ),
        (
            "tm.form.create",
            "Enregistrez la fiche en cliquant sur Créer.",
            [{"click": {"role": "button", "name": "Créer"}}],
            "before_create",
        ),
        (
            "tm.trainings.done",
            "La formation est créée : vous revenez sur la liste des formations.",
            [{"goto": trainings}, {"wait": 1500}],
        ),
    ]

    lines = [f"# Script — {objective}", "", "Statut : brouillon — à valider.", ""]
    steps = []
    for i, item in enumerate(paragraphs, start=1):
        if len(item) == 4:
            sid, narrate, actions, until = item
        else:
            sid, narrate, actions = item
            until = None
        lines.append(f"{i}. {narrate}")
        lines.append("")
        step: dict[str, Any] = {
            "id": sid,
            "action_id": sid,
            "narrate": narrate,
            "actions": actions,
        }
        if until:
            step["until"] = until
        steps.append(step)

    script_md = "\n".join(lines).rstrip() + "\n"
    scenario = {
        "id": "TrainingManagement",
        "title": objective,
        "locale": "fr",
        "status": "draft",
        "start_url": start,
        "viewport": {"width": 1920, "height": 1080},
        "voice": {
            "provider": "mistral",
            "model": "voxtral-mini-tts-2603",
            "voice_id": "env:MISTRAL_VOICE_ID",
        },
        "choreography": {
            "action_at": 0.55,
            "min_action_at": 0.40,
            "type_delay_ms": 100,
            "settle_ms": 600,
            "pointer_ms": 450,
        },
        "vars": {
            "training_ref": "FORM-DEMO-TUTO",
            "training_title": "Initiation Excel — démo tuto",
            "training_duration": "7",
            "dolibarr_login": "admin",
        },
        "steps": steps,
    }
    return script_md, scenario


def _parse_llm_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _steps_look_valid(steps: Any) -> bool:
    """Refuse les steps LLM avec id numériques / actions absentes (cassent le TTS)."""
    if not isinstance(steps, list) or len(steps) < 3:
        return False
    for step in steps:
        if not isinstance(step, dict):
            return False
        sid = str(step.get("id") or "").strip()
        if not sid or sid.isdigit() or not re.search(r"[a-zA-Z_]", sid):
            return False
        if not str(step.get("narrate") or "").strip():
            return False
        if not isinstance(step.get("actions"), list):
            return False
    return True


def author(
    scenario_dir: Path,
    settings: Settings,
    objective: str = "Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud",
    use_llm: bool = True,
    *,
    auto_approve: bool = False,
) -> tuple[Path, Path, str]:
    process_path = scenario_dir / "process.json"
    if not process_path.is_file():
        raise SystemExit(
            f"process.json manquant dans {scenario_dir}. Lancez d'abord `tutosvideo discover`."
        )
    process = json.loads(process_path.read_text(encoding="utf-8"))
    from tutosvideo.scenario_prompts import (
        extract_objective_title,
        load_author_prompt,
        save_author_prompt,
    )

    prompt_path = scenario_dir / "prompt.md"
    if objective and objective.strip() and objective != "Créer une instance Dolibarr Essentiel sur Ma Gestion Cloud":
        # Si un objectif court est passé sans fichier à jour, l'injecter en tête du prompt
        existing = load_author_prompt(scenario_dir)
        if "## Objectif" in existing and objective not in existing:
            # conserver le prompt détaillé ; l'objectif court reste le titre
            pass
        elif not prompt_path.is_file():
            save_author_prompt(
                scenario_dir,
                f"# Prompt d'authoring\n\n## Objectif détaillé\n{objective}\n",
            )
    extra = load_author_prompt(scenario_dir)
    title = objective.strip() or extract_objective_title(extra)
    is_tm = scenario_dir.name.lower().replace("-", "") in {
        "trainingmanagement",
        "trainingmanagementapp",
    }

    script_md: str
    scenario: dict[str, Any]
    source = "fallback"

    if use_llm and settings.mistral_api_key:
        try:
            from tutosvideo.mistral_client import mistral_client

            client = mistral_client(settings.mistral_api_key)
            catalog = {
                "plans": process.get("plans"),
                "proposed_path": process.get("proposed_path"),
                "view_aliases": process.get("view_aliases") or [],
                "pages": [
                    {
                        "id": p.get("id"),
                        "url": p.get("url"),
                        "actions": [
                            {
                                "id": a.get("id"),
                                "type": a.get("type"),
                                "name": a.get("name"),
                                "selector_hint": a.get("selector_hint"),
                            }
                            for a in (p.get("actions") or [])[:80]
                        ],
                    }
                    for p in process.get("pages") or []
                ],
            }
            user_msg = (
                f"Titre / objectif court : {title}\n\n"
                f"Règles d'authoring :\n{AUTHORING_RULES}\n\n"
                f"Prompt détaillé du scénario (source de vérité pour le parcours — "
                f"respecte-le, y compris les changements demandés par l'utilisateur) :\n{extra}\n\n"
                f"Brief de découverte (si présent) :\n{process.get('discover_brief') or '(aucun)'}\n\n"
                f"Catalogue process.json (extrait) :\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
                "IMPORTANT : si le prompt demande de présenter toutes les offres, "
                "crée UN paragraphe (une step) par offre listée dans plans[], "
                "puis un paragraphe pour revenir sur Essentiel, puis le CTA.\n"
                "Chaque step.id DOIT être une chaîne stable du type "
                "home.tarifs, plans.essentiel, plans.professionnel, plans.essentiel.cta "
                "(jamais un simple numéro 1, 2, 3).\n\n"
                "Réponds UNIQUEMENT en JSON avec les clés :\n"
                '  "script_md": string (markdown, paragraphes numérotés 1. 2. …),\n'
                '  "steps": [ {"id", "action_id", "narrate", "actions": [...], "until"? } ]\n'
                "Les actions Playwright autorisées : goto, click, type, select, highlight, scroll, wait, wait_for, "
                "ou {\"action\": \"fill_verify_code\"|\"wait_instance\"|\"login_dolibarr\"|\"fill_captcha\"|\"assert_dashboard\"}."
            )
            resp = client.chat.complete(
                model=settings.mistral_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": "Tu es un rédacteur de tutoriels SaaS. Réponds en JSON valide uniquement.",
                    },
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
            )
            content = resp.choices[0].message.content
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part) for part in content
                )
            parsed = _parse_llm_json(str(content))
            if not _steps_look_valid(parsed.get("steps")):
                raise ValueError(
                    "Réponse LLM invalide (ids numériques ou steps incomplets)"
                )
            script_md = str(parsed["script_md"]).rstrip() + "\n"
            scenario = {
                "id": scenario_dir.name,
                "title": title,
                "locale": "fr",
                "status": "draft",
                "start_url": process.get("start_url") or "https://www.ma-gestion-cloud.fr/",
                "viewport": {"width": 1920, "height": 1080},
                "voice": {
                    "provider": "mistral",
                    "model": settings.mistral_tts_model,
                    "voice_id": "env:MISTRAL_VOICE_ID",
                },
                "choreography": {
                    "action_at": 0.55,
                    "min_action_at": 0.40,
                    "type_delay_ms": 100,
                    "settle_ms": 600,
                    "pointer_ms": 450,
                    "scroll_mode": "jump",
                },
                "vars": {},
                "steps": parsed["steps"],
            }
            source = "llm"
        except Exception as exc:  # noqa: BLE001
            print(f"[author] Mistral indisponible ({exc}) — brouillon local de secours.")
            if is_tm:
                script_md, scenario = _fallback_trainingmanagement(process, title)
            else:
                script_md, scenario = _fallback_script_and_yaml(process, title)
            source = f"fallback ({exc})"
    else:
        if is_tm:
            script_md, scenario = _fallback_trainingmanagement(process, title)
        else:
            script_md, scenario = _fallback_script_and_yaml(process, title)
        source = "fallback (LLM désactivé ou clé absente)"

    scenario, script_md, removed_views = _apply_view_dedupe(scenario, script_md)
    if removed_views:
        print(
            "[author] Vues / navigations redondantes fusionnées : "
            + ", ".join(removed_views)
        )
        source = f"{source}+dedupe"

    scenario_dir.mkdir(parents=True, exist_ok=True)
    script_path = scenario_dir / "script.md"
    yaml_path = scenario_dir / "scenario.yaml"
    script_path.write_text(script_md, encoding="utf-8")
    dump_scenario(scenario, yaml_path)
    # Invalider une ancienne validation
    validation = scenario_dir / "validation.json"
    if validation.is_file():
        validation.unlink()

    if auto_approve:
        from tutosvideo.approve import approve

        approve(scenario_dir)
        source = f"{source}+approved"

    return script_path, yaml_path, source
