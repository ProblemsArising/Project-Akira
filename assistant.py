from audio.microphone import record_audio
from audio.whisper_stt import transcribe
from ai.llm import ask_ai
from audio.tts import tts

while True:
    audio_file = record_audio()
    if not audio_file:
        continue

    text = transcribe()

    if not text:
        continue

    reply = ask_ai(text)

    print(reply)

    tts(reply)