from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from telegram_out import TelegramOut

API = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class PendingLink:
    code: str
    user_id: str
    display_name: str
    created_at: float = field(default_factory=time.time)


@dataclass
class LinkedChat:
    chat_id: str
    user_id: str
    display_name: str
    telegram_user_id: str = ""
    telegram_username: str = ""
    linked_at: float = field(default_factory=time.time)
    code: str = ""


class TelegramLinkService:
    """Poll getUpdates so an signed-in operator can /start the bot and auto-save chat_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = ""
        self._bot_username = ""
        self._bot_id: int | None = None
        self._offset = 0
        self._pending: dict[str, PendingLink] = {}
        self._last_link: LinkedChat | None = None
        self._last_error = ""
        self._poll_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def configure(self, token: str) -> None:
        token = (token or "").strip()
        with self._lock:
            if token == self._token:
                return
            self._token = token
            self._bot_username = ""
            self._bot_id = None
            self._offset = 0
            self._last_error = ""
        if token:
            self.refresh_bot_identity()
            self.start()
        else:
            self.stop()

    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    def start(self) -> None:
        if not self.token:
            return
        with self._lock:
            if self._poll_thread and self._poll_thread.is_alive():
                return
            self._stop.clear()
            self._poll_thread = threading.Thread(
                target=self._poll_loop, name="telegram-link-poll", daemon=True
            )
            self._poll_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh_bot_identity(self) -> dict[str, Any]:
        token = self.token
        if not token:
            return {"ok": False, "error": "Bot token required."}
        try:
            response = requests.get(
                API.format(token=token, method="getMe"), timeout=15
            )
            data = response.json()
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        if not response.ok or not data.get("ok"):
            err = data.get("description") or f"HTTP {response.status_code}"
            with self._lock:
                self._last_error = err
            return {"ok": False, "error": err}
        result = data.get("result") or {}
        username = str(result.get("username") or "").strip()
        bot_id = result.get("id")
        with self._lock:
            self._bot_username = username
            self._bot_id = int(bot_id) if bot_id is not None else None
            self._last_error = ""
        return {
            "ok": True,
            "bot_username": username,
            "bot_id": bot_id,
            "bot_name": result.get("first_name") or "",
        }

    def begin_link(self, user_id: str, display_name: str = "") -> dict[str, Any]:
        uid = str(user_id or "").strip()
        if not uid:
            return {"ok": False, "error": "Signed-in account id required."}
        token = self.token
        if not token:
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured on the engine."}
        if not self._bot_username:
            identity = self.refresh_bot_identity()
            if not identity.get("ok"):
                return identity
        code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
        pending = PendingLink(
            code=code,
            user_id=uid,
            display_name=str(display_name or "").strip() or "Operator",
        )
        with self._lock:
            # One active link attempt per account.
            self._pending = {
                key: value
                for key, value in self._pending.items()
                if value.user_id != uid and (time.time() - value.created_at) < 900
            }
            self._pending[code] = pending
            username = self._bot_username
        deep_link = f"https://t.me/{username}?start={code}" if username else ""
        app_link = (
            f"tg://resolve?domain={username}&start={code}" if username else ""
        )
        return {
            "ok": True,
            "code": code,
            "bot_username": username,
            "deep_link": deep_link,
            "app_link": app_link,
            "expires_in_seconds": 900,
            "instructions": (
                f"Open @{username} in Telegram and tap Start, or send /start {code}. "
                "Your chat id is saved to this account automatically."
            ),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            pending = [
                {
                    "code": item.code,
                    "user_id": item.user_id,
                    "display_name": item.display_name,
                    "age_seconds": int(time.time() - item.created_at),
                }
                for item in self._pending.values()
            ]
            link = self._last_link
            return {
                "token_configured": bool(self._token),
                "bot_username": self._bot_username,
                "bot_id": self._bot_id,
                "polling": bool(self._poll_thread and self._poll_thread.is_alive() and self._token),
                "pending": pending,
                "last_error": self._last_error,
                "linked": None
                if link is None
                else {
                    "chat_id": link.chat_id,
                    "user_id": link.user_id,
                    "display_name": link.display_name,
                    "telegram_user_id": link.telegram_user_id,
                    "telegram_username": link.telegram_username,
                    "linked_at": link.linked_at,
                    "code": link.code,
                },
            }

    def consume_link_for_user(self, user_id: str) -> dict[str, Any] | None:
        uid = str(user_id or "").strip()
        with self._lock:
            link = self._last_link
            if link is None or link.user_id != uid:
                return None
            self._last_link = None
            return {
                "chat_id": link.chat_id,
                "user_id": link.user_id,
                "display_name": link.display_name,
                "telegram_user_id": link.telegram_user_id,
                "telegram_username": link.telegram_username,
                "linked_at": link.linked_at,
            }

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if not self.token:
                time.sleep(1.0)
                continue
            try:
                self.poll_once()
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                time.sleep(2.0)
            self._stop.wait(1.0)

    def poll_once(self) -> int:
        token = self.token
        if not token:
            return 0
        with self._lock:
            offset = self._offset
        try:
            response = requests.get(
                API.format(token=token, method="getUpdates"),
                params={"offset": offset, "timeout": 0, "limit": 20},
                timeout=20,
            )
            data = response.json()
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            return 0
        if not response.ok or not data.get("ok"):
            err = data.get("description") or f"HTTP {response.status_code}"
            with self._lock:
                self._last_error = err
            return 0
        updates = data.get("result") or []
        handled = 0
        for update in updates:
            update_id = int(update.get("update_id") or 0)
            with self._lock:
                self._offset = max(self._offset, update_id + 1)
            if self._handle_update(update):
                handled += 1
        with self._lock:
            self._last_error = ""
        return handled

    def _handle_update(self, update: dict[str, Any]) -> bool:
        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text") or "").strip()
        if not text.lower().startswith("/start"):
            return False
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            return False
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        # Deep-link payloads sometimes arrive as /start@BotName CODE
        if payload.startswith("@"):
            bits = payload.split(maxsplit=1)
            payload = bits[1].strip() if len(bits) > 1 else ""

        pending: PendingLink | None = None
        with self._lock:
            if payload and payload in self._pending:
                pending = self._pending.pop(payload)
            elif not payload and len(self._pending) == 1:
                only_code = next(iter(self._pending))
                pending = self._pending.pop(only_code)
            elif payload:
                # Accept UUID-without-dashes payloads matching a pending user.
                compact = payload.replace("-", "").lower()
                for code, item in list(self._pending.items()):
                    if item.user_id.replace("-", "").lower() == compact:
                        pending = self._pending.pop(code)
                        break

        if pending is None:
            self._reply(
                chat_id,
                "Open Integrations in Inbound Surveillance and tap Connect Telegram, "
                "then press Start here so we can link this chat to your account.",
            )
            return False

        username = str(from_user.get("username") or "").strip()
        tg_user_id = str(from_user.get("id") or "").strip()
        link = LinkedChat(
            chat_id=chat_id,
            user_id=pending.user_id,
            display_name=pending.display_name,
            telegram_user_id=tg_user_id,
            telegram_username=username,
            code=pending.code,
        )
        with self._lock:
            self._last_link = link
        name = pending.display_name
        self._reply(
            chat_id,
            f"Linked to {name}. Garage alerts and daily scorecards will arrive here.",
        )
        return True

    def _reply(self, chat_id: str, text: str) -> None:
        token = self.token
        if not token or not chat_id:
            return
        try:
            requests.post(
                API.format(token=token, method="sendMessage"),
                data={"chat_id": chat_id, "text": text},
                timeout=20,
            )
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)


def apply_linked_chat(bot: TelegramOut, chat_id: str) -> TelegramOut:
    """Return a TelegramOut pointed at the newly linked chat."""
    return TelegramOut(bot.token if bot else "", chat_id)
