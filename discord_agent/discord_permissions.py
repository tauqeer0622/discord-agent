def _view_value(permissions_or_overwrite):
    for attr in ("view_channel", "read_messages"):
        value = getattr(permissions_or_overwrite, attr, None)
        if value is not None:
            return value
    return None


def _resolve_guild_member(guild, user):
    if guild is None or user is None:
        return None

    user_id = getattr(user, "id", None)
    if user_id is not None and hasattr(guild, "get_member"):
        member = guild.get_member(user_id)
        if member is not None:
            return member

    member = getattr(guild, "me", None)
    if member is not None:
        return member

    return None


def can_send_messages(channel, user=None) -> bool:
    """Return True when the current user can post in the channel."""
    guild = getattr(channel, "guild", None)
    targets = []

    member = _resolve_guild_member(guild, user)
    if member is not None:
        targets.append(member)

    if user is not None:
        targets.append(user)

    default_role = getattr(guild, "default_role", None)
    if default_role is not None:
        targets.append(default_role)

    seen = set()
    for target in targets:
        target_id = id(target)
        if target_id in seen:
            continue
        seen.add(target_id)

        try:
            permissions = channel.permissions_for(target)
        except (AttributeError, TypeError):
            continue

        can_view = _view_value(permissions) is not False
        can_send = getattr(permissions, "send_messages", None) is True
        return can_view and can_send

    return False


def is_locked_or_private_channel(channel) -> bool:
    """Return True when the channel is not publicly visible to everyone."""
    guild = getattr(channel, "guild", None)
    default_role = getattr(guild, "default_role", None)
    if default_role is None:
        return False

    try:
        default_permissions = channel.permissions_for(default_role)
    except (AttributeError, TypeError):
        return False

    return _view_value(default_permissions) is False


def is_restricted_text_channel(channel, user=None) -> bool:
    """Return True for locked/private channels or channels we cannot post in."""
    return is_locked_or_private_channel(channel) or not can_send_messages(channel, user)
