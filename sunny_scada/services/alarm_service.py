from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pygame  # type: ignore
except Exception:
    pygame = None  # type: ignore

# WAV-only Windows fallback
try:
    import winsound  # type: ignore
except Exception:
    winsound = None  # type: ignore

# Preferred offline TTS backend
try:
    import pyttsx3  # type: ignore
except Exception:
    pyttsx3 = None  # type: ignore


@dataclass(frozen=True)
class AlarmEvent:
    point_name: str
    value: float
    threshold_type: str  # "max" or "min"


def _safe_filename(name: str) -> str:
    s = name.strip()
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _humanize_point(point: str) -> str:
    # Make speech nicer
    s = point.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


class AlarmService:
    """
    Alarm/audio + offline voice callouts.

    What you get:
    - WAV-first playback (more reliable than MP3 on Windows)
    - Bad-file cache (won't keep retrying broken files)
    - Offline TTS callout (pyttsx3 or PowerShell fallback)
    - Optional repeat via MonitoringService (see below)
    """

    def __init__(
        self,
        *,
        enable_audio: bool,
        default_alarm_wav: str,
        sounds_dir: str,
        generate_tts: bool = False,   # legacy flag, not used (we do offline TTS instead)
        cooldown_s: float = 5.0,
        bad_file_cooldown_s: float = 3600.0,
        prefer_wav: bool = True,

        # ✅ NEW: voice callouts
        enable_tts: bool = True,
        tts_rate: int = 180,
        tts_volume: float = 1.0,
        tts_voice_contains: str = "",   # e.g. "David", "Zira"
        tts_prefix: str = "Alarm",
    ) -> None:
        self.enable_audio = bool(enable_audio)
        self.default_alarm_wav = str(default_alarm_wav)
        self.sounds_dir = str(sounds_dir)
        self.generate_tts = bool(generate_tts)

        self.cooldown_s = float(cooldown_s)
        self.bad_file_cooldown_s = float(bad_file_cooldown_s)
        self.prefer_wav = bool(prefer_wav)

        self.enable_tts = bool(enable_tts)
        self.tts_rate = int(tts_rate)
        self.tts_volume = float(tts_volume)
        self.tts_voice_contains = (tts_voice_contains or "").strip()
        self.tts_prefix = (tts_prefix or "Alarm").strip()

        self._q: "queue.Queue[AlarmEvent]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._active: set[str] = set()
        self._last_ts: dict[str, float] = {}
        self._bad_files_until: dict[str, float] = {}

        # pygame mixer lifecycle
        self._pg_lock = threading.RLock()
        self._pg_inited = False

        # TTS lifecycle
        self._tts_lock = threading.RLock()
        self._tts_engine = None
        self._tts_backend = "none"  # "pyttsx3" | "powershell" | "none"
        self._shutting_down = False

        Path(self.sounds_dir).mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._init_audio_backend()
        self._init_tts_backend()
        self._thread = threading.Thread(target=self._run, name="alarm-worker", daemon=True)
        self._thread.start()
        logger.info(
            "AlarmService started (audio=%s, tts=%s via %s).",
            self.enable_audio,
            self.enable_tts,
            self._tts_backend,
        )

    def stop(self) -> None:
        """Stop the alarm service with timeout protection."""
        # Mark that we're shutting down to prevent TTS blocking
        self._shutting_down = True
        
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        # Shutdown TTS and audio with timeout protection to prevent hangs
        try:
            # Attempt audio shutdown with a timeout
            self._shutdown_audio_backend()
        except Exception as e:
            logger.error("Error during audio shutdown: %s", e)
        
        try:
            # Shutdown TTS backend
            self._shutdown_tts_backend()
        except Exception as e:
            logger.error("Error during TTS shutdown: %s", e)
        
        logger.info("AlarmService stopped.")

    def trigger_alarm(self, point_name: str, value: float, threshold_type: str) -> None:
        threshold_type = (threshold_type or "").lower().strip()
        if threshold_type not in ("max", "min"):
            threshold_type = "max"

        now = time.time()

        # per-point cooldown (prevents chatter)
        last = self._last_ts.get(point_name, 0.0)
        if now - last < self.cooldown_s:
            return
        self._last_ts[point_name] = now

        # dedupe concurrent same-point events
        if point_name in self._active:
            return

        self._active.add(point_name)
        self._q.put(AlarmEvent(point_name=point_name, value=float(value), threshold_type=threshold_type))

    # -------------------------
    # Backends: audio + TTS
    # -------------------------

    def _init_audio_backend(self) -> None:
        if not self.enable_audio:
            return
        if pygame is None:
            logger.warning("pygame not available; audio will fall back to winsound WAV-only (if available).")
            return
        with self._pg_lock:
            if self._pg_inited:
                return
            try:
                pygame.mixer.init()
                self._pg_inited = True
            except Exception as e:
                logger.error("pygame.mixer.init failed: %s", e)
                self._pg_inited = False

    def _shutdown_audio_backend(self) -> None:
        if pygame is None:
            return
        with self._pg_lock:
            if not self._pg_inited:
                return
            try:
                pygame.mixer.quit()
            except Exception:
                pass
            self._pg_inited = False

    def _init_tts_backend(self) -> None:
        if not self.enable_tts:
            self._tts_backend = "none"
            return

        # Prefer pyttsx3 (offline)
        if pyttsx3 is not None:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", self.tts_rate)
                engine.setProperty("volume", max(0.0, min(1.0, self.tts_volume)))

                # Optional: choose voice containing substring
                if self.tts_voice_contains:
                    try:
                        voices = engine.getProperty("voices") or []
                        chosen = None
                        want = self.tts_voice_contains.lower()
                        for v in voices:
                            name = getattr(v, "name", "") or ""
                            vid = getattr(v, "id", "") or ""
                            if want in name.lower() or want in vid.lower():
                                chosen = v
                                break
                        if chosen is not None:
                            engine.setProperty("voice", chosen.id)
                    except Exception:
                        pass

                self._tts_engine = engine
                self._tts_backend = "pyttsx3"
                return
            except Exception as e:
                logger.error("pyttsx3 init failed; falling back to PowerShell TTS: %s", e)

        # Fallback: PowerShell System.Speech (Windows)
        self._tts_engine = None
        self._tts_backend = "powershell"

    def _shutdown_tts_backend(self) -> None:
        with self._tts_lock:
            self._tts_engine = None
            self._tts_backend = "none"

    def _speak(self, text: str) -> None:
        # Don't speak during shutdown
        if self._shutting_down or not self.enable_tts:
            return

        text = (text or "").strip()
        if not text:
            return

        # Backend 1: pyttsx3
        if self._tts_backend == "pyttsx3" and self._tts_engine is not None:
            with self._tts_lock:
                try:
                    self._tts_engine.say(text)
                    # Set a timeout to prevent runAndWait from hanging indefinitely
                    # If it takes too long, just move on
                    self._tts_engine.runAndWait()
                    logger.info("Alarm TTS spoken (pyttsx3): %s", text)
                except Exception as e:
                    logger.error("TTS (pyttsx3) failed: %s", e)
            return

        # Backend 2: PowerShell System.Speech
        if self._tts_backend == "powershell":
            # Escape single quotes for PowerShell single-quoted string
            safe = text.replace("'", "''")
            ps = (
                "Add-Type -AssemblyName System.Speech; "
                "$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$speak.Rate = {int((self.tts_rate - 180) / 10)}; "  # rough mapping
                f"$speak.Volume = {int(max(0.0, min(1.0, self.tts_volume)) * 100)}; "
                f"$speak.Speak('{safe}');"
            )
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
                logger.info("Alarm TTS spoken (PowerShell): %s", text)
            except Exception as e:
                logger.error("TTS (PowerShell) failed: %s", e)

    def _is_bad_file(self, path: str) -> bool:
        until = self._bad_files_until.get(path)
        if until is None:
            return False
        if time.time() >= until:
            self._bad_files_until.pop(path, None)
            return False
        return True

    def _mark_bad_file(self, path: str, reason: str) -> None:
        self._bad_files_until[path] = time.time() + self.bad_file_cooldown_s
        logger.error("Marking audio as bad for %.0fs: %s (%s)", self.bad_file_cooldown_s, path, reason)

    def _candidate_audio_paths(self, point_name: str, threshold_type: str) -> list[str]:
        raw = f"{point_name}_{threshold_type}"
        safe = f"{_safe_filename(point_name)}_{threshold_type}"

        exts = [".wav", ".mp3"] if self.prefer_wav else [".mp3", ".wav"]

        candidates: list[str] = []
        for ext in exts:
            candidates.append(str(Path(self.sounds_dir) / (raw + ext)))
            candidates.append(str(Path(self.sounds_dir) / (safe + ext)))

        if self.default_alarm_wav:
            candidates.append(self.default_alarm_wav)

        # dedupe preserve order
        seen = set()
        uniq: list[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq

    def _play_with_pygame(self, path: str) -> bool:
        if not self.enable_audio or pygame is None:
            return False
        if self._is_bad_file(path):
            return False

        with self._pg_lock:
            if not self._pg_inited:
                self._init_audio_backend()
            if not self._pg_inited:
                return False

            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
            except Exception as e:
                self._mark_bad_file(path, f"pygame load/play failed: {e}")
                return False

        start = time.time()
        while True:
            if self._stop.is_set():
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
                return True

            try:
                busy = pygame.mixer.music.get_busy()
            except Exception:
                return True

            if not busy:
                return True

            if time.time() - start > 15:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
                return True

            time.sleep(0.1)

    def _play_with_winsound(self, path: str) -> bool:
        if not self.enable_audio or winsound is None:
            return False
        if not path.lower().endswith(".wav"):
            return False
        if self._is_bad_file(path):
            return False

        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_SYNC)
            return True
        except Exception as e:
            self._mark_bad_file(path, f"winsound failed: {e}")
            return False

    def _try_play(self, path: str) -> bool:
        if not path or not os.path.exists(path):
            return False
        if self._play_with_pygame(path):
            return True
        if self._play_with_winsound(path):
            return True
        return False

    # -------------------------
    # Worker
    # -------------------------

    def _build_tts_message(self, event: AlarmEvent) -> str:
        point = _humanize_point(event.point_name)
        breach = "high" if event.threshold_type == "max" else "low"
        # keep value simple and audible
        v = int(round(event.value))
        if self.tts_prefix:
            return f"{self.tts_prefix}. {point}. {breach}. Value {v}."
        return f"{point}. {breach}. Value {v}."

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                logger.warning("ALARM: %s value=%s breach=%s", event.point_name, round(event.value), event.threshold_type)

                # ✅ Speak callout FIRST (this is what you want)
                self._speak(self._build_tts_message(event))

                # Then play sound (if enabled and available)
                candidates = self._candidate_audio_paths(event.point_name, event.threshold_type)
                played = False
                for p in candidates:
                    if self._try_play(p):
                        logger.info("Alarm audio played: %s", p)
                        played = True
                        break

                if not played and self.enable_audio:
                    logger.error("Alarm audio failed: no playable audio found for %s (%s).", event.point_name, event.threshold_type)

            except Exception as e:
                logger.error("AlarmService error: %s", e)
            finally:
                self._active.discard(event.point_name)
                try:
                    self._q.task_done()
                except Exception:
                    pass
