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


def can_view_text_channel(channel, user=None) -> bool:
    """Return True when the logged-in account can view the channel."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return True

    permission_target = _current_member(guild, user) or getattr(guild, "default_role", None)
    if permission_target is None:
        return True

    permissions = channel.permissions_for(permission_target)
    return _view_value(permissions) is not False


def is_restricted_text_channel(channel, user=None) -> bool:
    """Return True for locked/private channels that should stay hidden."""
    guild = getattr(channel, "guild", None)
    default_role = getattr(guild, "default_role", None)
    if default_role is None:
        return False

    default_permissions = channel.permissions_for(default_role)
    return _view_value(default_permissions) is False
