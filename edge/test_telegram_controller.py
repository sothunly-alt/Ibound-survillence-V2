"""RBAC + /name parsing tests for telegram_controller (no live Telegram)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from telegram_controller import (
    DENIED,
    TelegramController,
    parse_name_args,
)
from visitor_registry import get_visitor_display_name


class ParseNameArgsTests(unittest.TestCase):
    def test_alias_only_defaults_tier(self) -> None:
        parsed = parse_name_args("visitor_a1f3 Lok Chumteav Sophy")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        vid, alias, tier, notes = parsed
        self.assertEqual(vid, "visitor_a1f3")
        self.assertEqual(alias, "Lok Chumteav Sophy")
        self.assertEqual(tier, "Standard")
        self.assertEqual(notes, "")

    def test_tier_and_notes(self) -> None:
        parsed = parse_name_args("visitor_x Alice | Gold | Prefers Room 3")
        self.assertEqual(
            parsed,
            ("visitor_x", "Alice", "Gold", "Prefers Room 3"),
        )

    def test_invalid_missing_alias(self) -> None:
        self.assertIsNone(parse_name_args("visitor_only"))


class TelegramControllerRbacTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "events.db"
        self.session = MagicMock()
        self.ctrl = TelegramController(
            token="test-token",
            staff_chat_id="111",
            owner_chat_id="222",
            db_path=self.db_path,
            branch_id="champei-pp-01",
            session=self.session,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stranger_ignored(self) -> None:
        reply = self.ctrl.handle_message("999", "/scorecard")
        self.assertIsNone(reply)
        reply2 = self.ctrl.handle_message("999", "/name visitor_a Alice")
        self.assertIsNone(reply2)

    def test_staff_denied_scorecard_and_churn(self) -> None:
        self.assertEqual(self.ctrl.handle_message("111", "/scorecard"), DENIED)
        self.assertEqual(self.ctrl.handle_message("111", "/churn"), DENIED)
        self.assertEqual(self.ctrl.handle_message("111", "/weekly"), DENIED)

    def test_owner_permitted_scorecard(self) -> None:
        reply = self.ctrl.handle_message("222", "/scorecard")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("DAILY OPERATIONS SCORECARD", reply)
        self.assertIn("champei-pp-01", reply)

    def test_owner_permitted_churn(self) -> None:
        reply = self.ctrl.handle_message("222", "/churn")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("SILENT CHURN WATCHLIST", reply)

    def test_owner_permitted_weekly_brief(self) -> None:
        reply = self.ctrl.handle_message("222", "/weekly")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("WEEKLY EXECUTIVE BUSINESS REPORT", reply)
        self.assertIn("champei-pp-01", reply)

    def test_staff_name_upsert(self) -> None:
        reply = self.ctrl.handle_message(
            "111",
            "/name visitor_a1f3 Lok Chumteav Sophy | Gold | Prefers Room 3",
        )
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("✅ Saved profile", reply)
        self.assertIn("visitor_a1f3", reply)
        self.assertIn("Lok Chumteav Sophy", reply)
        self.assertIn("Gold", reply)

        info = get_visitor_display_name("visitor_a1f3", db_path=self.db_path)
        self.assertTrue(info["is_named"])
        self.assertEqual(info["display_name"], "Lok Chumteav Sophy")
        self.assertEqual(info["tier"], "Gold")
        self.assertEqual(info["notes"], "Prefers Room 3")

    def test_info_after_name(self) -> None:
        self.ctrl.handle_message("111", "/name visitor_b Bob | Standard |")
        reply = self.ctrl.handle_message("111", "/info visitor_b")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("Bob", reply)
        self.assertIn("Standard", reply)

    def test_staff_help_omits_owner_commands(self) -> None:
        reply = self.ctrl.handle_message("111", "/help")
        assert reply is not None
        self.assertIn("/name", reply)
        self.assertNotIn("/scorecard", reply)
        self.assertNotIn("/weekly", reply)

    def test_owner_help_includes_owner_commands(self) -> None:
        reply = self.ctrl.handle_message("222", "/help")
        assert reply is not None
        self.assertIn("/scorecard", reply)
        self.assertIn("/churn", reply)
        self.assertIn("/weekly", reply)
        self.assertIn("/alertchat", reply)


class AlertChatCommandTests(unittest.TestCase):
    """Remote admin: retarget alerts from Telegram without laptop access."""

    def _ctrl(self, hook):
        return TelegramController(
            token="test-token",
            staff_chat_id="111",
            owner_chat_id="222",
            session=MagicMock(),
            on_set_alert_chat=hook,
        )

    def test_staff_denied(self) -> None:
        ctrl = self._ctrl(lambda kind, value: {"success": True})
        self.assertEqual(ctrl.handle_message("111", "/alertchat group -100"), DENIED)

    def test_group_set(self) -> None:
        calls: list[tuple[str, str]] = []
        ctrl = self._ctrl(
            lambda kind, value: (calls.append((kind, value)), {"success": True})[1]
        )
        reply = ctrl.handle_message("222", "/alertchat group -1001234567890")
        self.assertEqual(calls, [("group", "-1001234567890")])
        assert reply is not None
        self.assertIn("Group alert chat set", reply)

    def test_owner_set(self) -> None:
        calls: list[tuple[str, str]] = []
        ctrl = self._ctrl(
            lambda kind, value: (calls.append((kind, value)), {"success": True})[1]
        )
        reply = ctrl.handle_message("222", "/alertchat owner 1217328195")
        self.assertEqual(calls, [("owner", "1217328195")])
        assert reply is not None
        self.assertIn("Owner alert chat set", reply)

    def test_off_clears(self) -> None:
        calls: list[tuple[str, str]] = []
        ctrl = self._ctrl(
            lambda kind, value: (calls.append((kind, value)), {"success": True})[1]
        )
        reply = ctrl.handle_message("222", "/alertchat group off")
        self.assertEqual(calls, [("group", "off")])
        assert reply is not None
        self.assertIn("cleared", reply)

    def test_bad_kind_rejected(self) -> None:
        ctrl = self._ctrl(lambda kind, value: {"success": True})
        reply = ctrl.handle_message("222", "/alertchat everyone 5")
        assert reply is not None
        self.assertIn("must be 'owner' or 'group'", reply)

    def test_missing_args_usage(self) -> None:
        ctrl = self._ctrl(lambda kind, value: {"success": True})
        reply = ctrl.handle_message("222", "/alertchat")
        assert reply is not None
        self.assertIn("Usage", reply)

    def test_failure_surfaces_error(self) -> None:
        ctrl = self._ctrl(lambda kind, value: {"success": False, "error": "bad id"})
        reply = ctrl.handle_message("222", "/alertchat group x")
        assert reply is not None
        self.assertIn("bad id", reply)

    def test_no_hook_reports_unwired(self) -> None:
        ctrl = TelegramController(
            token="test-token", staff_chat_id="111", owner_chat_id="222", session=MagicMock()
        )
        reply = ctrl.handle_message("222", "/alertchat group -1")
        assert reply is not None
        self.assertIn("not wired", reply)


class TelegramControllerPairingForwardTests(unittest.TestCase):
    """The controller is the sole getUpdates owner in the local install, so it
    must forward /start pairing updates to the shared link service rather than
    swallow them as unknown commands."""

    def test_start_update_forwarded_to_link_service(self) -> None:
        forwarded: list[dict] = []

        class FakeLink:
            def handle_pairing_update(self, update: dict) -> bool:
                forwarded.append(update)
                return True

        ctrl = TelegramController(
            token="test-token", session=MagicMock(), link_service=FakeLink()
        )
        handled = ctrl._dispatch_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/start abc123",
                    "chat": {"id": 42},
                    "from": {"id": 7},
                },
            }
        )
        self.assertTrue(handled)
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0]["update_id"], 1)

    def test_start_is_ignored_without_link_service(self) -> None:
        ctrl = TelegramController(token="test-token", session=MagicMock())
        with patch.object(ctrl, "send_reply") as mock_reply:
            handled = ctrl._dispatch_update(
                {"update_id": 2, "message": {"text": "/start", "chat": {"id": 42}}}
            )
        self.assertFalse(handled)
        mock_reply.assert_not_called()

    def test_link_service_error_is_recorded_not_raised(self) -> None:
        class BoomLink:
            def handle_pairing_update(self, update: dict) -> bool:
                raise RuntimeError("telegram down")

        ctrl = TelegramController(
            token="test-token", session=MagicMock(), link_service=BoomLink()
        )
        handled = ctrl._dispatch_update(
            {"update_id": 3, "message": {"text": "/start x", "chat": {"id": 1}}}
        )
        self.assertFalse(handled)
        self.assertIn("telegram down", ctrl.last_error)

    def test_run_loop_clears_webhook_before_polling(self) -> None:
        ctrl = TelegramController(token="test-token", session=MagicMock())
        stop = threading.Event()

        def fake_poll(timeout: int = 10) -> int:
            stop.set()
            return 0

        with patch.object(ctrl, "poll_once", side_effect=fake_poll) as mock_poll, patch(
            "telegram_controller.ensure_get_updates_ready"
        ) as mock_ensure:
            ctrl.run_loop(stop)
        mock_ensure.assert_called_once_with("test-token")
        self.assertTrue(mock_poll.called)


if __name__ == "__main__":
    unittest.main()
