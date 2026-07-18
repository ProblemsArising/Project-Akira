# FastAPI backend

Project Akira's HTTP and WebSocket backend is the foundation for the v0.2 WebUI. It keeps the
existing CLI intact and exposes the reusable `ConversationService`, listening
controls, settings, and SQLite history through local REST endpoints.

## Install

```powershell
pip install -r requirements.txt
```

## Run

```powershell
python server.py
```

The server binds to `127.0.0.1:8000` by default, so it is accessible only from
the same computer.

Interactive API documentation is available at:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

During backend development, automatic reload can be enabled:

```powershell
python server.py --reload
```

## Initial endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Lightweight server health check |
| GET | `/api/status` | Listening/service/conversation state |
| POST | `/api/chat` | Send a typed message through `ConversationService` |
| POST | `/api/conversations` | Start a new history conversation |
| GET | `/api/conversations` | List recent conversations |
| GET | `/api/conversations/{id}` | Read turns in one conversation |
| POST | `/api/listening/start` | Start microphone listening in the background |
| POST | `/api/listening/stop` | Stop microphone listening |
| GET | `/api/settings` | Read the current settings snapshot |
| WS | `/api/events` | Live chat, voice, listening, and status events |

Example chat request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/chat `
  -ContentType application/json `
  -Body '{"message":"Hello, Akira","speak":false}'
```

## Architecture notes

- FastAPI is only an HTTP adapter; conversation logic remains in
  `app/conversation.py`.
- Heavy components are lazy. `/api/health` and `/api/status` do not initialize
  Whisper, CUDA, TTS, or the avatar.
- The first endpoint that needs `ConversationService` initializes the production
  pipeline.
- Calls to `ConversationService.process_text()` remain serialized by the
  service's existing lock.
- The server shuts down the listening loop through FastAPI's lifespan handler.
- WebSocket event details and browser examples are in [`websocket_events.md`](websocket_events.md).
