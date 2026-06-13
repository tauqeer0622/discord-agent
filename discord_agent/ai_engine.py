import logging
from openai import AsyncOpenAI
from config import OPENAI_API_KEY
from typing import Dict, List

logger = logging.getLogger(__name__)

# Initialize the async OpenAI client
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

import json
import os

MEMORY_FILE = "ai_memory.json"

def load_memory() -> Dict[str, List[dict]]:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load AI memory: {e}")
    return {}

def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(context_memory, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save AI memory: {e}")

context_memory: Dict[str, List[dict]] = load_memory()

def add_to_context(memory_key: str, role: str, content: str):

    if memory_key not in context_memory:
        context_memory[memory_key] = []

    context_memory[memory_key].append({
        "role": role,
        "content": content
    })

    save_memory()

async def generate_reply(
    source_content: str,
    memory_key: str
) -> str:
    """
    Uses GPT-4o to draft a contextual, humanized reply to the incoming message.
    """
    print("OPENAI KEY LOADED:", bool(OPENAI_API_KEY))
    if not client:
        return "I'm currently unable to process your request due to missing AI configuration."

    previous_context = list(context_memory.get(memory_key, []))
    add_to_context(memory_key, "user", source_content)

    system_prompt = {
        "role": "system",
        "content": (
            "You are a real, casual human operator of a Discord server, NOT an AI or a formal consultant. "
            "Draft a helpful but very casual and natural reply to the user's message. "
            "Keep it short, use lowercase letters mostly, and talk like a normal person texting on Discord. "
            "NEVER say 'I am here to assist', 'I can help with that', or use formal robotic greetings. "
            "Do not over-punctuate; avoid exclamation marks to sound like a real human. "
            "CRITICAL: Use ONLY the conversation history provided in this thread.\n"
            "Do NOT invent or assume previous conversations.\n"
            "If the user asks what they said before, answer ONLY from the messages in this thread.\n"
            "If there is no relevant previous message in the supplied history, say you don't see one in this conversation.\n"
            "If the user asks What did I ask before?\n"
            "answer using ONLY the previous USER messages from the current thread.\n"
            "Do not include assistant replies.\n"
            "Do not say there is no history if previous user messages exist.\n"
            "Use previous messages only as context.\n"
            "Answer only the latest user message.\n"
            "If previous messages are clearly related to the latest user message, use them as context.\n"
            "If the latest user message changes topic, ignore unrelated previous messages.\n"
            "Do not combine unrelated topics in the same reply.\n"
            "Do not answer previous unanswered questions again.\n"
            "Never fabricate memory."
        )
    }

    latest_message = {
        "role": "user",
        "content": (
            "LATEST USER MESSAGE:\n"
            f"{source_content}\n\n"
            "Reply only to this latest message. Use earlier thread messages only if they are directly relevant."
        )
    }

    messages = [system_prompt] + previous_context + [latest_message]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=150
        )
        reply = response.choices[0].message.content.strip()
        return reply
    except Exception as e:
        logger.error(f"Error generating AI reply: {e}")
        return "Sorry, I ran into an error trying to understand that."

async def humanize_override(operator_input: str, source_content: str) -> str:
    """
    If the operator types a short command or raw input, use GPT-4o to make it sound human.
    """
    if not client:
        return operator_input

    # If it's not a shortcut/command and seems long enough, just send it as-is
    if not operator_input.startswith("!") and len(operator_input) > 20:
        return operator_input

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are helping a human operator rephrase their short notes or commands into "
                        "a fully-formed, polite, and humanized Discord message. The user asked a question, "
                        "and the operator provided a brief instruction or template key."
                    )
                },
                {"role": "user", "content": f"User asked: {source_content}\nOperator input: {operator_input}"}
            ],
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error humanizing override: {e}")
        return operator_input
