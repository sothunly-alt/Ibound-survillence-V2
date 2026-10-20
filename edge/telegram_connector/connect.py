from __future__ import annotations

from typing import Any, Callable

import requests

API = "https://api.telegram.org/bot{token}/{method}"

LOCAL_OPERATOR_ID = "local-operator"


def validate_bot_token(token: str) -> dict[str, Any]:
    """Verify a pasted bot token with getMe.

    Returns ``{"ok": True, "bot_username", "bot_id", "bot_name"}`` on success
    or ``{"ok": False, "error"}`` on failure. Never raises.
    """
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "Paste the bot token first (from @BotFather)."}
    if ":" not in token:
        return {
            "ok": False,
            "error": "That does not look like a Telegram bot token (expected format 123456:ABC-xyz).",
        }
    try:
        response = requests.get(API.format(token=token, method="getMe"), timeout=15)
        data = response.json() or {}
    except Exception as exc:
        return {"ok": False, "error": f"Could not reach Telegram: {exc}"}
    if not response.ok or not data.get("ok"):
        return {"ok": False, "error": data.get("description") or f"HTTP {response.status_code}"}
    result = data.get("result") or {}
    username = str(result.get("username") or "").strip()
    bot_id = result.get("id")
    return {
        "ok": True,
        "bot_username": username,
        "bot_id": bot_id,
        "bot_name": result.get("first_name") or "",
    }


class TelegramConnector:
    """One-click Telegram pairing: paste token, click Connect, customer taps Start.

    The engine wires this with its link service and a settings saver so a
    pasted token is validated, persisted to config.yaml (no env vars needed),
    and immediately ready for ``/start`` pairing. Works without a cloud
    account sign-in — an empty ``user_id`` falls back to ``LOCAL_OPERATOR_ID``.
    """

    def __init__(
        self,
        link_service: Any,
        save_settings: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._links = link_service
        self._save_settings = save_settings

    def connect(
        self,
        token: str,
        *,
        user_id: str = "",
        display_name: str = "",
        open_client: bool = False,
        open_url: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        """Validate ``token``, save it, and begin /start pairing.

        Returns the ``begin_link`` payload plus ``bot_name`` / ``user_id`` /
        ``opened_on_host``, or ``{"ok": False, "error"}``.
        """
        token = (token or "").strip()
        identity = validate_bot_token(token)
        if not identity.get("ok"):
            return identity
        self._save_settings({"telegram_bot_token": token})
        uid = str(user_id or "").strip() or LOCAL_OPERATOR_ID
        name = str(display_name or "").strip() or "Operator"
        result = self._links.begin_link(uid, name)
        if not result.get("ok"):
            return result
        result["bot_username"] = result.get("bot_username") or identity.get("bot_username", "")
        result["bot_name"] = identity.get("bot_name", "")
        result["user_id"] = uid
        opened = False
        if open_client and open_url is not None:
            opened = open_url(str(result.get("app_link") or ""))
            if not opened:
                opened = open_url(str(result.get("deep_link") or ""))
        result["opened_on_host"] = bool(opened)
        return result
