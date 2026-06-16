def _view_value(permissions_or_overwrite):
    for attr in ("view_channel", "read_messages"):
        value = getattr(permissions_or_overwrite, attr, None)
        if value is not None:
            return value
    return None


def _has_visibility_gate(entity, default_role) -> bool:
    overwrites = getattr(entity, "overwrites", {}) or {}
    for target, overwrite in overwrites.items():
        view_value = _view_value(overwrite)
        if target == default_role and view_value is False:
            return True
        if target != default_role and view_value is True:
            return True
    return False


def is_restricted_text_channel(channel) -> bool:
    """Return True for locked/private channels that should stay hidden."""
    guild = getattr(channel, "guild", None)
    default_role = getattr(guild, "default_role", None)
    if default_role is None:
        return False

    default_permissions = channel.permissions_for(default_role)
    if _view_value(default_permissions) is False:
        return True

    if _has_visibility_gate(channel, default_role):
        return True

    category = getattr(channel, "category", None)
    if category is None:
        return False

    if _has_visibility_gate(category, default_role):
        return True

    category_permissions = category.permissions_for(default_role)
    return _view_value(category_permissions) is False
