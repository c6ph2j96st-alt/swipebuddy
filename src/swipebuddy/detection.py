"""Person detection with YOLOX-Nano via onnxruntime (CPU). Images stay in memory."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

import numpy as np
from PIL import Image

from .paths import models_dir

MODEL_FILENAME = "yolox_nano.onnx"
MODEL_URL = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/"
    "0.1.1rc0/yolox_nano.onnx"
)
MODEL_SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
MODEL_SIZE = 3_659_407
INPUT_SIZE = 416
PERSON_CLASS = 0
STRIDES = (8, 16, 32)
PAD_VALUE = 114


def default_model_path() -> Path:
    """Default location of the detection model."""
    return models_dir() / MODEL_FILENAME


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in original image pixels."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


class PersonDetector(Protocol):
    def detect(self, img: Image.Image) -> list[Box]: ...


def letterbox(img: Image.Image) -> tuple[np.ndarray, float]:
    """Resize keeping aspect ratio, pad bottom/right with 114.

    Returns a float32 BGR CHW array (no normalization) and the scale ratio.
    """
    size = INPUT_SIZE
    rgb = img.convert("RGB")
    w, h = rgb.size
    r = min(size / h, size / w)
    nw, nh = max(1, int(w * r)), max(1, int(h * r))
    resized = np.asarray(rgb.resize((nw, nh), Image.BILINEAR), dtype=np.uint8)
    canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
    canvas[:nh, :nw] = resized[:, :, ::-1]  # RGB -> BGR
    chw = np.ascontiguousarray(canvas.transpose(2, 0, 1), dtype=np.float32)
    return chw, r


def make_grids(
    input_size: int = INPUT_SIZE, strides: Sequence[int] = STRIDES
) -> tuple[np.ndarray, np.ndarray]:
    """Grid offsets (N,2) and per-cell strides (N,1) over all feature maps."""
    grids, expanded = [], []
    for s in strides:
        n = input_size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        expanded.append(np.full((n * n, 1), s))
    return (
        np.concatenate(grids, 0).astype(np.float32),
        np.concatenate(expanded, 0).astype(np.float32),
    )


def decode_outputs(raw: np.ndarray, input_size: int = INPUT_SIZE) -> np.ndarray:
    """Turn raw grid-offset outputs (N,85) into cx,cy,w,h in input pixels."""
    out = np.array(raw, dtype=np.float32, copy=True)
    grids, strides = make_grids(input_size)
    out[:, :2] = (out[:, :2] + grids) * strides
    out[:, 2:4] = np.exp(out[:, 2:4]) * strides
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Greedy NMS on x1y1x2y2 boxes; returns kept indices, best score first."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        if rest.size == 0:
            break
        iw = np.maximum(0.0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        ih = np.maximum(0.0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = iw * ih
        union = areas[i] + areas[rest] - inter
        overlap = np.where(union > 0, inter / np.maximum(union, 1e-12), 0.0)
        order = rest[overlap <= iou]
    return keep


def postprocess(
    raw: np.ndarray,
    ratio: float,
    img_w: int,
    img_h: int,
    conf: float = 0.4,
    iou: float = 0.5,
) -> list[Box]:
    """Decode model output into person boxes in original image coordinates."""
    arr = np.asarray(raw)
    if arr.ndim == 3:
        arr = arr[0]
    dec = decode_outputs(arr)
    scores = dec[:, 4] * dec[:, 5 + PERSON_CLASS]
    mask = scores >= conf
    if not mask.any():
        return []
    dec, scores = dec[mask], scores[mask]
    cx, cy, w, h = dec[:, 0], dec[:, 1], dec[:, 2], dec[:, 3]
    xyxy = np.stack((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), 1) / ratio
    xyxy[:, [0, 2]] = np.clip(xyxy[:, [0, 2]], 0, img_w)
    xyxy[:, [1, 3]] = np.clip(xyxy[:, [1, 3]], 0, img_h)
    keep = nms(xyxy, scores, iou)
    return [
        Box(float(xyxy[i, 0]), float(xyxy[i, 1]), float(xyxy[i, 2]),
            float(xyxy[i, 3]), float(scores[i]))
        for i in keep
    ]


class OnnxPersonDetector:
    """YOLOX-Nano person detector running on onnxruntime CPU."""

    def __init__(
        self,
        model_path: Path | str,
        conf: float = 0.4,
        iou: float = 0.5,
        session_factory: Callable[[str], Any] | None = None,
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Detection model not found at {path}. Run setup to download "
                f"{MODEL_FILENAME}."
            )
        self.conf = conf
        self.iou = iou
        if session_factory is None:
            session_factory = _default_session_factory
        self._session = session_factory(str(path))
        self._input_name = self._session.get_inputs()[0].name

    def detect(self, img: Image.Image) -> list[Box]:
        w, h = img.size
        chw, ratio = letterbox(img)
        outputs = self._session.run(None, {self._input_name: chw[None, ...]})
        return postprocess(outputs[0], ratio, w, h, self.conf, self.iou)


def _default_session_factory(path: str) -> Any:
    import onnxruntime as ort  # lazy: heavy import

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    return ort.InferenceSession(
        path, sess_options=opts, providers=["CPUExecutionProvider"]
    )


def crop_person(
    img: Image.Image,
    boxes: Sequence[Box],
    margin: float = 0.10,
    min_area_frac: float = 0.05,
) -> Image.Image | None:
    """Crop the largest sufficiently large person box (plus margin), else None."""
    iw, ih = img.size
    min_area = min_area_frac * iw * ih
    candidates = [b for b in boxes if b.area >= min_area and b.area > 0]
    if not candidates:
        return None
    b = max(candidates, key=lambda x: x.area)
    mx, my = (b.x2 - b.x1) * margin, (b.y2 - b.y1) * margin
    left = max(0, int(np.floor(b.x1 - mx)))
    top = max(0, int(np.floor(b.y1 - my)))
    right = min(iw, int(np.ceil(b.x2 + mx)))
    bottom = min(ih, int(np.ceil(b.y2 + my)))
    if right <= left or bottom <= top:
        return None
    return img.crop((left, top, right, bottom))


def verify_model_file(path: Path | str) -> bool:
    """True if the file has the expected size and sha256."""
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size != MODEL_SIZE:
            return False
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest() == MODEL_SHA256
