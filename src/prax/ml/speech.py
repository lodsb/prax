"""What a recording says, on this host: where it is speech, singing or
music, and the words of the speech and the singing (plan item AQ stage 4,
``docs/media-by-reference.md``).

Three parts, in order:

1. **The sound**, decoded by ffmpeg to 16 kHz mono (``decode``). Any
   container ffmpeg reads, a phone's ``.mov`` included; the video is
   never decoded.
2. **What each stretch is.** An AudioSet tagger (AST, ``TAGGER``) reads
   it in windows of ``WINDOW_S`` seconds and gives each a probability of
   speech, singing and music; a window quieter than ``SILENT_DBFS`` is
   silence without a model. ``kind_of`` turns them into one kind a
   window, and ``spans`` merges neighbours of one kind.
3. **The words**, by Whisper (``WHISPER``), of each span of speech or
   singing on its own. Read whole, Whisper stretched a sentence back over
   the music before it and may write words for music: the user's
   recordings hold song ideas (2026-11-16). Its own checks are on (a
   segment it is not sure of is read again warmer, one it hears no speech
   in is dropped), and each segment it keeps is scored again for
   ``logprob``, the mean log-probability of its words, which the text
   shows where it is low (``DOUBTFUL``).

A music span is written as music and its length, never as words. A sung
segment is written as sung: lyrics are words, but less sure ones.

The models run on the card when it has room (``NEED_MB`` free), and are
let go after every pass (``release``): the worker is resident, and the
card is shared with llama-server and marker. A recording never leaves the
host: no paid model, no API, as for a face.

The parts that need no model (``kind_of``, ``spans``, ``render``) are
pure, and the tests run the rest with a stand-in (``Models``).
"""

from __future__ import annotations

import importlib
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from prax.text import markup

SR = 16000  # what both models read
WINDOW_S = 5.0
SILENT_DBFS = -50.0  # a window quieter than this is silence, no model asked
SPEECH_LINE = 0.4
SUNG_LINE = 0.3  # singing is a rarer label: a lower line finds it
MUSIC_LINE = 0.4
MIN_SPAN_S = 2 * WINDOW_S  # a lone window of another kind joins its neighbours
WORDS = ("speech", "sung")  # the kinds Whisper reads
DOUBTFUL = -1.0  # a segment's mean log-probability below this is marked
NEED_MB = 3000  # what both models take on the card, with room (1.8 GB peak)

TAGGER = "MIT/ast-finetuned-audioset-10-10-0.4593"
TAGGER_REVISION = "f826b80d28226b62986cc218e5cec390b1096902"
WHISPER = "openai/whisper-large-v3-turbo"
WHISPER_REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
# another model, another line or another way of writing it is a new
# version, and every recording is read again
VERSION = 1

# the AudioSet labels each kind is the most of (AST's own names)
LABELS = {
    "speech": (
        "Speech",
        "Male speech, man speaking",
        "Female speech, woman speaking",
        "Child speech, kid speaking",
        "Conversation",
        "Narration, monologue",
    ),
    "sung": ("Singing", "Choir", "Male singing", "Female singing", "Rapping"),
    "music": ("Music", "Musical instrument"),
}


@dataclass(frozen=True)
class Span:
    """A stretch of one kind, in seconds from the start."""

    start: float
    end: float
    kind: str


@dataclass(frozen=True)
class Segment:
    """Words Whisper heard, where, in which kind of span, how sure."""

    start: float
    end: float
    text: str
    kind: str
    logprob: float | None = None


@dataclass
class Reading:
    duration: float
    spans: list[Span]
    segments: list[Segment] = field(default_factory=list)

    def seconds(self, kind: str) -> float:
        return round(sum(s.end - s.start for s in self.spans if s.kind == kind), 1)

    def facts(self) -> dict[str, Any]:
        """What ``meta.transcript`` keeps besides the stamp."""
        return {
            "duration": round(self.duration, 1),
            **{f"{k}_s": self.seconds(k) for k in ("speech", "sung", "music")},
            "segments": len(self.segments),
            "doubtful": sum(1 for s in self.segments if _doubtful(s)),
        }


class Models(Protocol):
    """What reads the sound: the tagger's probabilities a window, and
    Whisper's segments of one span (times from the span's start)."""

    def tag(self, windows: list[Any]) -> list[dict[str, float]]: ...

    def words(self, audio: Any, kind: str) -> list[Segment]: ...


def available() -> bool:
    """Whether ffmpeg, torch and transformers are here; the models are
    fetched once."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        for name in ("numpy", "torch", "transformers"):
            importlib.import_module(name)
    except ImportError:
        return False
    return True


def stamp() -> str:
    """Which reading wrote it, for ``meta.transcript.by``."""
    return f"whisper-large-v3-turbo+ast/{VERSION}"


def decode(path: Path) -> Any:
    """The sound of the file at ``path`` as 16 kHz mono float32; an empty
    array for a file with no sound."""
    import numpy as np

    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1"]
        + ["-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip()
        if "does not contain any stream" in err or "matches no streams" in err:
            return np.zeros(0, np.float32)
        raise ValueError(f"ffmpeg: {err[-300:] or out.returncode}")
    return np.frombuffer(out.stdout, np.float32)


def dbfs(window: Any) -> float:
    """How loud a window is, as its RMS in dB below full scale."""
    import numpy as np

    rms = (
        float(np.sqrt(np.mean(np.square(window, dtype=np.float64))))
        if len(window)
        else 0.0
    )
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def kind_of(probs: dict[str, float] | None) -> str:
    """One kind for a window from the tagger's probabilities: silence
    where no model was asked (``None``), singing before speech (a song's
    words are heard as speech too), then speech, then music, else
    ``other`` (a door, traffic, wind)."""
    if probs is None:
        return "silence"
    best = {
        k: max((probs.get(x, 0.0) for x in names), default=0.0)
        for k, names in LABELS.items()
    }
    if best["sung"] >= SUNG_LINE:
        return "sung"
    if best["speech"] >= SPEECH_LINE:
        return "speech"
    if best["music"] >= MUSIC_LINE:
        return "music"
    return "other"


def spans(kinds: list[str], duration: float) -> list[Span]:
    """Neighbouring windows of one kind as one span. A run shorter than
    ``MIN_SPAN_S`` between two runs of one kind joins them (a breath in a
    sentence, a spoken word over a song), except that words never join
    music: a short phrase between two songs is still read."""
    runs: list[list[Any]] = []
    for i, kind in enumerate(kinds):
        start = i * WINDOW_S
        end = min(duration, start + WINDOW_S)
        if runs and runs[-1][2] == kind:
            runs[-1][1] = end
        else:
            runs.append([start, end, kind])
    merged: list[list[Any]] = []
    for run in runs:
        merged.append(run)
        while len(merged) >= 3:
            a, b, c = merged[-3:]
            short = b[1] - b[0] < MIN_SPAN_S
            if not (short and a[2] == c[2] and not (b[2] in WORDS and a[2] == "music")):
                break
            merged[-3:] = [[a[0], c[1], a[2]]]
    return [Span(round(s, 2), round(e, 2), k) for s, e, k in merged if e > s]


def read(audio: Any, models: Models) -> Reading:
    """The whole reading of a sound already decoded."""
    duration = len(audio) / SR
    step = int(WINDOW_S * SR)
    windows = [audio[i : i + step] for i in range(0, len(audio), step)]
    loud = [i for i, w in enumerate(windows) if dbfs(w) > SILENT_DBFS]
    probs: list[dict[str, float] | None] = [None] * len(windows)
    for i, p in zip(loud, models.tag([windows[i] for i in loud]), strict=True):
        probs[i] = p
    found = spans([kind_of(p) for p in probs], duration)
    out = Reading(duration, found)
    for span in found:
        if span.kind not in WORDS:
            continue
        piece = audio[int(span.start * SR) : int(span.end * SR)]
        for seg in models.words(piece, span.kind):
            out.segments.append(
                Segment(
                    round(span.start + seg.start, 2),
                    round(span.start + min(seg.end, span.end - span.start), 2),
                    seg.text,
                    span.kind,
                    seg.logprob,
                )
            )
    return out


def _doubtful(seg: Segment) -> bool:
    return seg.logprob is not None and seg.logprob < DOUBTFUL


def render(reading: Reading) -> str:
    """The text of a reading: a ``## Transcript`` of timed lines, each
    span of music one line with its length, each sung segment and each
    doubtful one marked (``prax.text.markup``). Empty when nothing was
    heard: no speech, no singing, no music."""
    lines: list[str] = []
    by_start = sorted(reading.segments, key=lambda s: s.start)
    for span in reading.spans:
        if span.kind == "music":
            lines.append(markup.music_line(span.start, span.end - span.start))
        elif span.kind in WORDS:
            for seg in by_start:
                if span.start <= seg.start < span.end and seg.text.strip():
                    lines.append(
                        markup.spoken_line(
                            seg.start,
                            seg.text,
                            sung=seg.kind == "sung",
                            doubtful=_doubtful(seg),
                        )
                    )
    if not lines:
        return ""
    return "\n\n".join(["## Transcript", *lines]) + "\n"


# -- the models, on this host


class Local:
    """AST and Whisper through transformers and torch, on the card when
    it has room, else on the CPU when ``cpu`` is allowed. Loaded on first
    use, let go by ``release``."""

    def __init__(self, device: str) -> None:
        self.device = device
        self._tagger: Any = None
        self._whisper: Any = None

    def _dtype(self) -> Any:
        import torch

        return torch.float16 if self.device == "cuda" else torch.float32

    def tag(self, windows: list[Any]) -> list[dict[str, float]]:
        import torch

        tf: Any = importlib.import_module("transformers")  # untyped on purpose
        if not windows:
            return []
        if self._tagger is None:
            fe = tf.ASTFeatureExtractor.from_pretrained(
                TAGGER, revision=TAGGER_REVISION
            )
            model = tf.ASTForAudioClassification.from_pretrained(
                TAGGER, revision=TAGGER_REVISION, torch_dtype=self._dtype()
            )
            self._tagger = (fe, model.to(self.device).eval())
        fe, model = self._tagger
        names = model.config.id2label
        wanted = {n for group in LABELS.values() for n in group}
        out: list[dict[str, float]] = []
        for i in range(0, len(windows), 16):
            batch = fe(list(windows[i : i + 16]), sampling_rate=SR, return_tensors="pt")
            x = batch["input_values"].to(self.device, self._dtype())
            with torch.no_grad():
                p = torch.sigmoid(model(x).logits.float()).cpu().numpy()
            for row in p:
                out.append(
                    {names[j]: float(row[j]) for j in names if names[j] in wanted}
                )
        return out

    def _load_whisper(self) -> Any:
        tf: Any = importlib.import_module("transformers")
        if self._whisper is None:
            proc = tf.AutoProcessor.from_pretrained(WHISPER, revision=WHISPER_REVISION)
            model = tf.AutoModelForSpeechSeq2Seq.from_pretrained(
                WHISPER, revision=WHISPER_REVISION, torch_dtype=self._dtype()
            )
            self._whisper = (proc, model.to(self.device).eval())
        return self._whisper

    def words(self, audio: Any, kind: str) -> list[Segment]:
        import torch

        proc, model = self._load_whisper()
        inp = proc(
            audio,
            sampling_rate=SR,
            return_tensors="pt",
            truncation=False,
            padding="longest",
            return_attention_mask=True,
        )
        if inp["input_features"].shape[-1] < 3000:  # short form wants 30 s
            inp = proc(
                audio, sampling_rate=SR, return_tensors="pt", return_attention_mask=True
            )
        feats = inp["input_features"].to(self.device, self._dtype())
        mask = inp["attention_mask"].to(self.device)
        lang = self._language(feats)
        with torch.no_grad():
            got = model.generate(
                input_features=feats,
                attention_mask=mask,
                task="transcribe",
                language=lang,
                return_timestamps=True,
                return_segments=True,
                condition_on_prev_tokens=False,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                logprob_threshold=-1.0,
                compression_ratio_threshold=1.35,  # gzip's 2.4, as Whisper's own
                no_speech_threshold=0.6,
            )
        out: list[Segment] = []
        for seg in got["segments"][0] if got.get("segments") else []:
            text = proc.decode(seg["tokens"], skip_special_tokens=True).strip()
            if not text:
                continue
            start, end = float(seg["start"]), float(seg["end"])
            score = self._score(audio, start, end, text, lang)
            out.append(Segment(start, end, text, kind, score))
        return out

    def _language(self, feats: Any) -> str:
        """The language the span opens in, as Whisper hears its first 30 s
        (``de``, ``en``): one for the span, so the words and their score
        are read in the same one."""
        import torch

        proc, model = self._load_whisper()
        with torch.no_grad():
            token = model.detect_language(input_features=feats[..., :3000])[0]
        return str(proc.tokenizer.decode([int(token)])).strip("<|>")

    def _score(
        self, audio: Any, start: float, end: float, text: str, lang: str
    ) -> float | None:
        """The mean log-probability of ``text`` given its stretch of sound,
        with the words forced: Whisper's own scores are a window's, not a
        segment's."""
        import torch

        proc, model = self._load_whisper()
        piece = audio[int(start * SR) : int(end * SR)]
        if len(piece) < SR // 10:
            return None
        feats = proc(piece, sampling_rate=SR, return_tensors="pt")["input_features"]
        feats = feats.to(self.device, self._dtype())
        proc.tokenizer.set_prefix_tokens(
            language=lang, task="transcribe", predict_timestamps=False
        )
        # Whisper writes a word with the space before it, the first one too:
        # "This" alone is a token it never says there (-10 against -0.01)
        spoken = " " + text.strip()
        ids = proc.tokenizer(spoken, return_tensors="pt").input_ids.to(self.device)
        with torch.no_grad():
            logits = model(input_features=feats, decoder_input_ids=ids[:, :-1]).logits
        logp = torch.log_softmax(logits.float(), dim=-1)
        target = ids[:, 1:]
        picked = logp.gather(-1, target.unsqueeze(-1)).squeeze(-1)[0]
        # the forced start (start, language, task) is not the words'
        words = picked[len(proc.tokenizer.prefix_tokens) - 1 :]
        return round(float(words.mean()), 3) if len(words) else None

    def release(self) -> None:
        """Let the models go, and the card's memory with them."""
        self._tagger = self._whisper = None
        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def device(setting: str | None) -> str | None:
    """Where the models run: ``cuda`` when the card has ``NEED_MB`` free
    (asked of ``nvidia-smi``, so the worker never opens a CUDA context of
    its own), else ``cpu`` when the host allows it (``transcribe.device:
    cpu`` or ``auto``), else None, and the pass waits for the card."""
    from prax.host import hostinfo

    choice = (setting or "cuda").strip().lower()
    if choice == "cpu":
        return "cpu"
    free = hostinfo.vram_free_mb()
    if free is not None and free >= NEED_MB:
        return "cuda"
    return "cpu" if choice == "auto" else None


def read_file(path: Path, models: Models) -> dict[str, Any]:
    """One recording's file read whole: its text and what was found, or
    ``unreadable`` when ffmpeg cannot read it."""
    try:
        audio = decode(path)
    except ValueError as exc:  # a file ffmpeg cannot read is that file's
        return {"unreadable": str(exc)[:300]}
    got = read(audio, models)
    return {"text": render(got), "facts": got.facts()}


def read_files(paths: list[Path], where: str) -> list[dict[str, Any]]:
    """Each file read by a process of its own (``python -m
    prax.ml.speech``), which loads the models, reads them all and exits:
    the worker is resident, and torch and a CUDA context would otherwise
    keep their RAM and some of the card for its whole life. Raises when
    the process fails, and every file waits for the next pass."""
    import json
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "prax.ml.speech", where, *map(str, paths)],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        tail = out.stderr.decode("utf-8", "replace").strip()[-400:]
        raise RuntimeError(f"the reading process ended with {out.returncode}: {tail}")
    got = json.loads(out.stdout.decode("utf-8"))
    if not isinstance(got, list) or len(got) != len(paths):
        raise RuntimeError("the reading process answered for other files")
    return [dict(r) for r in got]


def _main(argv: list[str]) -> int:
    import json
    import sys

    where, paths = argv[0], [Path(p) for p in argv[1:]]
    local = Local(where)
    try:
        results = [read_file(p, local) for p in paths]
    finally:
        local.release()
    sys.stdout.write(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
