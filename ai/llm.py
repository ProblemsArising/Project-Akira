from openai import OpenAI
from .personality import getPersonality
from .memory import build_memory_context, remember_turn

client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="None"  # LM Studio ignores this, but the library requires one
)

# Short-term memory for the current run.
# Long-term memory is stored in data/memories.json by ai/memory.py.
messages = [
    {
        "role": "system",
        "content": getPersonality()
    }
]

MAX_SHORT_TERM_MESSAGES = 20


def _trim_short_term_memory():
    """Keep the current-run chat history from growing forever."""
    global messages
    system_message = messages[0]
    conversation = messages[1:]

    if len(conversation) > MAX_SHORT_TERM_MESSAGES:
        conversation = conversation[-MAX_SHORT_TERM_MESSAGES:]

    messages = [system_message] + conversation


def ask_ai(prompt):
    global messages

    prompt = prompt.strip()
    if not prompt:
        return ""

    # Add the user's message to short-term memory.
    messages.append({
        "role": "user",
        "content": prompt
    })

    memory_context = build_memory_context(prompt)

    api_messages = [messages[0]]

    if memory_context:
        api_messages.append({
            "role": "system",
            "content": (
                "Use this long-term memory only when it is relevant. "
                "Do not mention the memory system or the JSON file. "
                "If the user asks what they said before, use the memory below.\n\n"
                f"{memory_context}"
            )
        })

    api_messages.extend(messages[1:])

    response = client.chat.completions.create(
        model="gemma4-12b-qat-uncensored-hauhaucs-balanced",
        messages=api_messages,
        temperature=0.75,
        top_p=0.9,
        max_tokens=180,
        stop=[
            "\nUser:",
            "\nUSER:",
            "\nHuman:",
            "\nAssistant:",
            "<|im_end|>",
            "<|endoftext|>",
        ],
    )

    reply = response.choices[0].message.content.strip()

    # Save the AI's reply to short-term memory.
    messages.append({
        "role": "assistant",
        "content": reply
    })

    _trim_short_term_memory()

    # Save the completed turn to long-term memory on disk.
    remember_turn(prompt, reply)

    return reply
