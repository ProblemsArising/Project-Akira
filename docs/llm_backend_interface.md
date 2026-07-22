# LLM backend interface

Project Akira talks to language-model implementations through
`ai.llm_backend.LLMBackend`.

The interface keeps conversation, Discord, and future game integrations
independent from a specific inference server. The current implementation still
uses the existing LM Studio/OpenAI-compatible client; later v0.5 issues can add
managed llama.cpp without changing callers.

## Required contract

A backend provides:

- `info` — stable ID, display name, and whether Akira manages its process
- `ask(prompt)` — generate one reply
- `reset_short_term_memory()` — clear the active model conversation
- `load_short_term_history(turns)` — restore saved user/assistant turns
- `set_system_prompt(prompt)` — update the active companion prompt
- `close()` — release clients or managed processes

Backends are structurally typed. Third-party implementations do not need to
inherit from a Project Akira base class, but they must provide every protocol
member.

## Factory boundary

Call `ai.llm.create_llm_backend()` instead of constructing the current
implementation directly. The factory currently returns `LocalLLM` and will
become the selection point for the managed llama.cpp backend.

`ConversationService.from_default_components()` and
`ConversationService.from_text_components()` also accept an optional
`llm_backend=` argument. This makes custom backends testable without importing
OpenAI, LM Studio, CUDA, microphone, or TTS components.
