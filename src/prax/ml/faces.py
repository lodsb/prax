"""Where the faces in a picture are, without a network call: YuNet
(OpenCV Zoo, MIT licence, 232 KB of ONNX) on onnxruntime's CPU provider
(plan item AT step 1; ``docs/biography.md`` §7).

A face is a box with five landmarks (the eyes, the nose tip, the corners
of the mouth) and the detector's score. Each is kept as a fraction of
the picture's width and height, so it holds for the original, the preview
and the thumbnail alike, all three upright. The detector reads the
preview of the photos step (1,600 px on the long side): the original may
lie on a share that is not mounted, and a face too small to find in the
preview is too small to recognise.

The model's file declares a 640 x 640 input. The network is convolutions
throughout, so the input is made any size when the session is built and
the picture is read at its own size, padded to a multiple of 32: 23 ms a
preview of 1,600 x 1,216 on the desktop's CPU (2026-10-27), against 5 ms
at 640, where a face of 40 px in the preview would shrink to 16. The
outputs are decoded as OpenCV's ``FaceDetectorYN`` decodes them, with the
same non-maximum suppression (measured against it on 2026-10-27,
``docs/log.md``).

Faces are biometric data: this module runs on the host only, and what it
finds goes to the door, never to a paid model or an API.
"""

from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass
from typing import Any

REPO = "opencv/face_detection_yunet"
FILE = "face_detection_yunet_2023mar.onnx"
REVISION = "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"
SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
DETECTOR = "yunet-2023mar"
# what a reading is: another model, another input, another rule of what
# is kept, is a new version and every picture is read again
VERSION = 1
STRIDES = (8, 16, 32)
SCORE = 0.8  # the line a face is kept at (OpenCV's demo keeps 0.9)
NMS = 0.3  # boxes overlapping more than this are one face
TOP_K = 5000
LANDMARKS = ("right_eye", "left_eye", "nose", "mouth_right", "mouth_left")


@dataclass(frozen=True)
class Face:
    """A face in a picture: ``box`` is ``(x, y, w, h)`` and each landmark
    ``(x, y)``, as fractions of the picture; ``px`` is the box's width in
    pixels of the image it was found in."""

    box: tuple[float, float, float, float]
    landmarks: tuple[tuple[float, float], ...]
    score: float
    px: int


def available() -> bool:
    """Whether onnxruntime and numpy are here; the model is fetched once."""
    try:
        importlib.import_module("onnxruntime")
        importlib.import_module("numpy")
    except ImportError:
        return False
    return True


def stamp() -> str:
    """Which reading found them, for ``meta.faces.by`` and each face."""
    return f"{DETECTOR}/{VERSION}"


@functools.cache
def _session() -> Any:
    import hashlib

    from prax.ml import fetch

    path = fetch.model_file(REPO, FILE, revision=REVISION)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError(f"{path} is not the YuNet file prax was measured with")
    import onnx
    import onnxruntime as ort

    model = onnx.load_from_string(data)
    dims = model.graph.input[0].type.tensor_type.shape.dim
    dims[2].dim_param, dims[3].dim_param = "height", "width"
    for out in model.graph.output:
        out.type.tensor_type.shape.dim[1].dim_param = "anchors"
    del model.graph.value_info[:]  # the shapes inferred for 640 x 640
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(
        model.SerializeToString(), opts, providers=["CPUExecutionProvider"]
    )


def _nms(boxes: Any, scores: Any, threshold: float) -> list[int]:
    """Greedy non-maximum suppression over ``(x, y, w, h)`` boxes, the
    best first, as ``cv2.dnn.NMSBoxes`` does it."""
    import numpy as np

    order = list(np.argsort(-scores, kind="stable")[:TOP_K])
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = x1 + boxes[:, 2], y1 + boxes[:, 3]
    area = boxes[:, 2] * boxes[:, 3]
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(int(i))
        if not order:
            break
        rest = np.array(order)
        w = np.clip(np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]), 0, None)
        h = np.clip(np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]), 0, None)
        inter = w * h
        iou = inter / (area[i] + area[rest] - inter + 1e-9)
        order = [int(j) for j, o in zip(rest, iou, strict=True) if o <= threshold]
    return keep


def decode(
    outputs: dict[str, Any], height: int, width: int, *, score: float = SCORE
) -> list[tuple[list[float], list[float], float]]:
    """YuNet's outputs for an input of ``height`` x ``width`` (padded) as
    ``(box, landmarks, score)`` in pixels, after suppression."""
    import numpy as np

    boxes, marks, scores = [], [], []
    for stride in STRIDES:
        cols = width // stride
        cls = np.clip(outputs[f"cls_{stride}"][0, :, 0], 0, 1)
        obj = np.clip(outputs[f"obj_{stride}"][0, :, 0], 0, 1)
        conf = np.sqrt(cls * obj)
        hit = np.nonzero(conf >= score)[0]
        if not len(hit):
            continue
        r, c = (hit // cols).astype(np.float32), (hit % cols).astype(np.float32)
        b = outputs[f"bbox_{stride}"][0, hit]
        k = outputs[f"kps_{stride}"][0, hit]
        cx, cy = (c + b[:, 0]) * stride, (r + b[:, 1]) * stride
        w, h = np.exp(b[:, 2]) * stride, np.exp(b[:, 3]) * stride
        boxes.append(np.stack([cx - w / 2, cy - h / 2, w, h], axis=1))
        pts = np.empty_like(k)
        pts[:, 0::2] = (k[:, 0::2] + c[:, None]) * stride
        pts[:, 1::2] = (k[:, 1::2] + r[:, None]) * stride
        marks.append(pts)
        scores.append(conf[hit])
    if not boxes:
        return []
    all_boxes = np.concatenate(boxes)
    all_marks = np.concatenate(marks)
    all_scores = np.concatenate(scores)
    keep = _nms(all_boxes, all_scores, NMS)
    return [
        (all_boxes[i].tolist(), all_marks[i].tolist(), float(all_scores[i]))
        for i in keep
    ]


def detect(image: Any, *, score: float = SCORE) -> list[Face]:
    """The faces in a PIL image (made upright by the caller), best first."""
    import numpy as np

    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    ph, pw = -(-height // 32) * 32, -(-width // 32) * 32
    blob = np.zeros((1, 3, ph, pw), dtype=np.float32)
    blob[0, :, :height, :width] = rgb[:, :, ::-1].transpose(2, 0, 1)  # BGR, as trained
    session = _session()
    names = [o.name for o in session.get_outputs()]
    got = dict(zip(names, session.run(None, {"input": blob}), strict=True))
    faces = []
    for box, pts, conf in decode(got, ph, pw, score=score):
        x, y, w, h = box
        x0, y0 = max(0.0, x), max(0.0, y)
        x1, y1 = min(float(width), x + w), min(float(height), y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        faces.append(
            Face(
                box=(
                    round(x0 / width, 5),
                    round(y0 / height, 5),
                    round((x1 - x0) / width, 5),
                    round((y1 - y0) / height, 5),
                ),
                landmarks=tuple(
                    (round(pts[2 * i] / width, 5), round(pts[2 * i + 1] / height, 5))
                    for i in range(len(LANDMARKS))
                ),
                score=round(conf, 4),
                px=round(x1 - x0),
            )
        )
    return faces
