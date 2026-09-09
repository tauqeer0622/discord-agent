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


import re

ADMIN_KEYWORD_REGEX = re.compile(
    r"\b(admin|administrator|mod|moderator|owner|co-owner|founder|co-founder|staff|lead|core\s*team|community\s*manager|management|manager|officer|head|support)\b",
    re.IGNORECASE,
)


def is_admin_member(member, guild=None, channel=None, role_map=None) -> bool:
    """Return True if the member has administrator / moderation privileges
    or holds an admin/mod/staff role in the guild or channel.
    """
    if member is None:
        return False

    member_id = getattr(member, "id", None)
    if member_id is None:
        return False

    g = guild or getattr(member, "guild", None)
    # 1. Server Owner check
    if g is not None:
        owner_id = getattr(g, "owner_id", None)
        if owner_id and member_id == owner_id:
            return True

    # 2. Guild Administrator & Management Permissions
    perms = getattr(member, "guild_permissions", None)
    if perms is not None:
        if (
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
            or getattr(perms, "manage_channels", False)
            or getattr(perms, "manage_roles", False)
            or getattr(perms, "ban_members", False)
            or getattr(perms, "kick_members", False)
            or getattr(perms, "manage_messages", False)
        ):
            return True

    # 3. Channel Administrator / Management Permissions (e.g. channel moderators)
    if channel is not None and hasattr(channel, "permissions_for"):
        try:
            ch_perms = channel.permissions_for(member)
            if (
                getattr(ch_perms, "administrator", False)
                or getattr(ch_perms, "manage_channels", False)
                or getattr(ch_perms, "manage_messages", False)
                or getattr(ch_perms, "manage_permissions", False)
            ):
                return True
        except Exception:
            pass

    # 4. Role Permissions and Names
    raw_roles = getattr(member, "roles", []) or []
    for r in raw_roles:
        r_perms = getattr(r, "permissions", None)
        if r_perms is not None:
            if (
                getattr(r_perms, "administrator", False)
                or getattr(r_perms, "manage_guild", False)
                or getattr(r_perms, "manage_channels", False)
                or getattr(r_perms, "manage_roles", False)
                or getattr(r_perms, "ban_members", False)
                or getattr(r_perms, "kick_members", False)
                or getattr(r_perms, "manage_messages", False)
            ):
                return True

        r_name = getattr(r, "name", None)
        if not r_name and role_map and hasattr(r, "id"):
            r_name = role_map.get(r.id)
        elif not r_name and role_map and isinstance(r, int):
            r_name = role_map.get(r)

        if r_name and r_name != "@everyone":
            if ADMIN_KEYWORD_REGEX.search(r_name):
                return True

    # 5. Guild-level role lookup for members with raw role IDs
    if g is not None and hasattr(g, "roles"):
        role_ids = set()
        for r in raw_roles:
            if hasattr(r, "id"):
                role_ids.add(r.id)
            elif isinstance(r, int):
                role_ids.add(r)
        raw_m_roles = getattr(member, "_roles", None)
        if raw_m_roles:
            role_ids.update(raw_m_roles)

        for g_role in getattr(g, "roles", []):
            if g_role.id in role_ids and g_role.name != "@everyone":
                g_perms = getattr(g_role, "permissions", None)
                if g_perms and (
                    getattr(g_perms, "administrator", False)
                    or getattr(g_perms, "manage_guild", False)
                    or getattr(g_perms, "manage_messages", False)
                    or getattr(g_perms, "ban_members", False)
                ):
                    return True
                if ADMIN_KEYWORD_REGEX.search(g_role.name):
                    return True

    return False
