# Project-Akira
Local AI companion

## Set Up:
Python 3.10.11 was used creating this project, I recommend this version for compatability.  
pip install -r requirements.txt
  
### Basic tts only
Download LM Studio and set up a local server  
IF Needed, make api key and put it llm.py  
run the project assistant.py  


### Text chat

Use interactive typed chat instead of microphone input:

```powershell
python assistant.py --text
```

Send one typed message and exit:

```powershell
python assistant.py --message "Hello, Akira"
```

### Listening controls

Open temporary terminal controls for starting, stopping, and checking the
microphone listener:

```powershell
python assistant.py --controls
```

Use `start`, `stop`, `status`, or `quit`. These commands exercise the same
background listening API that the future WebUI will use.

Add `--no-speak` to either command to disable TTS for typed replies.

### Add Voice Changer
Download Virtual Audio Cable  
Download https://github.com/w-okada/voice-changer  
run the project assistant.py, in windows audio settings, change python audio output to vb cable in  
in the voice changer, select vb cable out as audio source and set processing delay to 500ms.  
add voice changer profile if wanted.  

### Add animated model
Download VSeeFace and find or make vrm model. You can make a vrm model with vroid studio, it is free  
open VseeFace and import model and start  
go to settings and change osc/vmc reciever on  
Start assistant.py  
  

## Base Project completed as v0
  
## Development Roadmap

### v0.1 — Application Foundation

- [ ] Central settings system
- [ ] Reusable conversation service
- [ ] SQLite chat history
- [ ] Text-message support
- [ ] Start/stop listening controls
- [ ] Audio-device selection

### v0.2 — WebUI

- [ ] Chat interface
- [ ] Settings interface
- [ ] Conversation history viewer
- [ ] Personality editor
- [ ] Model and device selectors
- [ ] Live status updates

### v0.3 — Desktop Application

- [ ] One-click Windows launcher
- [ ] Separate avatar window
- [ ] System tray support
- [ ] Remember window positions
- [ ] Optional launch on startup
- [ ] Windows installer

### v0.4 — Built-in Avatar

- [ ] Embedded VRM renderer
- [ ] Mouth visemes
- [ ] Facial expressions
- [ ] Idle animation
- [ ] Body poses
- [ ] Optional VMC compatibility

### v0.5 — Built-in LLM

- [ ] LLM backend interface
- [ ] LM Studio support
- [ ] Managed llama.cpp backend
- [ ] Model downloader
- [ ] Hardware presets

### v0.6 — Built-in Voice Conversion

- [ ] TTS audio generation
- [ ] Internal RVC inference
- [ ] Direct audio playback
- [ ] Audio-driven lip sync
- [ ] Remove VB-CABLE dependency

### v0.7 — Minecraft Integration

- [ ] Connect second account
- [ ] Follow and wait commands
- [ ] Basic navigation
- [ ] Resource collection
- [ ] In-game chat
- [ ] LLM action planning
