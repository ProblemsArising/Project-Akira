# SQLite chat history

Project Akira stores complete user/Akira turns in:

```text
data/chat_history.db
```

This database is runtime data and is ignored by Git. It is separate from
`data/memories.json`:

- Chat history stores complete conversations for the future WebUI.
- Long-term memory stores a compact prompt context used by the LLM.

## Application integration

`ConversationService` automatically records every successful text or voice
turn. A single service instance keeps writing to the same conversation until
`start_new_conversation()` is called.

```python
from app.conversation import ConversationService

service = ConversationService.from_default_components()

service.process_text("Hello", speak=False)
conversation_id = service.current_conversation_id

service.start_new_conversation()
service.process_text("This starts another conversation", speak=False)
```

## Querying history

```python
from app.history import get_history_store

history = get_history_store()

for conversation in history.list_conversations():
    print(conversation.title, conversation.turn_count)

turns = history.get_turns(conversation_id)
matches = history.search_turns("minecraft")
```

Available operations include:

- Create or rename a conversation
- Record a completed text or voice turn
- List recent conversations
- Read all turns in a conversation
- Search user and Akira messages
- Delete one conversation
- Clear all history

Set `AKIRA_HISTORY_FILE` to use a custom database path in tests or development.
