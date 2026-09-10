"""TTS Mistral Voxtral (+ interface provider)."""

from __future__ import annotations

import base64
import wave
from pathlib import Path
from typing import Protocol

import httpx

from tutosvideo.config import Settings


class TtsProvider(Protocol):
    def synthesize(self, text: str, out_path: Path) -> float:
        """Génère un fichier audio, retourne la durée en secondes."""


def audio_duration_seconds(path: Path) -> float:
    """Durée réelle du fichier (ffprobe en priorité, puis wav/mutagen)."""
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if ffprobe and path.is_file():
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                text = (proc.stdout or "").strip()
                if text:
                    return max(0.0, float(text))
        except Exception:
            pass

    suffix = path.suffix.lower()
    if suffix == ".wav":
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate else 0.0
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path))
        if audio is not None and getattr(audio, "info", None) and audio.info.length:
            return float(audio.info.length)
    except Exception:
        pass
    # fallback crude: file size
    return max(1.0, path.stat().st_size / 16000)


def with_audio_ext(path: Path, ext: str) -> Path:
    """Ajoute une extension sans écraser un id du type home.intro."""
    if not ext.startswith("."):
        ext = f".{ext}"
    if path.suffix.lower() == ext:
        return path
    return path.parent / f"{path.name}{ext}"


class MistralTts:
    def __init__(self, settings: Settings, *, voice_id: str | None = None) -> None:
        self.settings = settings
        self.voice_id = (voice_id or settings.mistral_voice_id or "").strip()

    def synthesize(self, text: str, out_path: Path) -> float:
        self.settings.require_mistral()
        if not self.voice_id:
            raise SystemExit(
                "Aucune voix TTS : choisissez-en une dans le scénario, "
                "ou définissez MISTRAL_VOICE_ID dans la configuration."
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from tutosvideo.mistral_client import mistral_client

            client = mistral_client(self.settings.mistral_api_key)
            response = client.audio.speech.complete(
                model=self.settings.mistral_tts_model,
                input=text,
                voice_id=self.voice_id,
                response_format="mp3",
            )
            audio_b64 = getattr(response, "audio_data", None)
            if audio_b64 is None and isinstance(response, dict):
                audio_b64 = response.get("audio_data")
            if not audio_b64:
                raise RuntimeError("Réponse TTS sans audio_data")
            raw = base64.b64decode(audio_b64)
            mp3_path = with_audio_ext(out_path, ".mp3")
            mp3_path.write_bytes(raw)
            return audio_duration_seconds(mp3_path)
        except Exception:
            return self._http_synthesize(text, out_path)

    def _http_synthesize(self, text: str, out_path: Path) -> float:
        url = "https://api.mistral.ai/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.settings.mistral_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.mistral_tts_model,
            "input": text,
            "voice_id": self.voice_id,
            "response_format": "mp3",
        }
        with httpx.Client(timeout=120.0) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "application/json" in ctype:
                data = r.json()
                raw = base64.b64decode(data["audio_data"])
            else:
                raw = r.content
        mp3_path = with_audio_ext(out_path, ".mp3")
        mp3_path.write_bytes(raw)
        return audio_duration_seconds(mp3_path)


class SilentTts:
    """Génère un silence WAV de durée estimée (tests / preview hors clé)."""

    def synthesize(self, text: str, out_path: Path) -> float:
        duration = max(2.0, len(text) / 14.0)
        wav_path = with_audio_ext(out_path, ".wav")
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        rate = 24000
        n_frames = int(duration * rate)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"\x00\x00" * n_frames)
        return duration


def get_tts(
    settings: Settings,
    allow_silent: bool = False,
    *,
    voice_id: str | None = None,
) -> TtsProvider:
    vid = (voice_id or settings.mistral_voice_id or "").strip()
    if settings.mistral_api_key and vid:
        return MistralTts(settings, voice_id=vid)
    if allow_silent:
        return SilentTts()
    settings.require_mistral()
    if not vid:
        raise SystemExit(
            "TTS : choisissez une voix dans le scénario ou MISTRAL_VOICE_ID en config."
        )
    raise SystemExit("TTS non configuré (MISTRAL_API_KEY).")
