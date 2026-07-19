# Personality editor

Issue #13 adds a dedicated browser editor for Project Akira personality presets.

Start the backend:

```powershell
python server.py
```

Open:

```text
http://127.0.0.1:8000/personalities
```

## Features

- View and search saved personality presets
- Create custom presets
- Edit names, descriptions, and full system prompts
- Duplicate any preset, including the protected built-in Gamer Companion
- Activate a preset without restarting Project Akira
- Preserve the current conversation context when changing personality
- Delete inactive custom presets
- Receive live WebSocket updates when another page changes a preset
- Warn before leaving with unsaved edits

The built-in Gamer Companion preset cannot be edited or deleted. Duplicate it to
create a customizable copy.

## Storage

Personality definitions are stored locally in:

```text
data/personalities.json
```

The active preset ID remains in:

```text
data/settings.json
```

`data/personalities.json` is ignored by Git so personal prompts are not
accidentally committed. `data/personalities.example.json` documents the format.

For compatibility, a non-empty `personality.prompt` value in `settings.json`
still overrides the selected preset. Activating a preset in the editor clears
that legacy override.

## API

```text
GET    /api/personalities
POST   /api/personalities
PATCH  /api/personalities/{preset_id}
POST   /api/personalities/{preset_id}/duplicate
POST   /api/personalities/{preset_id}/activate
DELETE /api/personalities/{preset_id}
```

Personality changes publish these WebSocket events:

```text
personality.created
personality.updated
personality.changed
personality.deleted
```

Activating or editing the active preset updates the already-loaded LLM system
prompt in place. Existing short-term conversation messages are preserved.
