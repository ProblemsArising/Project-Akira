# Chat page

Issue #10 adds the first browser-based Project Akira interface.

Start the local backend:

```powershell
python server.py
```

Open:

```text
http://127.0.0.1:8000/
```

`/chat` opens the same page. FastAPI's developer documentation remains at
`/docs`.

## Current features

- Typed chat through `POST /api/chat`
- Optional TTS for typed replies
- Start and stop microphone listening
- Live WebSocket connection and activity state
- Voice transcription/reply events displayed in the same chat
- New-conversation button
- Current session restoration after a page refresh
- Responsive layout for smaller browser windows
- No external JavaScript, CSS, font, or image dependencies

## Scope

This issue deliberately implements only the main chat experience.

Later v0.2 issues add:

- Full settings page
- Conversation history browser
- Personality editor
- Audio calibration page
- Model/backend selector

## Files

```text
web/chat/index.html
web/chat/styles.css
web/chat/app.js
```

FastAPI serves these files locally. The page communicates only with the
Project Akira process running on the same address.
