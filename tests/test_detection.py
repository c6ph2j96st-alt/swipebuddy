from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from swipebuddy import detection as d
from swipebuddy.detection import Box


def raw_tensor() -> np.ndarray:
    return np.zeros((1, 3549, 85), dtype=np.float32)


def _hot(raw, idx, cx, cy, w, h, obj=0.9, cls=1.0):
    raw[0, idx, :4] = [cx, cy, np.log(w), np.log(h)]
    raw[0, idx, 4] = obj
    raw[0, idx, 5] = cls


def test_letterbox_landscape():
    img = Image.new("RGB", (832, 416), (255, 0, 0))  # red in RGB
    arr, r = d.letterbox(img)
    assert arr.shape == (3, 416, 416) and arr.dtype == np.float32
    assert r == pytest.approx(0.5)
    # BGR: red lands in channel 2
    assert arr[2, 10, 10] == 255 and arr[0, 10, 10] == 0
    assert arr[0, 300, 10] == 114 and arr[1, 300, 10] == 114


def test_letterbox_portrait():
    img = Image.new("RGB", (208, 832), (0, 0, 255))  # blue in RGB
    arr, r = d.letterbox(img)
    assert r == pytest.approx(0.5)
    assert arr[0, 10, 10] == 255 and arr[2, 10, 10] == 0  # blue -> channel 0
    assert arr[0, 10, 300] == 114  # right padding


def test_grid_sizes():
    grids, strides = d.make_grids(416, d.STRIDES)
    assert grids.shape == (3549, 2) and strides.shape == (3549, 1)
    assert strides[0, 0] == 8 and strides[-1, 0] == 32
    assert tuple(grids[1]) == (1, 0)  # x varies fastest


def test_decode_one_hot_cell():
    raw = raw_tensor()
    idx = 2704 + 26 * 3 + 5  # stride-16 map, y=3, x=5
    raw[0, idx, :4] = [0.5, 0.25, np.log(2.0), np.log(3.0)]
    out = d.decode_outputs(raw[0])
    assert out[idx, 0] == pytest.approx((5 + 0.5) * 16)
    assert out[idx, 1] == pytest.approx((3 + 0.25) * 16)
    assert out[idx, 2] == pytest.approx(32)
    assert out[idx, 3] == pytest.approx(48)
    assert raw[0, idx, 2] == pytest.approx(np.log(2.0))  # input untouched


def test_nms_suppresses_and_orders():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=float)
    scores = np.array([0.6, 0.9, 0.7])
    assert d.nms(boxes, scores, 0.5) == [1, 2]
    assert d.nms(boxes, scores, 0.99) == [1, 2, 0]
    assert d.nms(np.zeros((0, 4)), np.zeros(0), 0.5) == []


def test_postprocess_scaling_and_clamp():
    raw = raw_tensor()
    # stride 8, x=10, y=10 -> center (80,80), w=h=2 -> 16px box
    _hot(raw, 10 * 52 + 10, 0.0, 0.0, 2.0, 2.0)
    boxes = d.postprocess(raw, 0.5, 1000, 1000, conf=0.4)
    assert len(boxes) == 1
    b = boxes[0]
    assert (b.x1, b.y1, b.x2, b.y2) == pytest.approx((144, 144, 176, 176))
    assert b.score == pytest.approx(0.9)
    clamped = d.postprocess(raw, 0.5, 160, 150, conf=0.4)[0]
    assert clamped.x2 == 160 and clamped.y2 == 150


def test_postprocess_conf_filter_and_nms():
    raw = raw_tensor()
    _hot(raw, 0, 0, 0, 4, 4, obj=0.3, cls=1.0)
    assert d.postprocess(raw, 1.0, 416, 416, conf=0.4) == []
    _hot(raw, 1, 0, 0, 4, 4, obj=0.8)
    _hot(raw, 2, -1, 0, 4, 4, obj=0.7)  # overlapping neighbour
    out = d.postprocess(raw, 1.0, 416, 416, conf=0.4, iou=0.5)
    assert len(out) == 1 and out[0].score == pytest.approx(0.8)


def test_crop_person_largest_margin_clamp():
    img = Image.new("RGB", (200, 100))
    boxes = [Box(10, 10, 30, 30, 0.9), Box(50, 20, 150, 80, 0.5)]
    c = d.crop_person(img, boxes, margin=0.1)
    assert c is not None and c.size == (120, 72)
    edge = d.crop_person(img, [Box(0, 0, 100, 100, 0.9)], margin=0.1)
    assert edge is not None and edge.size == (110, 100)


def test_crop_person_none_cases():
    img = Image.new("RGB", (200, 100))
    assert d.crop_person(img, []) is None
    assert d.crop_person(img, [Box(0, 0, 10, 10, 0.9)]) is None  # 100 < 1000


class FakeInput:
    name = "images"


class FakeSession:
    def __init__(self):
        self.calls = []

    def get_inputs(self):
        return [FakeInput()]

    def run(self, names, feed):
        self.calls.append((names, feed))
        raw = raw_tensor()
        _hot(raw, 10 * 52 + 10, 0.0, 0.0, 2.0, 2.0)
        return [raw]


def test_onnx_detector_with_fake_session(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    sess = FakeSession()
    det = d.OnnxPersonDetector(model, session_factory=lambda p: sess)
    boxes = det.detect(Image.new("RGB", (832, 416)))
    _, feed = sess.calls[0]
    arr = feed["images"]
    assert arr.shape == (1, 3, 416, 416) and arr.dtype == np.float32
    assert len(boxes) == 1
    assert boxes[0].x2 - boxes[0].x1 == pytest.approx(32)  # 16px / ratio 0.5


def test_missing_model(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        d.OnnxPersonDetector(tmp_path / "nope.onnx", session_factory=lambda p: None)


def test_verify_model_file(tmp_path, monkeypatch):
    f = tmp_path / "m.onnx"
    data = b"hello model"
    f.write_bytes(data)
    monkeypatch.setattr(d, "MODEL_SIZE", len(data))
    monkeypatch.setattr(d, "MODEL_SHA256", hashlib.sha256(data).hexdigest())
    assert d.verify_model_file(f)
    f.write_bytes(b"hello modeX")
    assert not d.verify_model_file(f)
    f.write_bytes(b"short")
    assert not d.verify_model_file(f)
    assert not d.verify_model_file(tmp_path / "missing")


def test_default_model_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SWIPEBUDDY_HOME", str(tmp_path))
    assert d.default_model_path() == tmp_path / "models" / d.MODEL_FILENAME


@pytest.mark.integration
def test_real_model_runs():
    p = os.environ.get("SWIPEBUDDY_YOLOX_PATH")
    if not p or not Path(p).is_file():
        pytest.skip("SWIPEBUDDY_YOLOX_PATH not set")
    det = d.OnnxPersonDetector(Path(p))
    assert isinstance(det.detect(Image.new("RGB", (640, 480), (128, 128, 128))), list)
