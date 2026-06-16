EXCLUDED_PRIVATE_CATEGORY_NAMES = {
    "member chats",
}


def is_restricted_text_channel(channel) -> bool:
    """Return True for configured private categories that should stay hidden."""
    category = getattr(channel, "category", None)
    if category is None:
        return False

    return category.name.strip().lower() in EXCLUDED_PRIVATE_CATEGORY_NAMES
