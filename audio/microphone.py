"""Microphone recording with simple energy-based voice activity detection."""

from __future__ import annotations

from collections import deque
import threading
import time

import numpy as np
import scipy.io.wavfile as wav
import sounddevice as sd

SAMPLE_RATE = 16000
AUDIO_FILE = "input.wav"
CHANNELS = 1

# Smaller frames react faster. 30ms is a common VAD frame size.
FRAME_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_MS / 1000)

# How much audio to keep from right before speech starts, so the first syllable
# does not get cut off.
PRE_ROLL_SECONDS = 0.35

# Stop after this much silence once speech has started.
END_SILENCE_SECONDS = 2.0

# Ignore tiny coughs/clicks by requiring this much speech before saving normally.
MIN_RECORD_SECONDS = 0.45

# Safety cap so the recorder cannot run forever if background noise triggers it.
MAX_RECORD_SECONDS = 45

# Calibrate room noise at the start of each recording call.
CALIBRATION_SECONDS = 0.6

# Tune these if your mic/environment needs it.
START_THRESHOLD_MULTIPLIER = 6.0
END_THRESHOLD_MULTIPLIER = 1.8
MIN_START_THRESHOLD = 0.02
MIN_END_THRESHOLD = 0.006


def _rms(frame: np.ndarray) -> float:
    """Return root-mean-square volume for a frame of float audio."""

    frame = np.asarray(frame, dtype=np.float32)
    return float(np.sqrt(np.mean(np.square(frame))))


def _stop_requested(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _record_noise_floor(
    stream: sd.InputStream,
    stop_event: threading.Event | None = None,
) -> float | None:
    """Listen briefly and estimate the room/mic noise floor.

    ``None`` means recording was cancelled while calibrating.
    """

    rms_values = []
    frames_needed = max(1, int(CALIBRATION_SECONDS * SAMPLE_RATE / FRAME_SIZE))

    for _ in range(frames_needed):
        if _stop_requested(stop_event):
            return None

        frame, overflowed = stream.read(FRAME_SIZE)
        if overflowed:
            continue
        rms_values.append(_rms(frame))

    if not rms_values:
        return 0.0

    # Median is less affected by a random click than average.
    return float(np.median(rms_values))


def _float_to_int16(audio: np.ndarray) -> np.ndarray:
    """Convert sounddevice float32 audio in [-1, 1] to normal 16-bit WAV PCM."""

    audio = np.asarray(audio, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767).astype(np.int16)


def record_audio(
    output_file: str = AUDIO_FILE,
    *,
    stop_event: threading.Event | None = None,
    input_device: int | str | None = None,
) -> str | None:
    """Record from the microphone until speech ends or recording is cancelled.

    Returns the saved WAV filename, or ``None`` if no usable speech was captured.
    When ``stop_event`` is set, the active recording is discarded so pressing a
    Stop Listening control cannot accidentally submit a partial sentence.
    """

    if _stop_requested(stop_event):
        return None

    print("\n🎤 Listening... start talking when ready. Press Ctrl+C to stop.")

    pre_roll_frames = max(1, int(PRE_ROLL_SECONDS * SAMPLE_RATE / FRAME_SIZE))
    pre_roll = deque(maxlen=pre_roll_frames)

    recorded_frames = []
    speech_started = False
    speech_start_time = None
    last_speech_time = None

    with sd.InputStream(
        device=input_device,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        blocksize=FRAME_SIZE,
    ) as stream:
        noise_floor = _record_noise_floor(stream, stop_event)
        if noise_floor is None:
            print("⏹️ Listening stopped.")
            return None

        start_threshold = max(noise_floor * START_THRESHOLD_MULTIPLIER, MIN_START_THRESHOLD)
        end_threshold = max(noise_floor * END_THRESHOLD_MULTIPLIER, MIN_END_THRESHOLD)

        print(
            f"✅ Calibrated noise floor: {noise_floor:.4f} "
            f"| start: {start_threshold:.4f} | stop: {end_threshold:.4f}"
        )

        while True:
            if _stop_requested(stop_event):
                print("⏹️ Listening stopped.")
                return None

            frame, overflowed = stream.read(FRAME_SIZE)
            if overflowed:
                print("⚠️ Mic buffer overflowed; continuing...")

            frame = frame.copy()
            volume = _rms(frame)
            now = time.monotonic()

            if not speech_started:
                pre_roll.append(frame)

                if volume >= start_threshold:
                    speech_started = True
                    speech_start_time = now
                    last_speech_time = now
                    recorded_frames.extend(list(pre_roll))
                    recorded_frames.append(frame)
                    print("🗣️ Speech detected. Recording...")

                continue

            recorded_frames.append(frame)

            if volume >= end_threshold:
                last_speech_time = now

            # These values are guaranteed to be populated once speech starts.
            recording_duration = now - speech_start_time
            silence_duration = now - last_speech_time

            if (
                recording_duration >= MIN_RECORD_SECONDS
                and silence_duration >= END_SILENCE_SECONDS
            ):
                break

            if recording_duration >= MAX_RECORD_SECONDS:
                print("⏱️ Max recording length reached; saving what was captured.")
                break

    if _stop_requested(stop_event):
        print("⏹️ Listening stopped.")
        return None

    if not recorded_frames:
        print("🤫 No speech captured.")
        return None

    audio = np.concatenate(recorded_frames, axis=0)

    if len(audio) < int(MIN_RECORD_SECONDS * SAMPLE_RATE):
        print("🤫 Speech was too short; skipping.")
        return None

    wav.write(output_file, SAMPLE_RATE, _float_to_int16(audio))
    print(f"✅ Saved audio: {output_file}")
    return output_file


class MicrophoneRecorder:
    """Interruptible recorder used by ``ConversationService``.

    A single instance can be stopped and later reset/reused, which maps cleanly
    to Start Listening and Stop Listening buttons in the future WebUI.
    """

    def __init__(
        self,
        output_file: str = AUDIO_FILE,
        *,
        input_device: int | str | None = None,
    ) -> None:
        self.output_file = output_file
        self.input_device = input_device
        self._stop_event = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def record(self) -> str | None:
        return record_audio(
            self.output_file,
            stop_event=self._stop_event,
            input_device=self.input_device,
        )

    def set_input_device(self, input_device: int | str | None) -> None:
        """Use a different microphone for future recording calls."""

        self.input_device = input_device

    def request_stop(self) -> None:
        self._stop_event.set()

    def reset(self) -> None:
        self._stop_event.clear()
