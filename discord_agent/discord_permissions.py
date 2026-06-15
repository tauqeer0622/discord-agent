def is_restricted_text_channel(channel) -> bool:
    """Return True for channels hidden from the guild's default role."""
    guild = getattr(channel, "guild", None)
    default_role = getattr(guild, "default_role", None)
    if default_role is None:
        return False

    channel_overwrite = channel.overwrites_for(default_role)
    if channel_overwrite.view_channel is False:
        return True

    category = getattr(channel, "category", None)
    if category is None:
        return False

    category_overwrite = category.overwrites_for(default_role)
    return category_overwrite.view_channel is False
