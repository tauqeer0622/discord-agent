import unittest
from types import SimpleNamespace

from discord_permissions import (
    can_send_messages,
    is_locked_or_private_channel,
    is_restricted_text_channel,
)


class FakeTarget:
    def __init__(self, target_id):
        self.id = target_id


class FakeGuild:
    def __init__(self, default_role, member=None):
        self.default_role = default_role
        self._member = member

    def get_member(self, user_id):
        if self._member is not None and self._member.id == user_id:
            return self._member
        return None


class FakeChannel:
    def __init__(self, guild, permissions_by_target):
        self.guild = guild
        self.permissions_by_target = permissions_by_target

    def permissions_for(self, target):
        return self.permissions_by_target[target]


def permissions(view_channel=True, send_messages=True):
    return SimpleNamespace(
        view_channel=view_channel,
        read_messages=view_channel,
        send_messages=send_messages,
    )


class DiscordPermissionTests(unittest.TestCase):
    def test_allows_channel_when_current_user_can_send(self):
        user = SimpleNamespace(id=100)
        member = FakeTarget(100)
        default_role = FakeTarget(1)
        guild = FakeGuild(default_role, member)
        channel = FakeChannel(
            guild,
            {
                member: permissions(view_channel=True, send_messages=True),
                default_role: permissions(view_channel=True, send_messages=False),
            },
        )

        self.assertTrue(can_send_messages(channel, user))
        self.assertFalse(is_locked_or_private_channel(channel))
        self.assertFalse(is_restricted_text_channel(channel, user))

    def test_blocks_locked_channel_even_when_current_user_can_send(self):
        user = SimpleNamespace(id=100)
        member = FakeTarget(100)
        default_role = FakeTarget(1)
        guild = FakeGuild(default_role, member)
        channel = FakeChannel(
            guild,
            {
                member: permissions(view_channel=True, send_messages=True),
                default_role: permissions(view_channel=False, send_messages=False),
            },
        )

        self.assertTrue(can_send_messages(channel, user))
        self.assertTrue(is_locked_or_private_channel(channel))
        self.assertTrue(is_restricted_text_channel(channel, user))

    def test_blocks_channel_when_current_user_can_view_but_not_send(self):
        user = SimpleNamespace(id=100)
        member = FakeTarget(100)
        default_role = FakeTarget(1)
        guild = FakeGuild(default_role, member)
        channel = FakeChannel(
            guild,
            {
                member: permissions(view_channel=True, send_messages=False),
                default_role: permissions(view_channel=True, send_messages=True),
            },
        )

        self.assertFalse(can_send_messages(channel, user))
        self.assertFalse(is_locked_or_private_channel(channel))
        self.assertTrue(is_restricted_text_channel(channel, user))

    def test_blocks_channel_when_current_user_cannot_view(self):
        user = SimpleNamespace(id=100)
        member = FakeTarget(100)
        default_role = FakeTarget(1)
        guild = FakeGuild(default_role, member)
        channel = FakeChannel(
            guild,
            {
                member: permissions(view_channel=False, send_messages=True),
                default_role: permissions(view_channel=True, send_messages=True),
            },
        )

        self.assertFalse(can_send_messages(channel, user))
        self.assertFalse(is_locked_or_private_channel(channel))
        self.assertTrue(is_restricted_text_channel(channel, user))

    def test_falls_back_to_default_role_without_user(self):
        default_role = FakeTarget(1)
        guild = FakeGuild(default_role)
        channel = FakeChannel(
            guild,
            {
                default_role: permissions(view_channel=True, send_messages=True),
            },
        )

        self.assertTrue(can_send_messages(channel))
        self.assertFalse(is_locked_or_private_channel(channel))
        self.assertFalse(is_restricted_text_channel(channel))


if __name__ == "__main__":
    unittest.main()
