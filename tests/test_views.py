"""Déduplication des vues / navigations redondantes."""

from tutosvideo.views import (
    dedupe_catalog_pages,
    dedupe_scenario_steps,
    dedupe_visit_urls,
    normalize_view_url,
)


def test_normalize_view_url_hash_and_index_php():
    a = "http://localhost:8000/custom/trainingmanagement/index.php#/trainings"
    b = "http://localhost:8000/custom/trainingmanagement/#/trainings"
    assert normalize_view_url(a) == normalize_view_url(b)
    assert "#/trainings" in normalize_view_url(a)


def test_dedupe_visit_urls():
    urls = [
        "http://localhost:8000/custom/tm/index.php#/trainings",
        "http://localhost:8000/custom/tm/#/trainings",
        "http://localhost:8000/custom/tm/index.php#/training/new",
    ]
    unique, skipped = dedupe_visit_urls(urls)
    assert len(unique) == 2
    assert len(skipped) == 1


def test_dedupe_catalog_pages():
    pages = [
        {"id": "a", "url": "http://x/app/index.php#/list", "actions": []},
        {"id": "b", "url": "http://x/app/#/list", "actions": []},
        {"id": "c", "url": "http://x/app/#/new", "actions": []},
    ]
    out, aliases = dedupe_catalog_pages(pages)
    assert [p["id"] for p in out] == ["a", "c"]
    assert len(aliases) == 1
    assert aliases[0]["id"] == "b"
    assert aliases[0]["same_as"] == "a"


def test_dedupe_scenario_steps_menu_then_goto():
    steps = [
        {
            "id": "tm.menu.pedagogique",
            "narrate": "Ouvrez Pédagogique.",
            "actions": [{"click": {"role": "button", "name": "Pédagogique"}}],
        },
        {
            "id": "tm.menu.formations",
            "narrate": "Choisissez Formations.",
            "actions": [
                {"click": {"text": "Formations"}},
                {"goto": "http://localhost:8000/tm/index.php#/trainings"},
            ],
        },
        {
            "id": "tm.list",
            "narrate": "Voici la liste.",
            "actions": [{"highlight": {"role": "button", "name": "Nouvelle"}}],
        },
    ]
    out, removed = dedupe_scenario_steps(steps)
    assert removed == ["tm.menu.pedagogique"]
    assert len(out) == 2
    assert out[0]["id"] == "tm.menu.formations"
    assert out[0]["actions"][0] == {"click": {"role": "button", "name": "Pédagogique"}}
    assert "Pédagogique" in out[0]["narrate"]
    assert "Formations" in out[0]["narrate"]


def test_dedupe_scenario_steps_same_goto():
    trainings = "http://localhost:8000/tm/index.php#/trainings"
    steps = [
        {
            "id": "a",
            "narrate": "Aller à la liste.",
            "actions": [{"goto": trainings}],
        },
        {
            "id": "b",
            "narrate": "Toujours la liste.",
            "actions": [{"goto": "http://localhost:8000/tm/#/trainings"}],
        },
        {
            "id": "c",
            "narrate": "Nouveau.",
            "actions": [{"goto": "http://localhost:8000/tm/#/training/new"}],
        },
    ]
    out, removed = dedupe_scenario_steps(steps)
    assert removed == ["b"]
    assert [s["id"] for s in out] == ["a", "c"]
    assert "Toujours la liste" in out[0]["narrate"]
