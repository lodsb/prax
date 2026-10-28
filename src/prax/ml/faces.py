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
import io
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
        path.unlink(missing_ok=True)  # fetched again next time
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


# ------------------------------------------------------------ recognition
# What a face looks like as numbers (plan item AT step 2): a recogniser of
# ``RECOGNIZERS``, the host's choice (``faces.model`` in prax.yaml), SFace
# by default (OpenCV Zoo, Apache 2.0, 37 MB of ONNX, 128 numbers) over the
# face aligned to the five points it was trained with, as OpenCV's
# ``FaceRecognizerSF`` aligns it. Each model has its own index file and
# its own line for two faces being one person's, measured
# (``faces.match.line``, ``scripts/eval_faces.py``) and read by every
# match, never fixed here.
SFACE_REPO = "opencv/face_recognition_sface"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
SFACE_REVISION = "3d7082438a6e4551e840c9b2bb60b71e8da4b524"
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
RECOGNIZER = "sface-2021dec"
DIM = 128
ALIGNED_PX = 112
# where the five landmarks lie in an aligned face of 112 x 112
TEMPLATE = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


@dataclass(frozen=True)
class Recognizer:
    """A face recogniser: its file (a Hugging Face repo, pinned to a
    revision and checked by sha256), how many numbers it gives, and the
    aligned face it reads (its side in pixels and where the five landmarks
    lie in it). It reads RGB from 0 to 255, as SFace does; a model that
    reads otherwise needs its own ``vector``."""

    name: str
    repo: str
    file: str
    revision: str
    sha256: str
    dim: int
    aligned_px: int
    template: tuple[tuple[float, float], ...]


RECOGNIZERS = {
    RECOGNIZER: Recognizer(
        RECOGNIZER,
        SFACE_REPO,
        SFACE_FILE,
        SFACE_REVISION,
        SFACE_SHA256,
        DIM,
        ALIGNED_PX,
        TEMPLATE,
    ),
}


def recognizer(name: str | None = None) -> Recognizer:
    """The recogniser ``name``, or the host's (``faces.model``); a name
    prax does not know is refused rather than read as SFace."""
    from prax import config

    chosen = name or str(
        config.setting("faces.model", "PRAX_FACES_MODEL", RECOGNIZER) or RECOGNIZER
    )
    if chosen not in RECOGNIZERS:
        known = ", ".join(sorted(RECOGNIZERS))
        raise ValueError(
            f"faces.model {chosen!r} is not a recogniser of prax ({known})"
        )
    return RECOGNIZERS[chosen]


CROP_PX = 160  # the face as the Review page shows it
CROP_MARGIN = 1.6  # the square's side, in the face box's longer side
CROP_QUALITY = 82


def _recognizer(name: str | None = None) -> Any:
    """The host's recogniser (or ``name``) as an ONNX session, loaded once."""
    return _recognizer_session(recognizer(name).name)


@functools.cache
def _recognizer_session(name: str) -> Any:
    import hashlib

    from prax.ml import fetch

    spec = RECOGNIZERS[name]
    path = fetch.model_file(spec.repo, spec.file, revision=spec.revision)
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec.sha256:
        path.unlink(missing_ok=True)  # fetched again next time
        raise RuntimeError(f"{path} is not the {name} file prax was measured with")
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def similarity_transform(src: Any, dst: Any) -> Any:
    """The 2 x 3 matrix of rotation, uniform scale and shift that takes the
    points ``src`` nearest to ``dst`` in least squares (Umeyama, 1991)."""
    import numpy as np

    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    sc, dc = src - mu_s, dst - mu_d
    var = (sc**2).sum() / len(src)
    cov = dc.T @ sc / len(src)
    u, s, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[-1] = -1
    rot = u @ np.diag(d) @ vt
    scale = float((s * d).sum() / var) if var else 1.0
    shift = mu_d - scale * rot @ mu_s
    return np.hstack([scale * rot, shift[:, None]])


def align(image: Any, landmarks: Any, model: str = RECOGNIZER) -> Any:
    """The face whose five ``landmarks`` (pixels of ``image``) are given,
    turned and scaled onto the ``model``'s template: for SFace a 112 x 112
    RGB image."""
    import numpy as np
    from PIL import Image

    spec = RECOGNIZERS[model]
    m = similarity_transform(landmarks, spec.template)
    a = m[:, :2]
    inv = np.linalg.inv(a)
    back = -inv @ m[:, 2]
    # Pillow's pixel x spans x to x + 1 where OpenCV's is centred on x:
    # the half-pixel shift that makes the two the same picture (a mean
    # difference of 0.5 of 255 against OpenCV's warp, 2026-10-27)
    back = back - inv @ np.array([0.5, 0.5]) + 0.5
    data = (inv[0, 0], inv[0, 1], back[0], inv[1, 0], inv[1, 1], back[1])
    return image.convert("RGB").transform(
        (spec.aligned_px, spec.aligned_px),
        Image.Transform.AFFINE,
        data=tuple(float(v) for v in data),
        resample=Image.Resampling.BILINEAR,
    )


def vector(aligned: Any, model: str = RECOGNIZER) -> list[float]:
    """An aligned face as the ``model``'s numbers (SFace's 128), of length
    one."""
    import numpy as np

    rgb = np.asarray(aligned.convert("RGB"), dtype=np.float32)
    blob = rgb.transpose(2, 0, 1)[None]  # RGB, 0 to 255, as OpenCV feeds it
    session = _recognizer_session(model)
    (out,) = session.run(None, {session.get_inputs()[0].name: blob})
    v = out[0].astype(np.float64)
    n = float(np.linalg.norm(v))
    return [round(float(x) / n, 6) for x in v] if n else [0.0] * len(v)


def crop(image: Any, box: Any) -> bytes:
    """The face with some of what is around it, square, as a small JPEG:
    what the Review page shows of it. ``box`` is ``(x, y, w, h)`` in
    fractions of ``image``."""
    x, y, w, h = box
    cx, cy = (x + w / 2) * image.width, (y + h / 2) * image.height
    side = max(w * image.width, h * image.height) * CROP_MARGIN
    region = (
        round(cx - side / 2),
        round(cy - side / 2),
        round(cx + side / 2),
        round(cy + side / 2),
    )
    out = image.convert("RGB").crop(region).resize((CROP_PX, CROP_PX))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=CROP_QUALITY)
    return buf.getvalue()
