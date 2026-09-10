"""Pipeline TTS : génération / localisation / lecture des audios par beat."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tutosvideo.config import OUTPUT_DIR, Settings
from tutosvideo.pacing import plan_beat
from tutosvideo.schema import Scenario
from tutosvideo.subtitles import write_srt
from tutosvideo.tts import audio_duration_seconds, get_tts
from tutosvideo.voices import resolve_scenario_voice_id as resolve_voice

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, Path | None], None]  # step_id, audio_path

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


@dataclass
class AudioBeat:
    step_id: str
    narrate: str
    path: Path | None
    duration_ms: int


def scenario_output_dir(scenario: Scenario) -> Path:
    out = OUTPUT_DIR / scenario.id
    out.mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    return out


def is_audio_file_for_step(filename: str, step_id: str) -> bool:
    """True seulement pour ce step_id exact — pas pour un id enfant.

    Ex. ``plans.essentiel.mp3`` matche ``plans.essentiel``,
    mais ``plans.essentiel.cta.mp3`` ne le matche PAS.
    """
    if filename == step_id:
        return True
    lower = filename.lower()
    sid = step_id.lower()
    for ext in AUDIO_EXTENSIONS:
        if lower == f"{sid}{ext}":
            return True
    return False


def find_audio_file(audio_dir: Path, step_id: str) -> Path | None:
    if not audio_dir.is_dir():
        return None
    candidates = [
        p
        for p in audio_dir.iterdir()
        if p.is_file() and is_audio_file_for_step(p.name, step_id)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def list_audio_beats(scenario: Scenario) -> list[AudioBeat]:
    audio_dir = scenario_output_dir(scenario) / "audio"
    beats: list[AudioBeat] = []
    for step in scenario.steps:
        if not step.narrate.strip():
            continue
        path = find_audio_file(audio_dir, step.id)
        ms = int(round(audio_duration_seconds(path) * 1000)) if path else 0
        beats.append(AudioBeat(step.id, step.narrate, path, ms))
    return beats


def refresh_timeline(scenario: Scenario, *, log: LogFn | None = None) -> dict[str, int]:
    """Re-mesure toutes les durées audio sur disque et réécrit timeline.json + SRT."""
    beats = list_audio_beats(scenario)
    if log:
        for b in beats:
            if b.path:
                log(f"durée {b.step_id} = {b.duration_ms}ms ({b.path.name})")
    _write_timeline(scenario, beats)
    return {b.step_id: b.duration_ms for b in beats if b.duration_ms}


def synthesize_step(
    scenario: Scenario,
    step_id: str,
    settings: Settings,
    *,
    allow_silent: bool = False,
    text: str | None = None,
) -> AudioBeat:
    step = next((s for s in scenario.steps if s.id == step_id), None)
    if step is None:
        raise ValueError(f"Étape inconnue : {step_id}")
    narrate = (text if text is not None else step.narrate).strip()
    if not narrate:
        raise ValueError(f"Pas de texte pour {step_id}")
    audio_dir = scenario_output_dir(scenario) / "audio"
    # supprimer uniquement l'ancienne version de CE step (pas les ids enfants)
    for old in list(audio_dir.iterdir()):
        if old.is_file() and is_audio_file_for_step(old.name, step_id):
            old.unlink(missing_ok=True)
    tts = get_tts(
        settings,
        allow_silent=allow_silent,
        voice_id=resolve_voice(scenario, settings),
    )
    tts.synthesize(narrate, audio_dir / step_id)
    path = find_audio_file(audio_dir, step_id)
    if path is None:
        raise RuntimeError(f"Audio non produit pour {step_id}")
    ms = int(round(audio_duration_seconds(path) * 1000))
    # Recalcule toute la timeline (durées à jour pour chaque beat)
    refresh_timeline(scenario)
    return AudioBeat(step_id, narrate, path, ms)


def synthesize_all(
    scenario: Scenario,
    settings: Settings,
    *,
    allow_silent: bool = False,
    reuse_existing: bool = False,
    on_progress: ProgressFn | None = None,
    log: LogFn | None = None,
) -> list[AudioBeat]:
    beats: list[AudioBeat] = []
    for step in scenario.steps:
        if not step.narrate.strip():
            continue
        audio_dir = scenario_output_dir(scenario) / "audio"
        existing = find_audio_file(audio_dir, step.id) if reuse_existing else None
        if existing is not None:
            beat = AudioBeat(
                step.id,
                step.narrate,
                existing,
                int(round(audio_duration_seconds(existing) * 1000)),
            )
            if log:
                log(f"Réutilise {existing.name} ({beat.duration_ms}ms)")
        else:
            if log:
                log(f"TTS → {step.id}")
            beat = synthesize_step(
                scenario, step.id, settings, allow_silent=allow_silent
            )
            # synthesize_step already refreshed; avoid double-write cost by collecting
            beats.append(beat)
            if on_progress:
                on_progress(step.id, beat.path)
            continue
        beats.append(beat)
        if on_progress:
            on_progress(step.id, beat.path)
    # Une seule écriture finale avec durées re-mesurées
    beats = list_audio_beats(scenario)
    _write_timeline(scenario, beats)
    if log:
        log(f"Timeline recalée ({len(beats)} beats).")
    return beats


def _write_timeline(
    scenario: Scenario,
    beats: list[AudioBeat],
    *,
    actual_slots_ms: dict[str, int] | None = None,
    prep_ms_by_step: dict[str, int] | None = None,
) -> Path:
    out_dir = scenario_output_dir(scenario)
    durations = {b.step_id: b.duration_ms for b in beats}
    cues: list[tuple[int, int, str]] = []
    cursor = 0
    slots: dict[str, int] = {}
    preps: dict[str, int] = {}
    for b in beats:
        step = next((s for s in scenario.steps if s.id == b.step_id), None)
        est = 0
        if step is not None:
            from tutosvideo.pacing import estimate_actions_ms

            est = estimate_actions_ms(step.actions, scenario.choreography.type_delay_ms)
        planned = plan_beat(b.duration_ms, scenario.choreography, est)
        prep = int((prep_ms_by_step or {}).get(b.step_id, 0) or 0)
        preps[b.step_id] = prep
        slot = (actual_slots_ms or {}).get(b.step_id) or (planned.total_ms + prep)
        # Slot ≥ prep + audio (+ marge) pour ne jamais couper la voix
        slot = max(int(slot), prep + b.duration_ms + 300 + int(scenario.choreography.settle_ms))
        slots[b.step_id] = slot
        # Sous-titres alignés sur le début de narration (après silence de tête)
        cue_start = cursor + prep
        cues.append((cue_start, cue_start + b.duration_ms, b.narrate))
        cursor += slot
    write_srt(cues, out_dir / "subtitles.srt")
    path = out_dir / "timeline.json"
    path.write_text(
        json.dumps(
            {
                "durations_ms": durations,
                "slots_ms": slots,
                "prep_ms": preps,
                "total_ms": cursor,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def apply_capture_slots(
    scenario: Scenario,
    timeline_log: list[dict],
) -> dict[str, int]:
    """Après capture : réécrit la timeline avec les durées réelles des beats vidéo."""
    actual = {
        str(row["step_id"]): int(row.get("actual_ms") or row.get("total_planned_ms") or 0)
        for row in timeline_log
        if row.get("step_id")
    }
    preps = {
        str(row["step_id"]): int(row.get("prep_ms") or 0)
        for row in timeline_log
        if row.get("step_id")
    }
    beats = list_audio_beats(scenario)
    _write_timeline(scenario, beats, actual_slots_ms=actual, prep_ms_by_step=preps)
    out = scenario_output_dir(scenario) / "capture_timeline.json"
    out.write_text(json.dumps(timeline_log, indent=2) + "\n", encoding="utf-8")
    result: dict[str, int] = {}
    for b in beats:
        prep = preps.get(b.step_id, 0)
        result[b.step_id] = max(
            actual.get(b.step_id, b.duration_ms),
            prep + b.duration_ms + 300,
        )
    return result


def load_durations_ms(scenario: Scenario) -> dict[str, int]:
    """Charge les durées ; si absentes/périmées, re-mesure depuis les fichiers."""
    refreshed = refresh_timeline(scenario)
    if refreshed:
        return refreshed
    path = scenario_output_dir(scenario) / "timeline.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in (data.get("durations_ms") or {}).items()}
    return {}


def audio_segments_for_mux(
    scenario: Scenario,
    slots_ms: dict[str, int] | None = None,
    *,
    only_step_ids: list[str] | None = None,
    prep_ms: dict[str, int] | None = None,
) -> list[tuple[Path, int, int]]:
    """Liste (fichier, slot_ms, lead_ms) dans l'ordre du scénario pour le mux paddé."""
    path = scenario_output_dir(scenario) / "timeline.json"
    file_preps: dict[str, int] = {}
    if slots_ms is None or prep_ms is None:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if slots_ms is None:
                slots_ms = {str(k): int(v) for k, v in (data.get("slots_ms") or {}).items()}
            file_preps = {str(k): int(v) for k, v in (data.get("prep_ms") or {}).items()}
        else:
            slots_ms = slots_ms or {}
    leads = prep_ms if prep_ms is not None else file_preps
    allowed = set(only_step_ids) if only_step_ids is not None else None
    segments: list[tuple[Path, int, int]] = []
    for b in list_audio_beats(scenario):
        if allowed is not None and b.step_id not in allowed:
            continue
        if b.path is None:
            raise FileNotFoundError(
                f"Audio manquant pour {b.step_id} — générez les voix d'abord."
            )
        lead = int(leads.get(b.step_id, 0) or 0)
        slot = int(slots_ms.get(b.step_id, b.duration_ms) or b.duration_ms)
        slot = max(slot, lead + b.duration_ms)
        segments.append((b.path, slot, lead))
    return segments


def audio_files_in_order(scenario: Scenario) -> list[Path]:
    files: list[Path] = []
    for b in list_audio_beats(scenario):
        if b.path is None:
            raise FileNotFoundError(f"Audio manquant pour {b.step_id} — générez les voix d'abord.")
        files.append(b.path)
    return files


def play_audio(path: Path) -> subprocess.Popen | None:
    """Lecture non bloquante (ffplay si dispo, sinon xdg-open)."""
    if not path.is_file():
        raise FileNotFoundError(path)
    ffplay = shutil.which("ffplay")
    if ffplay:
        return subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener:
        return subprocess.Popen(
            [opener, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    raise RuntimeError("Aucun lecteur audio (ffplay / xdg-open) disponible.")
