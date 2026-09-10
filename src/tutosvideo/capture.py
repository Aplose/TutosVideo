"""Backends de capture vidéo CLI (Playwright record_video | ffmpeg x11grab)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from tutosvideo.config import OUTPUT_DIR, ROOT

BackendName = Literal["playwright", "ffmpeg"]


def _state_file() -> Path:
    return OUTPUT_DIR / "capture.state.json"


@dataclass
class CaptureState:
    backend: BackendName
    output: str
    size: str
    started_at: float
    pid: int | None = None
    video_dir: str | None = None


def _write_state(state: CaptureState) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")


def _read_state() -> CaptureState | None:
    path = _state_file()
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CaptureState(**raw)


def _clear_state() -> None:
    path = _state_file()
    if path.is_file():
        path.unlink()


def parse_size(size: str) -> tuple[int, int]:
    w, h = size.lower().split("x", 1)
    return int(w), int(h)


class PlaywrightCaptureSession:
    """Session liée à un BrowserContext Playwright (record_video)."""

    def __init__(self, output: Path, width: int, height: int) -> None:
        self.output = output
        self.width = width
        self.height = height
        self.video_dir = output.parent / f".pw-video-{output.stem}"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self._page_video_path: Path | None = None

    def context_kwargs(self) -> dict:
        return {
            "record_video_dir": str(self.video_dir),
            "record_video_size": {"width": self.width, "height": self.height},
            "viewport": {"width": self.width, "height": self.height},
        }

    def bind_page(self, page) -> None:  # noqa: ANN001
        self._page = page

    def finalize(self) -> Path:
        page = getattr(self, "_page", None)
        if page is not None:
            video = page.video
            if video is not None:
                # Close page/context outside; caller closes first then we save.
                pass
        return self.output

    def save_after_close(self) -> Path:
        """Après fermeture du context, déplace le webm vers output."""
        candidates = sorted(self.video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise RuntimeError(f"Aucune vidéo Playwright dans {self.video_dir}")
        src = candidates[-1]
        self.output.parent.mkdir(parents=True, exist_ok=True)
        if self.output.exists():
            self.output.unlink()
        src.replace(self.output)
        # cleanup leftovers
        for leftover in self.video_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        try:
            self.video_dir.rmdir()
        except OSError:
            pass
        return self.output


def capture_start(
    backend: BackendName,
    output: Path,
    size: str = "1920x1080",
    display: str | None = None,
) -> CaptureState:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = output if output.is_absolute() else (ROOT / output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if backend == "playwright":
        state = CaptureState(
            backend="playwright",
            output=str(output),
            size=size,
            started_at=time.time(),
            video_dir=str(output.parent / f".pw-video-{output.stem}"),
        )
        Path(state.video_dir).mkdir(parents=True, exist_ok=True)
        _write_state(state)
        return state

    if backend == "ffmpeg":
        width, height = parse_size(size)
        disp = display or os.environ.get("DISPLAY", ":0")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "x11grab",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            "25",
            "-i",
            disp,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        state = CaptureState(
            backend="ffmpeg",
            output=str(output),
            size=size,
            started_at=time.time(),
            pid=proc.pid,
        )
        _write_state(state)
        return state

    raise SystemExit(f"Backend de capture inconnu : {backend}")


def capture_stop() -> Path:
    state = _read_state()
    if state is None:
        raise SystemExit("Aucune capture en cours (capture.state.json absent).")

    out = Path(state.output)
    if state.backend == "ffmpeg" and state.pid:
        try:
            os.kill(state.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        # attendre la fin
        for _ in range(50):
            try:
                os.kill(state.pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        _clear_state()
        if not out.is_file():
            raise SystemExit(f"Capture ffmpeg terminée mais fichier absent : {out}")
        return out

    if state.backend == "playwright":
        # Le runner finalise le fichier ; ici on signale juste l'arrêt.
        _clear_state()
        return out

    _clear_state()
    return out


def capture_status() -> CaptureState | None:
    return _read_state()
