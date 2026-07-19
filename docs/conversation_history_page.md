# Conversation history page

Issue #12 adds a browser interface for the complete SQLite chat history created
by Project Akira.

Start the backend:

```powershell
python server.py
```

Open:

```text
http://127.0.0.1:8000/history
```

## Features

- Lists saved conversations newest-first
- Searches conversation titles and complete user/Akira messages
- Opens full transcripts with text/voice and spoken-state metadata
- Renames conversations
- Deletes conversations and their turns
- Safely detaches an active conversation if it is deleted
- Starts a fresh chat and returns to the main chat page
- Refreshes after new turns, renames, and deletes through WebSocket events
- Keeps long-term memory separate from chat-history deletion

## API additions

```text
GET    /api/conversations?query=search text
GET    /api/conversations/{id}/summary
PATCH  /api/conversations/{id}
DELETE /api/conversations/{id}
```

The existing transcript endpoint remains:

```text
GET /api/conversations/{id}
```

Opening the history page, searching, renaming, and deleting do not initialize
Whisper, CUDA, TTS, or the avatar. Starting a new chat intentionally initializes
the conversation service because that new conversation becomes the active chat.


## Continue a previous conversation

Select a saved conversation and click **Continue chat**. Project Akira:

1. Makes that SQLite conversation active.
2. Restores its most recent saved turns into the LLM short-term context.
3. Returns to the Chat page.
4. Appends future text or voice turns to the same saved conversation.

Creating a new conversation resets the LLM short-term context so separate
chats do not accidentally share recent conversational state. Long-term
memory remains available across conversations.
