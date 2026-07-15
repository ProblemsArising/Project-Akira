import pyttsx3
import time

from avatar.vmc import start_talking, stop_talking

MOUTH_END_DELAY_SECONDS = 0.05

def tts(text):
    text = str(text).strip()
    if not text:
        return

    engine = pyttsx3.init()

    voices = engine.getProperty("voices")
    if len(voices) > 1:
        engine.setProperty("voice", voices[1].id)

    # Optional tuning. Change these if your RVCC voice sounds better differently.
    engine.setProperty("rate", 175)
    engine.setProperty("volume", 1.0)

    try:
        # Pass the actual reply text to the fake viseme driver.
        start_talking(text)
        engine.say(text)
        engine.runAndWait()
        time.sleep(MOUTH_END_DELAY_SECONDS)
        
    finally:
        stop_talking()
