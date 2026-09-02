"""Dual-target inference profile: local edge CPU vs central GPU server.

``runtime`` in config.yaml selects the execution backend:

- ``cpu`` / ``openvino`` — ONNX Runtime / OpenVINO on a mini-PC (no GPU).
- ``cuda`` / ``tensorrt`` — NVIDIA GPU for many simultaneous RTSP streams.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2
except Exception:  # pragma: no cover - OpenCV is a hard runtime dep
    cv2 = None  # type: ignore


RUNTIME_ALIASES = {
    "cpu": "cpu",
    "openvino": "openvino",
    "intel": "openvino",
    "cuda": "cuda",
    "gpu": "cuda",
    "tensorrt": "tensorrt",
    "trt": "tensorrt",
    "auto": "auto",
}

EDGE_WEIGHTS = "yolo11n-pose.pt"
SERVER_WEIGHTS = "yolo11s-pose.pt"


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    yolo_device: str | int
    weights_name: str
    dnn_backend: int
    dnn_target: int
    reid_enabled: bool
    track_max_age: int
    track_min_hits: int
    track_iou_threshold: float
    reid_match_threshold: float

    @property
    def is_gpu(self) -> bool:
        return self.name in ("cuda", "tensorrt")


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _dnn_constants() -> tuple[int, int, int, int]:
    if cv2 is None:
        return 0, 0, 0, 0
    backend_opencv = int(getattr(cv2.dnn, "DNN_BACKEND_OPENCV", 0))
    backend_cuda = int(getattr(cv2.dnn, "DNN_BACKEND_CUDA", backend_opencv))
    target_cpu = int(getattr(cv2.dnn, "DNN_TARGET_CPU", 0))
    target_cuda = int(getattr(cv2.dnn, "DNN_TARGET_CUDA", target_cpu))
    return backend_opencv, backend_cuda, target_cpu, target_cuda


def normalize_runtime_name(raw: object) -> str:
    name = str(raw or "cpu").strip().lower()
    return RUNTIME_ALIASES.get(name, "cpu")


def resolve_runtime(cfg: dict | None = None) -> RuntimeProfile:
    """Pick an execution profile from config, falling back if GPU is missing."""
    cfg = cfg or {}
    requested = normalize_runtime_name(cfg.get("runtime") or "cpu")
    if requested == "auto":
        requested = "cuda" if cuda_available() else "cpu"

    name = requested
    if name in ("cuda", "tensorrt") and not cuda_available():
        print(f"[Runtime] {name} requested but CUDA is unavailable; using cpu")
        name = "cpu"

    backend_opencv, backend_cuda, target_cpu, target_cuda = _dnn_constants()
    if name in ("cuda", "tensorrt"):
        yolo_device: str | int = 0
        dnn_backend, dnn_target = backend_cuda, target_cuda
        weights_name = str(cfg.get("server_weights") or cfg.get("weights") or SERVER_WEIGHTS)
    elif name == "openvino":
        yolo_device = "intel"
        dnn_backend, dnn_target = backend_opencv, target_cpu
        weights_name = str(cfg.get("weights") or EDGE_WEIGHTS)
    else:
        yolo_device = "cpu"
        dnn_backend, dnn_target = backend_opencv, target_cpu
        weights_name = str(cfg.get("weights") or EDGE_WEIGHTS)

    return RuntimeProfile(
        name=name,
        yolo_device=yolo_device,
        weights_name=weights_name.strip() or EDGE_WEIGHTS,
        dnn_backend=dnn_backend,
        dnn_target=dnn_target,
        reid_enabled=bool(cfg.get("enable_reid", True)),
        track_max_age=max(1, int(cfg.get("track_max_age") if cfg.get("track_max_age") is not None else 30)),
        track_min_hits=max(1, int(cfg.get("track_min_hits") if cfg.get("track_min_hits") is not None else 3)),
        track_iou_threshold=float(cfg.get("track_iou_threshold") if cfg.get("track_iou_threshold") is not None else 0.3),
        reid_match_threshold=float(
            cfg.get("reid_match_threshold") if cfg.get("reid_match_threshold") is not None else 0.50
        ),
    )


def apply_dnn_backend(net: Any, profile: RuntimeProfile | None) -> None:
    """Prefer CUDA on the server; OpenCV CPU/OpenVINO on the edge."""
    if net is None or profile is None or cv2 is None:
        return
    try:
        net.setPreferableBackend(profile.dnn_backend)
        net.setPreferableTarget(profile.dnn_target)
    except Exception:
        backend_opencv, _, target_cpu, _ = _dnn_constants()
        net.setPreferableBackend(backend_opencv)
        net.setPreferableTarget(target_cpu)


def resolve_weights_file(cfg: dict, resource_path, data_dir: Path) -> str:
    """Locate YOLO weights for the active runtime (engine > onnx > pt)."""
    profile = resolve_runtime(cfg)
    name = Path(profile.weights_name).name
    stem = Path(name).stem
    search_dirs = []
    try:
        bundled = Path(resource_path(name)).parent
        search_dirs.append(bundled)
    except Exception:
        pass
    search_dirs.append(Path(data_dir))

    preferred_exts: tuple[str, ...]
    if profile.name == "tensorrt":
        preferred_exts = (".engine", ".pt", ".onnx")
    elif profile.name == "openvino":
        preferred_exts = (".onnx", ".pt")
    elif profile.name == "cuda":
        preferred_exts = (".pt", ".engine", ".onnx")
    else:
        preferred_exts = (".onnx", ".pt")

    candidates: list[str] = []
    if Path(profile.weights_name).suffix:
        candidates.append(name)
    for ext in preferred_exts:
        candidates.append(f"{stem}{ext}")
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        for folder in search_dirs:
            path = folder / cand
            if path.is_file():
                return str(path)
    return str(profile.weights_name)
