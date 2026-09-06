"""Tier 2 Cloud Vision AI Auditor for Inbound Surveillance."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import requests

logger = logging.getLogger("ai_auditor")


@dataclass
class AIAuditVerdict:
    bay_id: str
    technician_id: str
    action: str
    category: str
    confidence: float
    explanation: str
    is_work_activity: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    crop_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "bay_id": self.bay_id,
            "technician_id": self.technician_id,
            "action": self.action,
            "category": self.category,
            "confidence": round(self.confidence, 2),
            "explanation": self.explanation,
            "is_work_activity": self.is_work_activity,
            "timestamp": self.timestamp,
            "crop_path": self.crop_path,
        }


class TokenSaverGate:
    def __init__(self, duration_threshold: float = 5.0, cooldown_seconds: float = 180.0):
        self.duration_threshold = duration_threshold
        self.cooldown_seconds = cooldown_seconds
        self._pattern_starts: dict[str, float] = {}
        self._last_audit_times: dict[str, float] = {}

    def evaluate(self, bay_id: str, pattern_matched: bool, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        if not pattern_matched:
            self._pattern_starts.pop(bay_id, None)
            return False
        if bay_id not in self._pattern_starts:
            self._pattern_starts[bay_id] = now
            return False
        if (now - self._pattern_starts[bay_id]) < self.duration_threshold:
            return False
        last_audit = self._last_audit_times.get(bay_id)
        if last_audit is not None and (now - last_audit) < self.cooldown_seconds:
            return False
        self._last_audit_times[bay_id] = now
        self._pattern_starts.pop(bay_id, None)
        return True

    def reset_bay(self, bay_id: str) -> None:
        self._pattern_starts.pop(bay_id, None)
        self._last_audit_times.pop(bay_id, None)


def extract_dual_crops(frame: np.ndarray, keypoints: list | None, bbox: tuple[int, int, int, int] | None = None, max_dim: int = 512, jpeg_quality: int = 80) -> tuple[str, str, np.ndarray, np.ndarray]:
    h, w = frame.shape[:2]
    if bbox:
        x1, y1, x2, y2 = bbox
        pad_x, pad_y = int((x2 - x1) * 0.25), int((y2 - y1) * 0.25)
        context_img = frame[max(0, y1 - pad_y):min(h, y2 + pad_y), max(0, x1 - pad_x):min(w, x2 + pad_x)].copy()
    else:
        context_img = frame.copy()

    hand_img = None
    if keypoints and len(keypoints) >= 11:
        wrists = [(int(keypoints[i][0]), int(keypoints[i][1])) for i in (9, 10) if len(keypoints[i]) >= 3 and keypoints[i][2] >= 0.25]
        if wrists:
            avg_x, avg_y = int(sum(p[0] for p in wrists) / len(wrists)), int(sum(p[1] for p in wrists) / len(wrists))
            bs = int(min(w, h) * 0.22)
            hand_img = frame[max(0, avg_y - bs):min(h, avg_y + bs), max(0, avg_x - bs):min(w, avg_x + bs)].copy()

    if hand_img is None or hand_img.size == 0:
        hand_img = context_img.copy()

    def _b64(im: np.ndarray) -> str:
        ih, iw = im.shape[:2]
        if max(ih, iw) > max_dim:
            sc = max_dim / max(ih, iw)
            im = cv2.resize(im, (int(iw * sc), int(ih * sc)), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        return base64.b64encode(buf).decode("utf-8")

    return _b64(hand_img), _b64(context_img), hand_img, context_img


def _load_env_or_config() -> str:
    if os.environ.get("FIREWORKS_API_KEY"):
        return os.environ["FIREWORKS_API_KEY"].strip().strip('"').strip("'")
    for p in (Path(__file__).parent / ".env", Path(__file__).parent.parent / ".env"):
        if p.is_file():
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("FIREWORKS_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
    cfg_path = Path(__file__).parent / "config.yaml"
    if cfg_path.is_file():
        try:
            import yaml
            data = yaml.safe_load(cfg_path.read_text()) or {}
            if "fireworks_api_key" in data:
                return str(data["fireworks_api_key"]).strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


class FireworksVLMClient:
    def __init__(self, api_key: str | None = None, model: str = "accounts/fireworks/models/deepseek-v4-flash-vision-exp", timeout: float = 15.0):
        self.api_key = api_key or _load_env_or_config()
        self.model = model
        self.timeout = timeout
        self.endpoint = "https://api.fireworks.ai/inference/v1/chat/completions"

    def audit_activity(self, b64_hand: str, b64_context: str, bay_id: str, technician_id: str, context_history: str = "") -> AIAuditVerdict:
        if not self.api_key:
            return self._mock_verdict(bay_id, technician_id)
        prompt = f"Analyze technician {technician_id} in {bay_id}. Is it DIAGNOSTIC_TOOL, PHONE_USAGE, DOCUMENT_READING, RESTING, or MANUAL_WORK? Context: {context_history}. Respond ONLY in JSON with keys: action, category, confidence, explanation, is_work_activity."
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_hand}"}},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_context}"}},
                    ],
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
        }
        try:
            r = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            res_json = r.json()
            msg = res_json["choices"][0]["message"]
            raw = msg.get("content", "") or msg.get("reasoning_content", "")
            match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
            raw_json = match.group(0) if match else raw
            data = json.loads(raw_json)
            action = data.get("action", "UNKNOWN")
            cat = data.get("category", "UNKNOWN")
            conf = float(data.get("confidence", 0.85))
            expl = str(data.get("explanation", ""))
            is_work = bool(data.get("is_work_activity", cat == "ACTIVE_WORK"))
            return AIAuditVerdict(bay_id=bay_id, technician_id=technician_id, action=action, category=cat, confidence=conf, explanation=expl, is_work_activity=is_work)
        except Exception as e:
            return AIAuditVerdict(bay_id=bay_id, technician_id=technician_id, action="UNKNOWN", category="UNKNOWN", confidence=0.0, explanation=f"Audit error: {e}", is_work_activity=True)

    def _mock_verdict(self, bay_id: str, technician_id: str) -> AIAuditVerdict:
        return AIAuditVerdict(bay_id=bay_id, technician_id=technician_id, action="PHONE_USAGE", category="DISTRACTION", confidence=0.92, explanation="Mock audit: holding smartphone.", is_work_activity=False)


class AIAuditorQueue:
    def __init__(self, vlm_client: FireworksVLMClient | None = None, gate: TokenSaverGate | None = None, max_workers: int = 2, on_verdict_callback: Callable[[AIAuditVerdict], None] | None = None, save_crops_dir: Path | None = None):
        self.vlm_client = vlm_client or FireworksVLMClient()
        self.gate = gate or TokenSaverGate()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ai_auditor")
        self.on_verdict_callback = on_verdict_callback
        self.save_crops_dir = save_crops_dir
        self.latest_verdicts: dict[str, AIAuditVerdict] = {}

    def maybe_audit_bay(self, bay_id: str, technician_id: str, frame: np.ndarray, keypoints: list | None, bbox: tuple[int, int, int, int] | None = None, pattern_matched: bool = False, context_history: str = "", now: float | None = None) -> bool:
        if not self.gate.evaluate(bay_id, pattern_matched, now):
            return False
        b64_h, b64_c, hand_img, _ = extract_dual_crops(frame, keypoints, bbox)
        crop_path = None
        if self.save_crops_dir:
            self.save_crops_dir.mkdir(parents=True, exist_ok=True)
            crop_path = str(self.save_crops_dir / f"audit_{bay_id}_{int(time.time()*1000)}.jpg")
            cv2.imwrite(crop_path, hand_img)
        self.executor.submit(self._task, b64_h, b64_c, bay_id, technician_id, context_history, crop_path)
        return True

    def _task(self, b64_h, b64_c, bay_id, tech_id, hist, crop_path):
        try:
            v = self.vlm_client.audit_activity(b64_h, b64_c, bay_id, tech_id, hist)
            v.crop_path = crop_path
            self.latest_verdicts[bay_id] = v
            if self.on_verdict_callback:
                self.on_verdict_callback(v)
        except Exception as e:
            logger.error(f"Task error: {e}")

    def get_latest_verdict(self, bay_id: str) -> AIAuditVerdict | None:
        return self.latest_verdicts.get(bay_id)

    def shutdown(self):
        self.executor.shutdown(wait=False)
