"""Pull per-site Telegram settings from the cloud so operators can retarget
alerts for a remote customer without touching the customer's machine.

Design
------
The edge engine is provisioned once with a ``cloud_sync`` block:

    cloud_sync:
      enabled: true
      url: https://<project>.supabase.co
      anon_key: <public anon key>
      site_key: <opaque per-site token>
      interval_seconds: 60

On each tick the engine calls the Supabase REST endpoint

    GET {url}/rest/v1/edge_site_config?site_key=eq.{site_key}&select=*

and, if the returned Telegram fields differ from the local config, applies
them through ``apply_hub_settings`` (which persists to config.yaml and
rebuilds the live Telegram bot). This makes "update the customer" a
zero-touch, operator-side action.

If ``cloud_sync`` is absent or disabled, nothing happens — the module is a
no-op, so existing installs are unaffected. It never raises into the engine
loop; all failures are logged and retried on the next tick.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import requests

TELEGRAM_KEYS = (
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_group_chat_id",
)


def _norm(value: Any) -> str:
    return str(value or "").strip()


class CloudConfigSync:
    """Background poller that applies cloud-managed Telegram settings."""

    def __init__(
        self,
        config: dict[str, Any] | None,
        apply_settings: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        session: requests.Session | None = None,
    ) -> None:
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled"))
        self.url = _norm(cfg.get("url") or cfg.get("supabase_url")).rstrip("/")
        self.anon_key = _norm(cfg.get("anon_key") or cfg.get("supabase_anon_key"))
        self.site_key = _norm(cfg.get("site_key"))
        self.table = _norm(cfg.get("table")) or "edge_site_config"
        try:
            self.interval = max(15.0, float(cfg.get("interval_seconds") or 60.0))
        except (TypeError, ValueError):
            self.interval = 60.0
        self._apply = apply_settings
        self._session = session or requests.Session()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_applied: dict[str, str] = {}
        self._last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.url and self.anon_key and self.site_key)

    @property
    def last_error(self) -> str:
        return self._last_error

    def _endpoint(self) -> str:
        return (
            f"{self.url}/rest/v1/{self.table}"
            f"?site_key=eq.{self.site_key}&select=*"
        )

    def fetch(self) -> dict[str, Any] | None:
        """Return the remote config row, or None on any failure."""
        if not self.configured:
            return None
        try:
            response = self._session.get(
                self._endpoint(),
                headers={
                    "apikey": self.anon_key,
                    "Authorization": f"Bearer {self.anon_key}",
                    "Accept": "application/json",
                },
                timeout=15,
            )
        except Exception as exc:
            self._last_error = str(exc)
            return None
        if not response.ok:
            self._last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            return None
        try:
            rows = response.json()
        except Exception as exc:
            self._last_error = str(exc)
            return None
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            self._last_error = "no row for site_key"
            return None
        self._last_error = ""
        return rows[0] if isinstance(rows[0], dict) else None

    def diff(self, row: dict[str, Any]) -> dict[str, Any]:
        """Return only the Telegram keys in row that changed since last apply."""
        changed: dict[str, Any] = {}
        for key in TELEGRAM_KEYS:
            if key not in row:
                continue
            value = _norm(row.get(key))
            if self._last_applied.get(key) != value:
                changed[key] = value
        return changed

    def poll_once(self, current: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch and apply changed Telegram settings. Returns a summary dict."""
        if not self.configured:
            return {"applied": False, "reason": "not configured"}
        row = self.fetch()
        if row is None:
            return {"applied": False, "reason": self._last_error or "fetch failed"}
        changed = self.diff(row)
        if not changed:
            return {"applied": False, "reason": "no change"}
        try:
            result = self._apply(changed) or {}
        except Exception as exc:
            self._last_error = str(exc)
            return {"applied": False, "reason": str(exc)}
        if not result.get("success"):
            self._last_error = str(result.get("error") or "apply failed")
            return {"applied": False, "reason": self._last_error}
        for key, value in changed.items():
            self._last_applied[key] = _norm(value)
        return {"applied": True, "changed": changed}

    def start(self) -> None:
        if not self.configured:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="cloud-config-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                summary = self.poll_once()
                if summary.get("applied"):
                    print(f"[cloud_sync] applied remote settings: {list(summary.get('changed', {}))}", flush=True)
            except Exception as exc:
                self._last_error = str(exc)
                print(f"[cloud_sync] poll error: {exc}", flush=True)
            self._stop.wait(self.interval)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "site_key": self.site_key,
            "interval_seconds": self.interval,
            "last_error": self._last_error,
            "applied_telegram_keys": sorted(self._last_applied.keys()),
        }
