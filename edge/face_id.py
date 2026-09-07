"""Local face recognition using OpenCV YuNet (detector) and SFace (recognizer).

Enroll staff by dropping photos in ``faces/<Name>/``. Matches overlay that name;
anyone else is labeled Customer. Runs fully offline via cv2.dnn.
"""

from __future__ import annotations

import re
import shutil
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"

YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"

UNKNOWN_LABEL = "Employee"
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
IDENTITY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'_-]{0,62}$")
MAX_PHOTO_BYTES = 8 * 1024 * 1024


@dataclass
class FaceMatch:
    name: str
    confidence: float
    is_staff: bool
    bbox: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)


def ensure_model_files(models_dir: Path) -> Tuple[Path, Path]:
    """Download ONNX models if not present locally."""
    models_dir.mkdir(parents=True, exist_ok=True)
    yunet_path = models_dir / YUNET_FILENAME
    sface_path = models_dir / SFACE_FILENAME

    if not yunet_path.exists():
        print("[FaceID] Downloading YuNet face detector (~300 KB)...")
        urllib.request.urlretrieve(YUNET_URL, yunet_path)
        print(f"[FaceID] Downloaded {yunet_path.name}")

    if not sface_path.exists():
        print("[FaceID] Downloading SFace recognizer (~37 MB)...")
        urllib.request.urlretrieve(SFACE_URL, sface_path)
        print(f"[FaceID] Downloaded {sface_path.name}")

    return yunet_path, sface_path


def resolve_face_paths(cfg: dict) -> tuple[Path, Path]:
    """Writable faces/ + models/ dirs, seeding from bundled resources when frozen."""
    from paths import data_dir, resource_dir

    data = data_dir()
    resource = resource_dir()

    faces_name = str(cfg.get("faces_dir") or "faces")
    faces = Path(faces_name)
    if not faces.is_absolute():
        faces = data / faces_name

    bundled_faces = resource / "faces"
    if bundled_faces.exists() and bundled_faces.resolve() != faces.resolve():
        faces.mkdir(parents=True, exist_ok=True)
        if not any(p.is_dir() for p in faces.iterdir()):
            shutil.copytree(bundled_faces, faces, dirs_exist_ok=True)

    models_name = str(cfg.get("models_dir") or "models")
    models = Path(models_name)
    if not models.is_absolute():
        models = data / models_name
    models.mkdir(parents=True, exist_ok=True)

    bundled_models = resource / "models"
    if bundled_models.exists() and bundled_models.resolve() != models.resolve():
        for src in bundled_models.glob("*.onnx"):
            dest = models / src.name
            if not dest.exists():
                shutil.copy2(src, dest)

    return faces, models


def try_create_face_recognizer(cfg: dict) -> FaceRecognizer | None:
    if not bool(cfg.get("enable_face_id", True)):
        return None
    faces_dir, models_dir = resolve_face_paths(cfg)
    thresh = float(cfg.get("face_match_threshold") or 0.60)
    try:
        return FaceRecognizer(
            faces_dir=faces_dir,
            models_dir=models_dir,
            match_threshold=thresh,
        )
    except Exception as exc:
        print(f"[FaceID] Disabled: {exc}")
        return None


def till_status_label(
    occupied: bool,
    detections: list,
    empty_elapsed: float,
    absent: float,
    *,
    face_id_enabled: bool = False,
) -> str:
    if not occupied:
        return f"EMPTY {empty_elapsed:.0f}/{absent:.0f}s"
    if not face_id_enabled:
        return "STAFF IN ROI"
    staff = [
        det.identity
        for det in detections
        if getattr(det, "is_staff", False) and getattr(det, "identity", None)
    ]
    if staff:
        shown = ", ".join(dict.fromkeys(staff))
        return f"STAFF [{shown}] IN ROI"
    if any(getattr(det, "identity", None) for det in detections):
        return "EMPLOYEE IN ROI"
    return "PERSON IN ROI"


class FaceRecognizer:
    def __init__(
        self,
        faces_dir: str | Path = "faces",
        models_dir: str | Path = "models",
        score_threshold: float = 0.60,
        nms_threshold: float = 0.30,
        match_threshold: float = 0.60,
    ) -> None:
        self.faces_dir = Path(faces_dir)
        self.models_dir = Path(models_dir)
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self.match_threshold = match_threshold

        yunet_path, sface_path = ensure_model_files(self.models_dir)
        self.yunet_path = str(yunet_path)
        self.sface_path = str(sface_path)

        self._lock = threading.Lock()
        self._reload_lock = threading.Lock()
        self._embedding_cache: Dict[Tuple[str, float], np.ndarray] = {}

        self.detector = self._create_detector(self.yunet_path)
        self.recognizer = self._create_recognizer(self.sface_path)

        self._reload_detector: cv2.FaceDetectorYN | None = None
        self._reload_recognizer: cv2.FaceRecognizerSF | None = None

        self.known_embeddings: Dict[str, List[np.ndarray]] = {}
        self.reload_enrolled_faces()

    def _create_detector(self, yunet_path: str | None = None) -> cv2.FaceDetectorYN:
        return cv2.FaceDetectorYN.create(
            str(yunet_path or self.yunet_path),
            "",
            (320, 320),
            score_threshold=self.score_threshold,
            nms_threshold=self.nms_threshold,
            top_k=5000,
        )

    def _create_recognizer(self, sface_path: str | None = None) -> cv2.FaceRecognizerSF:
        return cv2.FaceRecognizerSF.create(str(sface_path or self.sface_path), "")

    def _get_reload_models(self) -> Tuple[cv2.FaceDetectorYN, cv2.FaceRecognizerSF]:
        with self._reload_lock:
            if self._reload_detector is None:
                self._reload_detector = self._create_detector()
            if self._reload_recognizer is None:
                self._reload_recognizer = self._create_recognizer()
            return self._reload_detector, self._reload_recognizer

    def reload_enrolled_faces(self) -> int:
        """Scan faces_dir/<StaffName>/* and compute reference embeddings using an isolated detector/recognizer."""
        if not self.faces_dir.exists():
            self.faces_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self.known_embeddings.clear()
            return 0

        enroll_det: cv2.FaceDetectorYN | None = None
        enroll_rec: cv2.FaceRecognizerSF | None = None

        valid_extensions = (".jpg", ".jpeg", ".png", ".webp")
        new_embeddings: Dict[str, List[np.ndarray]] = {}
        total_faces = 0
        seen_files: set[Tuple[str, float]] = set()

        try:
            person_dirs = sorted(self.faces_dir.iterdir())
        except Exception as exc:
            print(f"[FaceID] Error listing {self.faces_dir}: {exc}")
            with self._lock:
                return len(self.known_embeddings)

        for person_dir in person_dirs:
            if not person_dir.is_dir() or person_dir.name.startswith("."):
                continue
            name = person_dir.name
            embeddings = []

            try:
                img_files = sorted(person_dir.iterdir())
            except Exception:
                continue

            for img_file in img_files:
                if img_file.suffix.lower() not in valid_extensions:
                    continue
                try:
                    resolved_path = str(img_file.resolve())
                    mtime = img_file.stat().st_mtime
                    cache_key = (resolved_path, mtime)
                    seen_files.add(cache_key)

                    if cache_key in self._embedding_cache:
                        emb = self._embedding_cache[cache_key]
                    else:
                        emb = None
                        img = cv2.imread(resolved_path)
                        if img is not None:
                            if enroll_det is None or enroll_rec is None:
                                enroll_det, enroll_rec = self._get_reload_models()
                            emb = self.extract_embedding_from_image(
                                img,
                                detector=enroll_det,
                                recognizer=enroll_rec,
                            )
                        self._embedding_cache[cache_key] = emb

                    if emb is not None:
                        embeddings.append(emb)
                        total_faces += 1
                except Exception as exc:
                    print(f"[FaceID] Warning reading {img_file.name}: {exc}")

            if embeddings:
                new_embeddings[name] = embeddings
                print(f"[FaceID] Enrolled '{name}' with {len(embeddings)} reference photos.")

        # Prune dead cache keys
        self._embedding_cache = {k: v for k, v in self._embedding_cache.items() if k in seen_files}

        # Atomically swap the new embeddings under lock
        with self._lock:
            self.known_embeddings = new_embeddings

        print(
            f"[FaceID] Total {len(new_embeddings)} staff members enrolled "
            f"({total_faces} photos)."
        )
        return len(new_embeddings)

    def extract_embedding_from_image(
        self,
        img: np.ndarray,
        detector: Optional[cv2.FaceDetectorYN] = None,
        recognizer: Optional[cv2.FaceRecognizerSF] = None,
    ) -> Optional[np.ndarray]:
        if img is None or img.size == 0:
            return None
        h, w = img.shape[:2]
        if h < 20 or w < 20:
            return None

        # If using instance models directly, protect with lock against concurrent inference
        needs_lock = detector is None or recognizer is None
        det = detector or self.detector
        rec = recognizer or self.recognizer

        def _do_extract() -> Optional[np.ndarray]:
            det.setInputSize((w, h))
            _, faces = det.detect(img)
            if faces is None or len(faces) == 0:
                return None
            face = max(faces, key=lambda row: float(row[2]) * float(row[3]))
            aligned_face = rec.alignCrop(img, face)
            return rec.feature(aligned_face)

        if needs_lock:
            with self._lock:
                return _do_extract()
        return _do_extract()

    def recognize_in_crop(
        self,
        frame: np.ndarray,
        crop_box: Optional[Tuple[int, int, int, int]] = None,
    ) -> FaceMatch:
        """Detect and recognize a face in the full frame or a person bounding box."""
        if crop_box is not None:
            x1, y1, x2, y2 = crop_box
            h_f, w_f = frame.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w_f, x2), min(h_f, y2)
            if x2 <= x1 or y2 <= y1:
                return FaceMatch(UNKNOWN_LABEL, 0.0, False)
            sub_img = frame[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1
        else:
            sub_img = frame
            offset_x, offset_y = 0, 0

        h, w = sub_img.shape[:2]
        if h < 20 or w < 20:
            return FaceMatch(UNKNOWN_LABEL, 0.0, False)

        with self._lock:
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(sub_img)

            if faces is None or len(faces) == 0:
                return FaceMatch(UNKNOWN_LABEL, 0.0, False)

            face = faces[0]
            fx, fy, fw, fh = map(int, face[:4])
            global_bbox = (offset_x + fx, offset_y + fy, fw, fh)

            aligned_face = self.recognizer.alignCrop(sub_img, face)
            embedding = self.recognizer.feature(aligned_face)

            best_name = "Unknown"
            best_score = 0.0
            cosine_mode = getattr(
                cv2,
                "FaceRecognizerSF_FR_COSINE",
                getattr(cv2, "FACE_RECOGNIZER_SF_FR_COSINE", 0),
            )

            known = self.known_embeddings
            for name, emb_list in known.items():
                for ref_emb in emb_list:
                    score = self.recognizer.match(ref_emb, embedding, cosine_mode)
                    if score > best_score:
                        best_score = float(score)
                        best_name = name

            is_staff = best_score >= self.match_threshold
            final_name = best_name if is_staff else UNKNOWN_LABEL

            return FaceMatch(
                name=final_name,
                confidence=best_score,
                is_staff=is_staff,
                bbox=global_bbox,
            )

    def annotate_detections(self, frame: np.ndarray, detections: list) -> None:
        for det in detections:
            crop_box = (int(det.x1), int(det.y1), int(det.x2), int(det.y2))
            match = self.recognize_in_crop(frame, crop_box)
            det.identity = match.name
            det.identity_conf = match.confidence
            det.is_staff = match.is_staff


def sanitize_identity_name(name: str) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    if not IDENTITY_NAME_RE.fullmatch(cleaned):
        raise ValueError("Use a name with letters, numbers, spaces, or . _ - '")
    return cleaned


def faces_root(cfg: dict | None = None) -> Path:
    faces, _models = resolve_face_paths(cfg or {})
    faces.mkdir(parents=True, exist_ok=True)
    return faces


def _safe_under(root: Path, child: Path) -> Path:
    resolved_root = root.resolve()
    resolved = child.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("Invalid path")
    return resolved


def identity_dir(name: str, cfg: dict | None = None) -> Path:
    root = faces_root(cfg)
    return _safe_under(root, root / sanitize_identity_name(name))


def list_identity_photos(person: Path) -> list[Path]:
    if not person.is_dir():
        return []
    files = [
        p
        for p in person.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS
    ]
    return sorted(files, key=lambda p: p.name.lower())


def list_identities(cfg: dict | None = None) -> list[dict]:
    root = faces_root(cfg)
    rows: list[dict] = []
    for folder in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        photos = list_identity_photos(folder)
        rows.append(
            {
                "name": folder.name,
                "photo_count": len(photos),
                "thumbnail": photos[0].name if photos else None,
            }
        )
    return rows


def get_identity(name: str, cfg: dict | None = None) -> dict:
    person = identity_dir(name, cfg)
    if not person.is_dir():
        raise FileNotFoundError(f"No identity named {name}")
    photos = list_identity_photos(person)
    return {
        "name": person.name,
        "photo_count": len(photos),
        "photos": [{"filename": p.name} for p in photos],
    }


def create_identity(name: str, cfg: dict | None = None) -> dict:
    person = identity_dir(name, cfg)
    if person.exists():
        raise FileExistsError(f"{person.name} already exists")
    person.mkdir(parents=True, exist_ok=False)
    return {"name": person.name, "photo_count": 0, "photos": []}


def identity_photo_path(name: str, filename: str, cfg: dict | None = None) -> Path:
    person = identity_dir(name, cfg)
    fname = Path(str(filename or "")).name
    if Path(fname).suffix.lower() not in PHOTO_EXTENSIONS:
        raise ValueError("Unsupported image type")
    path = _safe_under(person, person / fname)
    if not path.is_file():
        raise FileNotFoundError("Photo not found")
    return path


def _unique_photo_name(person: Path, original: str) -> str:
    raw = Path(original or "photo.jpg").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(raw).stem).strip("-._") or "photo"
    ext = Path(raw).suffix.lower()
    if ext not in PHOTO_EXTENSIONS:
        ext = ".jpg"
    candidate = f"{stem}{ext}"
    if not (person / candidate).exists():
        return candidate
    n = 2
    while (person / f"{stem}-{n}{ext}").exists():
        n += 1
    return f"{stem}-{n}{ext}"


def save_identity_photo(
    name: str,
    data: bytes,
    original_filename: str,
    cfg: dict | None = None,
) -> str:
    if not data:
        raise ValueError("Empty file")
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Photo is larger than 8 MB")
    person = identity_dir(name, cfg)
    if not person.is_dir():
        raise FileNotFoundError(f"No identity named {name}")
    fname = _unique_photo_name(person, original_filename)
    path = person / fname
    path.write_bytes(data)
    img = cv2.imread(str(path))
    if img is None:
        path.unlink(missing_ok=True)
        raise ValueError("Could not read that image")
    return fname


def delete_identity_photo(name: str, filename: str, cfg: dict | None = None) -> None:
    path = identity_photo_path(name, filename, cfg)
    path.unlink()


def delete_identity(name: str, cfg: dict | None = None) -> None:
    person = identity_dir(name, cfg)
    if not person.is_dir():
        raise FileNotFoundError(f"No identity named {name}")
    shutil.rmtree(person)
