"""Full-body person Re-Identification (OSNet) with a CPU appearance fallback.

OSNet-x0.25 (~1.1 MB ONNX) yields a 512-d clothing/shape embedding that stays
stable when a technician turns their head or back to the camera. When the
ONNX file is missing, a 512-bin HSV histogram keeps identity binding alive
on air-gapped edge boxes.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import cv2
import numpy as np

from runtime import RuntimeProfile, apply_dnn_backend, resolve_runtime

OSNET_FILENAME = "osnet_x0_25_market1501.onnx"
OSNET_URLS = (
    "https://github.com/KaiyangZhou/deep-person-reid/releases/download/v1.4.0/osnet_x0_25_market1501.onnx",
    "https://huggingface.co/kaiyangzhou/osnet/resolve/main/osnet_x0_25_market1501.onnx",
)
FEATURE_DIM = 512
IMAGENET_MEAN = (0.485 * 255.0, 0.456 * 255.0, 0.406 * 255.0)
IMAGENET_STD = (0.229, 0.224, 0.225)


def _l2_normalize(feat: np.ndarray) -> np.ndarray:
    feat = np.asarray(feat, dtype=np.float32).flatten()
    norm = float(np.linalg.norm(feat))
    if norm <= 1e-6:
        return feat
    return feat / norm


def appearance_embedding(crop: np.ndarray) -> np.ndarray:
    """Pose-tolerant 512-d fallback from an 8×8×8 HSV histogram."""
    if crop is None or crop.size == 0:
        return np.zeros(FEATURE_DIM, dtype=np.float32)
    h, w = crop.shape[:2]
    if h < 8 or w < 8:
        return np.zeros(FEATURE_DIM, dtype=np.float32)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
    return _l2_normalize(hist)


def ensure_reid_model(models_dir: Path, download: bool = False) -> Path | None:
    models_dir.mkdir(parents=True, exist_ok=True)
    dest = models_dir / OSNET_FILENAME
    if dest.is_file() and dest.stat().st_size > 10_000:
        return dest
    if not download:
        return None
    for url in OSNET_URLS:
        try:
            print(f"[ReID] Downloading {OSNET_FILENAME}...")
            with urllib.request.urlopen(url, timeout=8) as resp, dest.open("wb") as handle:
                handle.write(resp.read())
            if dest.is_file() and dest.stat().st_size > 10_000:
                print(f"[ReID] Downloaded {dest.name}")
                return dest
            dest.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[ReID] Download skipped ({exc})")
            dest.unlink(missing_ok=True)
    return dest if dest.is_file() and dest.stat().st_size > 10_000 else None


class BodyReIDExtractor:
    def __init__(
        self,
        model_path: Path | None = None,
        profile: RuntimeProfile | None = None,
    ) -> None:
        self.profile = profile
        self.net = None
        if model_path is not None and Path(model_path).is_file():
            try:
                self.net = cv2.dnn.readNetFromONNX(str(model_path))
                apply_dnn_backend(self.net, profile)
                print(f"[ReID] OSNet ready ({Path(model_path).name})")
            except Exception as exc:
                print(f"[ReID] OSNet load failed ({exc}); using appearance fallback")
                self.net = None

    @property
    def using_osnet(self) -> bool:
        return self.net is not None

    def extract(self, frame: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray:
        x1, y1, x2, y2 = (max(0, int(v)) for v in bbox)
        if frame is None or frame.size == 0:
            return np.zeros(FEATURE_DIM, dtype=np.float32)
        h_f, w_f = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w_f, x2), min(h_f, y2)
        if x2 - x1 < 12 or y2 - y1 < 12:
            return np.zeros(FEATURE_DIM, dtype=np.float32)
        crop = frame[y1:y2, x1:x2]
        if self.net is None:
            return appearance_embedding(crop)
        blob = cv2.dnn.blobFromImage(
            crop,
            scalefactor=1.0 / 255.0,
            size=(128, 256),
            mean=IMAGENET_MEAN,
            swapRB=True,
            crop=False,
        )
        blob[:, 0, :, :] /= IMAGENET_STD[0]
        blob[:, 1, :, :] /= IMAGENET_STD[1]
        blob[:, 2, :, :] /= IMAGENET_STD[2]
        self.net.setInput(blob)
        feat = self.net.forward().flatten()
        return _l2_normalize(feat)

    @staticmethod
    def cosine_similarity(feat1: np.ndarray, feat2: np.ndarray) -> float:
        a = np.asarray(feat1, dtype=np.float32).flatten()
        b = np.asarray(feat2, dtype=np.float32).flatten()
        if a.size == 0 or b.size == 0 or a.size != b.size:
            return 0.0
        return float(np.dot(a, b))


class ReIDGallery:
    """Named staff appearance memory used when the face is turned away."""

    def __init__(self, max_per_name: int = 16) -> None:
        self.max_per_name = max_per_name
        self.embeddings: dict[str, list[np.ndarray]] = {}

    def remember(self, name: str, feat: np.ndarray | None) -> None:
        if not name or feat is None:
            return
        vec = _l2_normalize(feat)
        if float(np.linalg.norm(vec)) <= 1e-6:
            return
        bucket = self.embeddings.setdefault(name, [])
        bucket.append(vec)
        if len(bucket) > self.max_per_name:
            del bucket[0 : len(bucket) - self.max_per_name]

    def match(self, feat: np.ndarray | None, threshold: float) -> tuple[str | None, float]:
        if feat is None:
            return None, 0.0
        vec = _l2_normalize(feat)
        if float(np.linalg.norm(vec)) <= 1e-6:
            return None, 0.0
        best_name: str | None = None
        best_score = float(threshold)
        for name, emb_list in self.embeddings.items():
            if not emb_list:
                continue
            proto = _l2_normalize(np.mean(np.stack(emb_list, axis=0), axis=0))
            score = float(np.dot(proto, vec))
            if score > best_score:
                best_score = score
                best_name = name
        if best_name is None:
            return None, 0.0
        return best_name, best_score

    def clear(self) -> None:
        self.embeddings.clear()


def try_create_body_reid(cfg: dict | None = None, models_dir: Path | None = None) -> BodyReIDExtractor | None:
    cfg = cfg or {}
    profile = resolve_runtime(cfg)
    if not profile.reid_enabled:
        return None
    model_path: Path | None = None
    if models_dir is not None:
        model_path = ensure_reid_model(Path(models_dir), download=bool(cfg.get("reid_download", False)))
    else:
        try:
            from face_id import resolve_face_paths

            _faces, resolved_models = resolve_face_paths(cfg)
            model_path = ensure_reid_model(resolved_models, download=bool(cfg.get("reid_download", False)))
        except Exception as exc:
            print(f"[ReID] Model dir unavailable ({exc}); using appearance fallback")
    if model_path is None:
        print("[ReID] OSNet ONNX not found; using clothing-histogram fallback (place osnet_x0_25_market1501.onnx in models/)")
    try:
        return BodyReIDExtractor(model_path=model_path, profile=profile)
    except Exception as exc:
        print(f"[ReID] Disabled: {exc}")
        return None
