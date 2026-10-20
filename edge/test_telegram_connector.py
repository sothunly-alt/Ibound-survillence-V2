"""One-click Telegram connector: token validation + orchestration (no live Telegram)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram_connector import LOCAL_OPERATOR_ID, TelegramConnector, validate_bot_token


class _Resp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 400

    def json(self):
        return self._payload


def _me_payload(username: str = "my_bot", name: str = "My Bot"):
    return {"ok": True, "result": {"id": 42, "username": username, "first_name": name}}


class ValidateBotTokenTests(unittest.TestCase):
    def test_empty_token_rejected(self) -> None:
        result = validate_bot_token("")
        self.assertFalse(result["ok"])
        self.assertIn("token", result["error"].lower())

    def test_malformed_token_rejected_locally(self) -> None:
        result = validate_bot_token("not-a-token")
        self.assertFalse(result["ok"])
        self.assertIn("123456", result["error"])

    @patch("telegram_connector.connect.requests.get")
    def test_valid_token_returns_identity(self, mock_get) -> None:
        mock_get.return_value = _Resp(_me_payload())
        result = validate_bot_token("123:abc")
        self.assertTrue(result["ok"])
        self.assertEqual(result["bot_username"], "my_bot")
        self.assertEqual(result["bot_id"], 42)
        self.assertEqual(result["bot_name"], "My Bot")

    @patch("telegram_connector.connect.requests.get")
    def test_unauthorized_token_reports_telegram_error(self, mock_get) -> None:
        mock_get.return_value = _Resp({"ok": False, "description": "Unauthorized"}, status=401)
        result = validate_bot_token("123:bad")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Unauthorized")

    @patch("telegram_connector.connect.requests.get")
    def test_network_error_reports_cleanly(self, mock_get) -> None:
        mock_get.side_effect = OSError("boom")
        result = validate_bot_token("123:abc")
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


class TelegramConnectorConnectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.links = MagicMock()
        self.saved: list[dict] = []
        self.connector = TelegramConnector(
            self.links, lambda updates: self.saved.append(updates) or {"success": True}
        )

    @patch("telegram_connector.connect.requests.get")
    def test_connect_validates_saves_and_begins_link(self, mock_get) -> None:
        mock_get.return_value = _Resp(_me_payload())
        self.links.begin_link.return_value = {
            "ok": True,
            "code": "abc123",
            "bot_username": "my_bot",
            "deep_link": "https://t.me/my_bot?start=abc123",
            "app_link": "tg://resolve?domain=my_bot&start=abc123",
            "instructions": "Open @my_bot and tap Start.",
        }
        result = self.connector.connect("123:abc")
        self.assertTrue(result["ok"])
        self.assertEqual(result["bot_username"], "my_bot")
        self.assertEqual(result["bot_name"], "My Bot")
        self.assertEqual(self.saved, [{"telegram_bot_token": "123:abc"}])
        self.links.begin_link.assert_called_once_with(LOCAL_OPERATOR_ID, "Operator")

    @patch("telegram_connector.connect.requests.get")
    def test_connect_rejects_bad_token_before_saving(self, mock_get) -> None:
        mock_get.return_value = _Resp({"ok": False, "description": "Unauthorized"}, status=401)
        result = self.connector.connect("123:bad")
        self.assertFalse(result["ok"])
        self.assertEqual(self.saved, [])
        self.links.begin_link.assert_not_called()

    @patch("telegram_connector.connect.requests.get")
    def test_connect_uses_provided_user(self, mock_get) -> None:
        mock_get.return_value = _Resp(_me_payload())
        self.links.begin_link.return_value = {"ok": True, "code": "x", "bot_username": "my_bot"}
        result = self.connector.connect("123:abc", user_id="user-1", display_name="Alice")
        self.links.begin_link.assert_called_once_with("user-1", "Alice")
        self.assertEqual(result["user_id"], "user-1")

    @patch("telegram_connector.connect.requests.get")
    def test_connect_opens_deep_link_when_requested(self, mock_get) -> None:
        mock_get.return_value = _Resp(_me_payload())
        self.links.begin_link.return_value = {
            "ok": True,
            "code": "x",
            "bot_username": "my_bot",
            "app_link": "tg://app",
            "deep_link": "https://t.me/x",
        }
        opened: list[str] = []
        result = self.connector.connect(
            "123:abc", open_client=True, open_url=lambda url: opened.append(url) or True
        )
        self.assertTrue(result["opened_on_host"])
        self.assertEqual(opened, ["tg://app"])

    @patch("telegram_connector.connect.requests.get")
    def test_connect_falls_back_to_web_link_when_app_fails(self, mock_get) -> None:
        mock_get.return_value = _Resp(_me_payload())
        self.links.begin_link.return_value = {
            "ok": True,
            "code": "x",
            "bot_username": "my_bot",
            "app_link": "tg://app",
            "deep_link": "https://t.me/x",
        }
        opened: list[str] = []

        def open_url(url: str) -> bool:
            opened.append(url)
            return url.startswith("https://")

        result = self.connector.connect("123:abc", open_client=True, open_url=open_url)
        self.assertTrue(result["opened_on_host"])
        self.assertEqual(opened, ["tg://app", "https://t.me/x"])


class CredentialPriorityTests(unittest.TestCase):
    """UI-pasted config values win; TELEGRAM_* env vars are fallback only."""

    def test_config_token_and_chat_win_over_env(self) -> None:
        from telegram_out import resolve_telegram_credentials

        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "env-token", "TELEGRAM_CHAT_ID": "999"},
        ):
            token, chat = resolve_telegram_credentials("cfg-token", "111")
            self.assertEqual(token, "cfg-token")
            self.assertEqual(chat, "111")

    def test_env_used_when_config_empty(self) -> None:
        from telegram_out import resolve_telegram_credentials

        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "env-token", "TELEGRAM_CHAT_ID": "999"},
        ):
            token, chat = resolve_telegram_credentials("", "")
            self.assertEqual(token, "env-token")
            self.assertEqual(chat, "999")

    def test_group_chat_config_wins_over_env(self) -> None:
        from telegram_out import resolve_group_chat_id

        with patch.dict(os.environ, {"TELEGRAM_GROUP_CHAT_ID": "-100111"}):
            self.assertEqual(resolve_group_chat_id("-100222"), "-100222")
            self.assertEqual(resolve_group_chat_id(""), "-100111")


if __name__ == "__main__":
    unittest.main()
