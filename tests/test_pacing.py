"""Tests unitaires du moteur de rythme."""

from tutosvideo.pacing import estimate_type_ms, ms_to_srt_ts, plan_beat
from tutosvideo.schema import Choreography


def test_plan_beat_action_with_voice_start():
    """Par défaut l'action part avec la voix (action_at=0)."""
    choreo = Choreography(action_at=0.0, min_action_at=0.0, pointer_ms=280, settle_ms=350)
    tl = plan_beat(10_000, choreo, estimated_action_ms=1000)
    assert tl.action_at_ms == 0
    assert tl.hold_before_action_ms == 0
    assert tl.total_ms == 10_000 + 350


def test_plan_beat_legacy_fraction():
    choreo = Choreography(action_at=0.55, min_action_at=0.40, pointer_ms=450, settle_ms=600)
    tl = plan_beat(10_000, choreo, estimated_action_ms=1000)
    assert tl.action_at_ms == 5500
    assert tl.pointer_ms == 450
    assert tl.hold_before_action_ms == 5050
    assert tl.total_ms == 10_000 + 600


def test_plan_beat_action_overrun_extends_total():
    choreo = Choreography(action_at=0.0, settle_ms=350, pointer_ms=0)
    tl = plan_beat(5_000, choreo, estimated_action_ms=10_000)
    assert tl.total_ms == 10_000 + 350


def test_srt_ts():
    assert ms_to_srt_ts(3661_234) == "01:01:01,234"


def test_estimate_type():
    assert estimate_type_ms("abc", 100) == 300
