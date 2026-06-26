import os


async def generate_promo_variant(base_message, target, index, total):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc

    client = AsyncOpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    channel_name = target.get("channel_name") or target.get("label") or "the channel"
    guild_name = target.get("guild_name") or "the server"

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Rewrite the user's promo message as a short, natural Discord message. "
                    "Keep the same meaning and offer. Do not add claims, prices, guarantees, "
                    "urgency, links, or facts that were not in the original. Do not mention AI. "
                    "Return only the message text."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Base promo message:\n{base_message}\n\n"
                    f"Target {index} of {total}: #{channel_name} in {guild_name}.\n"
                    "Make this version slightly different from the others."
                ),
            },
        ],
        temperature=0.8,
        max_tokens=180,
    )
    return response.choices[0].message.content.strip()
