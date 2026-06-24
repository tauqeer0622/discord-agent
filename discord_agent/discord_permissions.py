def _view_value(permissions_or_overwrite):
    for attr in ("view_channel", "read_messages"):
        value = getattr(permissions_or_overwrite, attr, None)
        if value is not None:
            return value
    return None


def _current_member(guild, user=None):
    for attr in ("me", "self_member"):
        member = getattr(guild, attr, None)
        if callable(member):
            member = member()
        if member is not None and (user is None or getattr(member, "id", None) == getattr(user, "id", None)):
            return member

    if user is not None and hasattr(guild, "get_member"):
        return guild.get_member(user.id)

    return None


def is_restricted_text_channel(channel, user=None) -> bool:
    """Return True when the logged-in account cannot view the channel."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return False

    permission_target = _current_member(guild, user) or getattr(guild, "default_role", None)
    if permission_target is None:
        return False

    permissions = channel.permissions_for(permission_target)
    return _view_value(permissions) is False
