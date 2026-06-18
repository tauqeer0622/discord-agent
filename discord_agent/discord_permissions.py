def _view_value(permissions_or_overwrite):
    for attr in ("view_channel", "read_messages"):
        value = getattr(permissions_or_overwrite, attr, None)
        if value is not None:
            return value
    return None


def is_restricted_text_channel(channel) -> bool:
    """Return True for locked/private channels that should stay hidden."""
    guild = getattr(channel, "guild", None)
    default_role = getattr(guild, "default_role", None)
    if default_role is None:
        return False

    channel_overwrite = channel.overwrites_for(default_role)
    if _view_value(channel_overwrite) is False:
        return True

    category = getattr(channel, "category", None)
    if category is not None:
        category_overwrite = category.overwrites_for(default_role)
        if _view_value(category_overwrite) is False:
            return True

    default_permissions = channel.permissions_for(default_role)
    return _view_value(default_permissions) is False
