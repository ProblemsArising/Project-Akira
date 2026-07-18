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
    *,
    frame_size: int,
    sample_rate: int,
    calibration_seconds: float = CALIBRATION_SECONDS,
    stop_event: threading.Event | None = None,
) -> float | None:
    """Listen briefly and estimate the room/mic noise floor.

    ``None`` means recording was cancelled while calibrating.
    """

    rms_values = []
    frames_needed = max(1, int(calibration_seconds * sample_rate / frame_size))

    for _ in range(frames_needed):
        if _stop_requested(stop_event):
            return None

        frame, overflowed = stream.read(frame_size)
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


def _supported_input_sample_rate(
    input_device: int | str | None,
    *,
    requested_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    sd_module=sd,
) -> int:
    """Choose a sample rate the selected microphone can actually open.

    The recorder prefers Project Akira's 16 kHz rate. Some Windows WASAPI
    endpoints only accept their native shared-mode rate, so fall back to the
    device's advertised default when necessary.
    """

    requested_rate = max(1, int(requested_rate))

    try:
        sd_module.check_input_settings(
            device=input_device,
            channels=channels,
            samplerate=requested_rate,
            dtype="float32",
        )
        return requested_rate
    except Exception as requested_error:
        try:
            info = sd_module.query_devices(input_device, kind="input")
            fallback_rate = int(round(float(info.get("default_samplerate", 0))))
        except Exception as query_error:
            raise RuntimeError(
                f"Could not inspect microphone {input_device!r}: {query_error}"
            ) from requested_error

        if fallback_rate <= 0 or fallback_rate == requested_rate:
            raise RuntimeError(
                f"Microphone {input_device!r} does not support {requested_rate} Hz."
            ) from requested_error

        try:
            sd_module.check_input_settings(
                device=input_device,
                channels=channels,
                samplerate=fallback_rate,
                dtype="float32",
            )
        except Exception as fallback_error:
            raise RuntimeError(
                f"Microphone {input_device!r} supports neither {requested_rate} Hz "
                f"nor its advertised default of {fallback_rate} Hz."
            ) from fallback_error

        print(
            f"ℹ️ Microphone does not support {requested_rate} Hz through this "
            f"audio API; using {fallback_rate} Hz instead."
        )
        return fallback_rate


def record_audio(
    output_file: str = AUDIO_FILE,
    *,
    stop_event: threading.Event | None = None,
    input_device: int | str | None = None,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    frame_ms: int = FRAME_MS,
    pre_roll_seconds: float = PRE_ROLL_SECONDS,
    end_silence_seconds: float = END_SILENCE_SECONDS,
    min_record_seconds: float = MIN_RECORD_SECONDS,
    max_record_seconds: float = MAX_RECORD_SECONDS,
    calibration_seconds: float = CALIBRATION_SECONDS,
    start_threshold_multiplier: float = START_THRESHOLD_MULTIPLIER,
    end_threshold_multiplier: float = END_THRESHOLD_MULTIPLIER,
    min_start_threshold: float = MIN_START_THRESHOLD,
    min_end_threshold: float = MIN_END_THRESHOLD,
) -> str | None:
    """Record from the microphone until speech ends or recording is cancelled.

    Returns the saved WAV filename, or ``None`` if no usable speech was captured.
    When ``stop_event`` is set, the active recording is discarded so pressing a
    Stop Listening control cannot accidentally submit a partial sentence.
    """

    if _stop_requested(stop_event):
        return None

    print("\n🎤 Listening... start talking when ready. Press Ctrl+C to stop.")

    stream_sample_rate = _supported_input_sample_rate(
        input_device, requested_rate=sample_rate, channels=channels
    )
    frame_size = max(1, int(stream_sample_rate * frame_ms / 1000))
    pre_roll_frames = max(
        1,
        int(pre_roll_seconds * stream_sample_rate / frame_size),
    )
    pre_roll = deque(maxlen=pre_roll_frames)

    recorded_frames = []
    speech_started = False
    speech_start_time = None
    last_speech_time = None

    with sd.InputStream(
        device=input_device,
        samplerate=stream_sample_rate,
        channels=channels,
        dtype="float32",
        blocksize=frame_size,
    ) as stream:
        noise_floor = _record_noise_floor(
            stream,
            frame_size=frame_size,
            sample_rate=stream_sample_rate,
            calibration_seconds=calibration_seconds,
            stop_event=stop_event,
        )
        if noise_floor is None:
            print("⏹️ Listening stopped.")
            return None

        start_threshold = max(noise_floor * start_threshold_multiplier, min_start_threshold)
        end_threshold = max(noise_floor * end_threshold_multiplier, min_end_threshold)

        print(
            f"✅ Calibrated noise floor: {noise_floor:.4f} "
            f"| start: {start_threshold:.4f} | stop: {end_threshold:.4f}"
        )

        while True:
            if _stop_requested(stop_event):
                print("⏹️ Listening stopped.")
                return None

            frame, overflowed = stream.read(frame_size)
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
                recording_duration >= min_record_seconds
                and silence_duration >= end_silence_seconds
            ):
                break

            if recording_duration >= max_record_seconds:
                print("⏱️ Max recording length reached; saving what was captured.")
                break

    if _stop_requested(stop_event):
        print("⏹️ Listening stopped.")
        return None

    if not recorded_frames:
        print("🤫 No speech captured.")
        return None

    audio = np.concatenate(recorded_frames, axis=0)

    if len(audio) < int(min_record_seconds * stream_sample_rate):
        print("🤫 Speech was too short; skipping.")
        return None

    wav.write(output_file, stream_sample_rate, _float_to_int16(audio))
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
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        frame_ms: int = FRAME_MS,
        pre_roll_seconds: float = PRE_ROLL_SECONDS,
        end_silence_seconds: float = END_SILENCE_SECONDS,
        min_record_seconds: float = MIN_RECORD_SECONDS,
        max_record_seconds: float = MAX_RECORD_SECONDS,
        calibration_seconds: float = CALIBRATION_SECONDS,
        start_threshold_multiplier: float = START_THRESHOLD_MULTIPLIER,
        end_threshold_multiplier: float = END_THRESHOLD_MULTIPLIER,
        min_start_threshold: float = MIN_START_THRESHOLD,
        min_end_threshold: float = MIN_END_THRESHOLD,
    ) -> None:
        self.output_file = output_file
        self.input_device = input_device
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame_ms = int(frame_ms)
        self.pre_roll_seconds = float(pre_roll_seconds)
        self.end_silence_seconds = float(end_silence_seconds)
        self.min_record_seconds = float(min_record_seconds)
        self.max_record_seconds = float(max_record_seconds)
        self.calibration_seconds = float(calibration_seconds)
        self.start_threshold_multiplier = float(start_threshold_multiplier)
        self.end_threshold_multiplier = float(end_threshold_multiplier)
        self.min_start_threshold = float(min_start_threshold)
        self.min_end_threshold = float(min_end_threshold)
        self._stop_event = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def record(self) -> str | None:
        return record_audio(
            self.output_file,
            stop_event=self._stop_event,
            input_device=self.input_device,
            sample_rate=self.sample_rate,
            channels=self.channels,
            frame_ms=self.frame_ms,
            pre_roll_seconds=self.pre_roll_seconds,
            end_silence_seconds=self.end_silence_seconds,
            min_record_seconds=self.min_record_seconds,
            max_record_seconds=self.max_record_seconds,
            calibration_seconds=self.calibration_seconds,
            start_threshold_multiplier=self.start_threshold_multiplier,
            end_threshold_multiplier=self.end_threshold_multiplier,
            min_start_threshold=self.min_start_threshold,
            min_end_threshold=self.min_end_threshold,
        )

    def set_input_device(self, input_device: int | str | None) -> None:
        """Use a different microphone for future recording calls."""

        self.input_device = input_device

    def request_stop(self) -> None:
        self._stop_event.set()

    def reset(self) -> None:
        self._stop_event.clear()
