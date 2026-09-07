from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram_link import TelegramLinkService


class TelegramLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = TelegramLinkService()
        self.svc._token = "123:ABC"
        self.svc._bot_username = "GarageBot"

    def test_begin_link_builds_deep_link(self) -> None:
        result = self.svc.begin_link("11111111-2222-3333-4444-555555555555", "Alex")
        self.assertTrue(result["ok"])
        self.assertTrue(result["deep_link"].startswith("https://t.me/GarageBot?start="))
        self.assertTrue(result["app_link"].startswith("tg://resolve?domain=GarageBot&start="))
        self.assertIn(result["code"], self.svc._pending)

    def test_start_payload_links_chat(self) -> None:
        begun = self.svc.begin_link("user-42", "Alex")
        code = begun["code"]
        handled = self.svc._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": f"/start {code}",
                    "chat": {"id": 998877},
                    "from": {"id": 55, "username": "alex_op"},
                },
            }
        )
        self.assertTrue(handled)
        link = self.svc.consume_link_for_user("user-42")
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link["chat_id"], "998877")
        self.assertEqual(link["telegram_username"], "alex_op")

    def test_bare_start_uses_single_pending(self) -> None:
        self.svc.begin_link("user-7", "Sam")
        handled = self.svc._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "/start",
                    "chat": {"id": 42},
                    "from": {"id": 9},
                },
            }
        )
        self.assertTrue(handled)
        link = self.svc.consume_link_for_user("user-7")
        self.assertEqual(link["chat_id"], "42")

    @patch("telegram_link.requests.get")
    def test_poll_once_advances_offset(self, mock_get: MagicMock) -> None:
        self.svc.begin_link("user-1", "Alex")
        code = next(iter(self.svc._pending))
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 10,
                    "message": {
                        "text": f"/start {code}",
                        "chat": {"id": 77},
                        "from": {"id": 1},
                    },
                }
            ],
        }
        with patch.object(self.svc, "_reply"):
            handled = self.svc.poll_once()
        self.assertEqual(handled, 1)
        self.assertEqual(self.svc._offset, 11)


if __name__ == "__main__":
    unittest.main()
