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

def add_to_context(user_id: str, role: str, content: str):
    """Adds a message to the user's context memory."""
    if user_id not in context_memory:
        context_memory[user_id] = []
    context_memory[user_id].append({"role": role, "content": content})
    save_memory()

async def generate_reply(source_content: str, user_id: str) -> str:
    """
    Uses GPT-4o to draft a contextual, humanized reply to the incoming message.
    """
    if not client:
        return "I'm currently unable to process your request due to missing AI configuration."

    add_to_context(user_id, "user", source_content)

    system_prompt = {
        "role": "system",
        "content": (
            "You are a real, casual human operator of a Discord server, NOT an AI or a formal consultant. "
            "Draft a helpful but very casual and natural reply to the user's message. "
            "Keep it short, use lowercase letters mostly, and talk like a normal person texting on Discord. "
            "NEVER say 'I am here to assist', 'I can help with that', or use formal robotic greetings. "
            "Do not over-punctuate; avoid exclamation marks to sound like a real human. "
            "CRITICAL: You DO have conversational memory! The previous messages in this thread are the user's past interactions with you. "
            "If the user asks if you remember what they said, you must accurately recall and reference their previous messages from this session. "
            "Never say you cannot remember past interactions."
        )
    }

    messages = [system_prompt] + context_memory[user_id]

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
