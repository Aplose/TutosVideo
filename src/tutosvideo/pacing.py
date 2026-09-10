"""Moteur de rythme voix-d'abord : pointer → hold → action → settle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tutosvideo.schema import Choreography


@dataclass(frozen=True)
class BeatTimeline:
    """Durées en millisecondes pour un beat."""

    audio_ms: int
    pointer_ms: int
    hold_before_action_ms: int
    action_at_ms: int
    settle_ms: int
    total_ms: int


def plan_beat(
    audio_ms: int,
    choreography: Choreography,
    estimated_action_ms: int = 0,
) -> BeatTimeline:
    """Calcule la timeline d'un beat à partir de la durée audio réelle.

    - Pointer au début.
    - L'action ne démarre qu'après max(min_action_at, action_at) de l'audio.
    - Si l'action finit avant la fin audio → freeze jusqu'à fin audio + settle.
    - Si l'action déborde → on laisse finir, puis settle (on ne coupe pas l'audio).
    """
    audio_ms = max(0, int(audio_ms))
    pointer_ms = max(0, int(choreography.pointer_ms))
    settle_ms = max(0, int(choreography.settle_ms))

    action_frac = max(choreography.min_action_at, min(0.95, choreography.action_at))
    action_at_ms = int(audio_ms * action_frac) if audio_ms else 0
    # Le hold inclut le pointer : on pointe pendant le début de la phrase.
    hold_before_action_ms = max(0, action_at_ms - pointer_ms)

    content_end = max(audio_ms, action_at_ms + max(0, estimated_action_ms))
    total_ms = content_end + settle_ms

    return BeatTimeline(
        audio_ms=audio_ms,
        pointer_ms=pointer_ms,
        hold_before_action_ms=hold_before_action_ms,
        action_at_ms=action_at_ms,
        settle_ms=settle_ms,
        total_ms=total_ms,
    )


def estimate_type_ms(text: str, delay_ms: int) -> int:
    return max(0, len(text)) * max(0, delay_ms)


def estimate_actions_ms(actions: list[dict[str, Any]], type_delay_ms: int) -> int:
    total = 0
    for action in actions:
        if "type" in action:
            spec = action["type"]
            text = str(spec.get("text") or "")
            total += estimate_type_ms(text, type_delay_ms) + 200
        elif "click" in action:
            total += 800
        elif "scroll" in action or "highlight" in action:
            total += 600
        elif "wait" in action:
            total += int(action["wait"])
        elif "goto" in action:
            total += 2000
        else:
            total += 1000
    return total


def ms_to_srt_ts(ms: int) -> str:
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
