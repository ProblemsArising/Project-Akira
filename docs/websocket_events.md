# WebSocket event system

Project Akira exposes live WebUI events at:

```text
ws://127.0.0.1:8000/api/events
```

The WebSocket is for live state and progress updates. Actions such as sending a
message, starting listening, or changing conversations still use the REST API.
This keeps commands easy to validate and lets every connected WebUI window see
the same event stream.

## Event envelope

Every message sent by the backend has the same structure:

```json
{
  "sequence": 12,
  "type": "chat.completed",
  "timestamp": "2026-07-18T18:30:00.123Z",
  "data": {
    "user_text": "Hello",
    "reply": "Hey!",
    "source": "text",
    "spoken": false,
    "conversation_id": 4
  }
}
```

`sequence` increases for the lifetime of the server process. It can be used to
preserve ordering across chat, listening, and conversation events.

## Connection commands

A client may send small JSON commands over the WebSocket:

```json
{"type": "ping", "data": {"id": 1}}
```

The server answers with `connection.pong` and echoes the supplied data.

```json
{"type": "status"}
```

The server answers with `status.snapshot` without loading heavy AI components.
All actual application actions remain normal HTTP requests.

## Events

| Event | Meaning |
| --- | --- |
| `connection.ready` | Initial status after a browser connects |
| `connection.pong` | Response to a connection ping |
| `connection.error` | Invalid or unknown WebSocket command |
| `status.snapshot` | Current backend state requested by this client |
| `listening.changed` | Microphone listening started or stopped |
| `voice.recording.started` | VAD began waiting/recording |
| `voice.recording.completed` | A WAV file was captured |
| `voice.recording.cancelled` | Recording stopped without usable audio |
| `voice.transcription.started` | Whisper transcription began |
| `voice.transcription.completed` | Whisper returned text |
| `voice.transcription.empty` | Whisper returned no usable text |
| `chat.started` | A voice or typed turn entered the LLM pipeline |
| `chat.reply_ready` | Visible LLM text is ready, before optional TTS finishes |
| `chat.completed` | TTS/history processing finished for the turn |
| `chat.failed` | The turn failed or produced an empty reply |
| `conversation.changed` | A new history conversation became active |
| `history.error` | A completed turn could not be written to SQLite |
| `system.shutdown` | The backend is shutting down |

## Browser example

```javascript
const socket = new WebSocket("ws://127.0.0.1:8000/api/events");

socket.addEventListener("message", (message) => {
  const event = JSON.parse(message.data);
  console.log(event.sequence, event.type, event.data);
});

socket.addEventListener("open", () => {
  socket.send(JSON.stringify({ type: "status" }));
});
```

## Threading and backpressure

Conversation and microphone work may emit events from background threads.
`app/events.py` safely transfers them to each WebSocket's asyncio queue. Every
connection has a bounded queue; if a browser stops reading, old live events are
dropped in favor of newer state rather than allowing unbounded memory growth.
