"""Catalogue des voix Mistral TTS (langues / humeurs) + résolution scénario."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from tutosvideo.config import Settings
from tutosvideo.schema import Scenario

VOICES_URL = "https://api.mistral.ai/v1/audio/voices"


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    slug: str
    name: str
    languages: tuple[str, ...]
    gender: str
    mood: str
    tags: tuple[str, ...]

    @property
    def voice_id(self) -> str:
        """Identifiant préféré pour l'API speech (slug si dispo)."""
        return self.slug or self.id

    @property
    def label(self) -> str:
        langs = ", ".join(self.languages) if self.languages else "?"
        mood = self.mood or "—"
        return f"{self.name}  [{langs} · {mood}]"


def _mood_from_item(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    if " - " in name:
        return name.split(" - ", 1)[1].strip().lower()
    slug = str(item.get("slug") or "")
    if "_" in slug:
        return slug.rsplit("_", 1)[-1].strip().lower()
    tags = [str(t).lower() for t in (item.get("tags") or [])]
    for preferred in (
        "neutral",
        "happy",
        "sad",
        "angry",
        "excited",
        "curious",
        "calm",
        "confident",
    ):
        if preferred in tags:
            return preferred
    return tags[0] if tags else ""


def list_mistral_voices(settings: Settings, *, limit: int = 200) -> list[VoiceInfo]:
    """GET /v1/audio/voices — catalogue (langues + tags/humeurs)."""
    if not settings.mistral_api_key:
        raise SystemExit("MISTRAL_API_KEY manquante pour lister les voix.")
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(
                VOICES_URL,
                params={"limit": min(100, limit), "offset": offset},
                headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
            )
            r.raise_for_status()
            data = r.json()
        batch = list(data.get("items") or [])
        items.extend(batch)
        total = int(data.get("total") or len(items))
        offset += len(batch)
        if not batch or offset >= total or len(items) >= limit:
            break

    out: list[VoiceInfo] = []
    for it in items:
        langs = tuple(str(x) for x in (it.get("languages") or []))
        tags = tuple(str(t) for t in (it.get("tags") or []))
        out.append(
            VoiceInfo(
                id=str(it.get("id") or ""),
                slug=str(it.get("slug") or ""),
                name=str(it.get("name") or it.get("slug") or it.get("id") or "voix"),
                languages=langs,
                gender=str(it.get("gender") or ""),
                mood=_mood_from_item(it),
                tags=tags,
            )
        )
    return out


def languages_from_voices(voices: list[VoiceInfo]) -> list[str]:
    langs: set[str] = set()
    for v in voices:
        langs.update(v.languages)
    return sorted(langs)


def moods_for_language(voices: list[VoiceInfo], language: str) -> list[str]:
    return sorted({v.mood for v in voices if language in v.languages and v.mood})


def filter_voices(
    voices: list[VoiceInfo],
    *,
    language: str | None = None,
    mood: str | None = None,
) -> list[VoiceInfo]:
    out = voices
    if language:
        out = [v for v in out if language in v.languages]
    if mood:
        m = mood.lower().strip()
        out = [v for v in out if v.mood == m]
    return sorted(out, key=lambda v: (v.name.lower(), v.slug))


def resolve_scenario_voice_id(scenario: Scenario, settings: Settings) -> str:
    """Voix du scénario si définie, sinon MISTRAL_VOICE_ID (config)."""
    raw = str((scenario.voice or {}).get("voice_id") or "").strip()
    if not raw or raw.startswith("env:"):
        return (settings.mistral_voice_id or "").strip()
    return raw


def voice_selection_dict(
    *,
    voice_id: str,
    language: str = "",
    mood: str = "",
    name: str = "",
    model: str = "voxtral-mini-tts-2603",
) -> dict[str, Any]:
    return {
        "provider": "mistral",
        "model": model,
        "voice_id": voice_id,
        "language": language,
        "mood": mood,
        "name": name,
    }


def update_scenario_voice_yaml(scenario_yaml: Path, voice: dict[str, Any]) -> None:
    data = yaml.safe_load(scenario_yaml.read_text(encoding="utf-8")) or {}
    current = dict(data.get("voice") or {})
    current.update({k: v for k, v in voice.items() if v is not None and str(v) != ""})
    data["voice"] = current
    scenario_yaml.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
