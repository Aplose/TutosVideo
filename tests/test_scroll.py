"""Tests du scroll progressif."""

from pathlib import Path

import yaml

from tutosvideo.browser import is_comfortably_visible


def test_comfortably_visible_center():
    box = {
        "top": 300,
        "bottom": 340,
        "left": 100,
        "right": 400,
        "height": 40,
        "width": 300,
        "vh": 1080,
        "vw": 1920,
    }
    assert is_comfortably_visible(box)


def test_not_visible_below_fold():
    box = {
        "top": 1200,
        "bottom": 1240,
        "left": 100,
        "right": 400,
        "height": 40,
        "width": 300,
        "vh": 1080,
        "vw": 1920,
    }
    assert not is_comfortably_visible(box)


def test_not_visible_above():
    box = {
        "top": -80,
        "bottom": -40,
        "left": 100,
        "right": 400,
        "height": 40,
        "width": 300,
        "vh": 1080,
        "vw": 1920,
    }
    assert not is_comfortably_visible(box)


def test_scenario_has_no_plans_others_step():
    raw = Path("scenarios/mgc-essentiel/scenario.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    ids = [str(s["id"]) for s in data["steps"]]
    assert "plans.others" not in ids
    # Au moins une step liée aux offres / Essentiel
    assert any("essentiel" in i.lower() or i.startswith("plans.") for i in ids)
