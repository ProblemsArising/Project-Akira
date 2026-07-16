# Text-message input

Project Akira can accept typed messages through the same `ConversationService`
used by microphone conversations. Typed and voice messages share the same
short-term LLM context, long-term memory, and SQLite history conversation.

## Interactive text chat

```powershell
python assistant.py --text
```

Akira still speaks typed replies through TTS by default. To use text-only
replies:

```powershell
python assistant.py --text --no-speak
```

Interactive commands:

- `/new` starts a new SQLite history conversation.
- `/new Minecraft planning` starts a new conversation with a title.
- `/speak on` enables TTS for typed replies.
- `/speak off` disables TTS for typed replies.
- `/help` displays available commands.
- `/quit` exits text chat.

## One-shot message

```powershell
python assistant.py --message "Hello, Akira"
```

To generate the answer without TTS:

```powershell
python assistant.py --message "Hello, Akira" --no-speak
```

## Future WebUI

The terminal interface is intentionally thin. A WebUI should submit messages
through:

```python
result = conversation_service.process_text(message, speak=True)
```

It should not implement a separate LLM or history pipeline.
