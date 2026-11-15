from __future__ import annotations

from pathlib import Path

from research_data.http import LoaderError
from research_data.podcasts import EpisodeUnavailable, Transcript

try:
    from av.error import FFmpegError
    from faster_whisper import WhisperModel
except ImportError:
    # Both come from the optional `podcasts` extra; LocalWhisper reports how to install it.
    WhisperModel = None

MODEL = "Systran/faster-whisper-small.en"
# Pinned so a later upload to the model repository can neither change transcripts nor run unreviewed.
REVISION = "d1d751a5f8271d482d14ca55d9e2deeebbae577f"


def whisper_installed() -> bool:
    return WhisperModel is not None


class LocalWhisper:
    """Transcribes English speech on this machine's CPU. The first use downloads the model (about 460 MB)."""

    def __init__(self) -> None:
        if WhisperModel is None:
            raise LoaderError("Transcribing podcast audio needs faster-whisper: run `poetry install --extras podcasts`")
        try:
            self._model = WhisperModel(MODEL, device="cpu", compute_type="int8", revision=REVISION)
        except OSError as exc:
            raise LoaderError(f"Cannot load {MODEL}: {exc}") from None

    def __call__(self, audio: Path) -> Transcript:
        try:
            # The filter skips stretches without speech, where Whisper can invent words.
            segments, _ = self._model.transcribe(str(audio), language="en", vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments)
        except FFmpegError as exc:
            raise EpisodeUnavailable(f"its audio could not be decoded: {exc.strerror}") from None
        return Transcript(text, MODEL)
