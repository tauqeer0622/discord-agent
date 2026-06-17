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

    default_permissions = channel.permissions_for(default_role)
    return _view_value(default_permissions) is False
