"""Catalogue voix Mistral."""

from tutosvideo.voices import VoiceInfo, filter_voices, languages_from_voices, moods_for_language


def _sample() -> list[VoiceInfo]:
    return [
        VoiceInfo("1", "fr_marie_neutral", "Marie - Neutral", ("fr_fr",), "female", "neutral", ("neutral",)),
        VoiceInfo("2", "fr_marie_happy", "Marie - Happy", ("fr_fr",), "female", "happy", ("happy",)),
        VoiceInfo("3", "en_paul_sad", "Paul - Sad", ("en_us",), "male", "sad", ("sad",)),
    ]


def test_filter_and_moods():
    voices = _sample()
    assert languages_from_voices(voices) == ["en_us", "fr_fr"]
    assert moods_for_language(voices, "fr_fr") == ["happy", "neutral"]
    fr = filter_voices(voices, language="fr_fr", mood="happy")
    assert len(fr) == 1
    assert fr[0].slug == "fr_marie_happy"
