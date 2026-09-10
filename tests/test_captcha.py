"""Tests lecture / normalisation captcha."""

from __future__ import annotations

from tutosvideo.captcha import normalize_captcha_text


def test_normalize_plain():
    assert normalize_captcha_text("Ab3K") == "Ab3K"
    assert normalize_captcha_text("  xY9z  ") == "xY9z"


def test_normalize_strips_noise():
    assert normalize_captcha_text("A-b3K") == "Ab3K"
    assert normalize_captcha_text("```\nQw8P\n```") == "Qw8P"


def test_normalize_jsonish():
    assert normalize_captcha_text('{"code": "Z9mQ"}') == "Z9mQ"
    assert normalize_captcha_text('{"captcha": "hello"}') == "hello"


def test_normalize_empty():
    assert normalize_captcha_text("") == ""
    assert normalize_captcha_text("   ") == ""
