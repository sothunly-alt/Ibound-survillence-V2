"""Tests for the zero-touch cloud config sync (no live network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_sync import CloudConfigSync


def _cfg(**over) -> dict:
    base = {
        "enabled": True,
        "url": "https://demo.supabase.co",
        "anon_key": "anon-key",
        "site_key": "site-abc",
        "interval_seconds": 60,
    }
    base.update(over)
    return base


class CloudConfigSyncTests(unittest.TestCase):
    def test_disabled_is_noop(self) -> None:
        sync = CloudConfigSync(_cfg(enabled=False), lambda p: {"success": True})
        self.assertFalse(sync.configured)
        self.assertEqual(sync.poll_once(), {"applied": False, "reason": "not configured"})

    def test_missing_site_key_not_configured(self) -> None:
        sync = CloudConfigSync(_cfg(site_key=""), lambda p: {"success": True})
        self.assertFalse(sync.configured)

    def test_applies_changed_telegram_fields(self) -> None:
        applied: list[dict] = []

        def apply(payload: dict) -> dict:
            applied.append(payload)
            return {"success": True}

        session = MagicMock()
        session.get.return_value.ok = True
        session.get.return_value.json.return_value = [
            {
                "site_key": "site-abc",
                "telegram_bot_token": "123:ABC",
                "telegram_chat_id": "1217328195",
                "telegram_group_chat_id": "-100777",
            }
        ]
        sync = CloudConfigSync(_cfg(), apply, session=session)
        result = sync.poll_once()
        self.assertTrue(result["applied"])
        self.assertEqual(
            applied[0],
            {
                "telegram_bot_token": "123:ABC",
                "telegram_chat_id": "1217328195",
                "telegram_group_chat_id": "-100777",
            },
        )

    def test_second_poll_is_noop_when_unchanged(self) -> None:
        applied: list[dict] = []
        session = MagicMock()
        session.get.return_value.ok = True
        session.get.return_value.json.return_value = [
            {"telegram_chat_id": "1217328195", "telegram_group_chat_id": "-100777"}
        ]
        sync = CloudConfigSync(
            _cfg(), lambda p: (applied.append(p), {"success": True})[1], session=session
        )
        self.assertTrue(sync.poll_once()["applied"])
        second = sync.poll_once()
        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "no change")
        self.assertEqual(len(applied), 1)

    def test_only_changed_key_reapplied(self) -> None:
        applied: list[dict] = []
        session = MagicMock()
        session.get.return_value.ok = True
        session.get.return_value.json.return_value = [
            {"telegram_chat_id": "111", "telegram_group_chat_id": "-100"}
        ]
        sync = CloudConfigSync(
            _cfg(), lambda p: (applied.append(p), {"success": True})[1], session=session
        )
        sync.poll_once()
        # group changes, chat stays the same
        session.get.return_value.json.return_value = [
            {"telegram_chat_id": "111", "telegram_group_chat_id": "-200"}
        ]
        result = sync.poll_once()
        self.assertTrue(result["applied"])
        self.assertEqual(applied[1], {"telegram_group_chat_id": "-200"})

    def test_http_error_is_recorded(self) -> None:
        session = MagicMock()
        session.get.return_value.ok = False
        session.get.return_value.status_code = 404
        session.get.return_value.text = "Not found"
        sync = CloudConfigSync(_cfg(), lambda p: {"success": True}, session=session)
        result = sync.poll_once()
        self.assertFalse(result["applied"])
        self.assertIn("404", sync.last_error)

    def test_network_exception_swallowed(self) -> None:
        session = MagicMock()
        session.get.side_effect = RuntimeError("offline")
        sync = CloudConfigSync(_cfg(), lambda p: {"success": True}, session=session)
        result = sync.poll_once()
        self.assertFalse(result["applied"])
        self.assertIn("offline", sync.last_error)

    def test_apply_failure_not_marked_applied(self) -> None:
        session = MagicMock()
        session.get.return_value.ok = True
        session.get.return_value.json.return_value = [{"telegram_chat_id": "555"}]
        sync = CloudConfigSync(
            _cfg(),
            lambda p: {"success": False, "error": "bad token"},
            session=session,
        )
        result = sync.poll_once()
        self.assertFalse(result["applied"])
        self.assertEqual(sync.last_error, "bad token")

    def test_apply_exception_swallowed(self) -> None:
        session = MagicMock()
        session.get.return_value.ok = True
        session.get.return_value.json.return_value = [{"telegram_chat_id": "555"}]

        def boom(payload: dict) -> dict:
            raise RuntimeError("apply blew up")

        sync = CloudConfigSync(_cfg(), boom, session=session)
        result = sync.poll_once()
        self.assertFalse(result["applied"])
        self.assertIn("apply blew up", sync.last_error)

    def test_empty_rows_is_noop(self) -> None:
        session = MagicMock()
        session.get.return_value.ok = True
        session.get.return_value.json.return_value = []
        sync = CloudConfigSync(_cfg(), lambda p: {"success": True}, session=session)
        result = sync.poll_once()
        self.assertFalse(result["applied"])

    def test_status_reports_config(self) -> None:
        sync = CloudConfigSync(_cfg(), lambda p: {"success": True})
        status = sync.status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["site_key"], "site-abc")


if __name__ == "__main__":
    unittest.main()
