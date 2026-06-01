"""
config_manager.py
─────────────────
Manages dynamic target-channel configurations that are persisted to
channel_configs.json.  On first startup the file is seeded from the
hardcoded TARGET_CHANNELS list in config.py so existing behaviour is
preserved.
"""

import json
import logging
import os

from config import TARGET_CHANNELS

logger = logging.getLogger(__name__)

CONFIGS_FILE = os.path.join(os.path.dirname(__file__), "channel_configs.json")


class ConfigManager:
    def __init__(self):
        self._configs: list[dict] = []
        self._load()

    # ── Persistence ──────────────────────────────────────────────

    def _load(self):
        if os.path.exists(CONFIGS_FILE):
            try:
                with open(CONFIGS_FILE, "r", encoding="utf-8") as f:
                    self._configs = json.load(f)
                logger.info(f"Loaded {len(self._configs)} channel config(s) from {CONFIGS_FILE}.")
                return
            except Exception as e:
                logger.error(f"Failed to load channel configs: {e}")

        # First run — seed from hardcoded defaults in config.py
        self._configs = [
            {
                "label": ch["label"],
                "channel_id": ch["channel_id"],
                "guild_id": None,
                "guild_name": None,
                "active": True,
            }
            for ch in TARGET_CHANNELS
        ]
        self._save()
        logger.info(f"Seeded {len(self._configs)} config(s) from config.py defaults.")

    def _save(self):
        try:
            with open(CONFIGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._configs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save channel configs: {e}")

    # ── CRUD ─────────────────────────────────────────────────────

    def get_all(self) -> list[dict]:
        """Return all configs (active and inactive)."""
        return list(self._configs)

    def add(self, label: str, channel_id: int | str, guild_id: int | str | None, guild_name: str | None) -> bool:
        """Add a new config. Returns False if the channel is already tracked."""
        channel_id = int(channel_id)
        guild_id   = int(guild_id) if guild_id else None
        if any(int(c["channel_id"]) == channel_id for c in self._configs):
            return False
        self._configs.append({
            "label":      label,
            "channel_id": channel_id,
            "guild_id":   guild_id,
            "guild_name": guild_name,
            "active":     True,
        })
        self._save()
        logger.info(f"Added config: '{label}' (channel {channel_id})")
        return True

    def remove(self, channel_id: int | str) -> bool:
        """Remove a config by channel_id. Returns True if removed."""
        channel_id = int(channel_id)
        before = len(self._configs)
        self._configs = [c for c in self._configs if int(c["channel_id"]) != channel_id]
        if len(self._configs) < before:
            self._save()
            logger.info(f"Removed config for channel {channel_id}")
            return True
        return False

    def toggle(self, channel_id: int | str) -> bool | None:
        """Toggle active status. Returns new active value, or None if not found."""
        channel_id = int(channel_id)
        for c in self._configs:
            if int(c["channel_id"]) == channel_id:
                c["active"] = not c.get("active", True)
                self._save()
                logger.info(f"Toggled channel {channel_id} → active={c['active']}")
                return c["active"]
        return None

    # ── Helpers ──────────────────────────────────────────────────

    def get_active_channel_ids(self) -> list[int]:
        return [int(c["channel_id"]) for c in self._configs if c.get("active", True)]

    def active_count(self) -> int:
        return sum(1 for c in self._configs if c.get("active", True))

    def inactive_count(self) -> int:
        return sum(1 for c in self._configs if not c.get("active", True))


# Global singleton
config_manager = ConfigManager()
