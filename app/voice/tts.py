"""Text-to-speech using pyttsx3 with a JARVIS-like voice.

Configuration (JARVIS-style):
  - Voice: Daniel (en-GB compact / neural quality) — male British voice
  - Speaking rate: 165 (slow, measured — like JARVIS)
  - Volume: 1.0 (full)

Output is standard WAV PCM (universally playable in browsers).
AIFF intermediate files from pyttsx3 are converted via ffmpeg.

To see available voices, run:
    python3 -c "import pyttsx3; e=pyttsx3.init();
    [print(f'[{i}] {v.id}') for i,v in enumerate(e.getProperty('voices'))]"
"""

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_DIR = Path("app/static/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Lazy-initialised engine singleton
_engine = None

# Preferred JARVIS-like voice — Daniel (en-GB compact, neural quality)
_JARVIS_VOICE = "com.apple.voice.compact.en-GB.Daniel"


def _get_engine():
    """Initialise and configure the pyttsx3 engine (JARVIS settings)."""
    global _engine
    if _engine is not None:
        return _engine

    import pyttsx3

    _engine = pyttsx3.init()

    voices = _engine.getProperty("voices")
    selected = None
    for v in voices:
        if v.id == _JARVIS_VOICE:
            selected = v
            break

    if selected:
        _engine.setProperty("voice", selected.id)
        logger.info("TTS voice: %s (%s)", selected.name, selected.id)
    elif voices:
        _engine.setProperty("voice", voices[0].id)
        logger.warning("JARVIS voice not found, using: %s", voices[0].id)

    _engine.setProperty("rate", 165)    # Slower, measured pace
    _engine.setProperty("volume", 1.0)   # Full volume

    logger.info("pyttsx3 engine ready (voice=%s, rate=%d, volume=%.1f)",
                _engine.getProperty("voice"), _engine.getProperty("rate"),
                _engine.getProperty("volume"))
    return _engine


def text_to_audio(text: str) -> str | None:
    """Generate a WAV PCM audio file for *text*.

    pyttsx3 on macOS outputs AIFF-C compressed audio which most browsers
    cannot play.  We convert it to standard WAV PCM with ffmpeg.

    Returns the relative URL path (e.g. ``/static/audio/abc123.wav``)
    or ``None`` on failure.  Files are cached by MD5 hash of the text
    so repeated calls are instant.
    """
    try:
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        wav_path = AUDIO_DIR / f"{text_hash}.wav"

        if not wav_path.exists():
            aiff_path = AUDIO_DIR / f"{text_hash}.aiff"

            # Step 1 — generate AIFF via pyttsx3
            if not aiff_path.exists():
                engine = _get_engine()
                engine.save_to_file(text, str(aiff_path))
                engine.runAndWait()
                logger.info("AIFF generated: %s (%d bytes)",
                            aiff_path.name, aiff_path.stat().st_size)

            # Step 2 — convert AIFF → WAV PCM with ffmpeg
            logger.info("Converting %s → WAV PCM…", aiff_path.name)
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(aiff_path),
                    "-acodec", "pcm_s16le",   # 16-bit signed little-endian PCM
                    "-ar", "22050",            # 22.05 kHz
                    "-ac", "1",                # mono
                    str(wav_path),
                ],
                capture_output=True,
                timeout=30,
                text=True,
            )

            if result.returncode != 0:
                logger.error("ffmpeg conversion failed:\n%s", result.stderr)
                return None

            wav_size = wav_path.stat().st_size
            logger.info("WAV ready: %s (%d bytes)", wav_path.name, wav_size)

            # Clean up the intermediate AIFF
            aiff_path.unlink(missing_ok=True)
        else:
            logger.debug("TTS cache hit: %s", wav_path.name)

        return f"/static/audio/{text_hash}.wav"

    except Exception as exc:
        logger.warning("TTS failed (%.80s…): %s", text, exc)
        return None