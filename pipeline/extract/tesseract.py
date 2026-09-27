"""Tesseract tam+eng via the `nilasaatchi-ocr-tools` docker image.

Promoted from spikes/ocr_bakeoff/tesseract_docker.py (one long-running container, `docker exec`
per page). New in P2: TSV output → text lines with page-fraction bboxes, so header fields carry
evidence boxes; results are cached by content hash (image bytes + params).
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from .cache import ContentCache, content_hash

IMAGE = "nilasaatchi-ocr-tools:latest"
CONTAINER = "nilasaatchi-ocr-extract"
REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ID = "tesseract-5-tam+eng-psm4"


class DockerUnavailable(RuntimeError):
    pass


@dataclass
class OcrLine:
    text: str
    bbox: list[float]          # page fraction [x0, y0, x1, y1]
    conf: float                # mean word confidence 0..100


@dataclass
class OcrPage:
    text: str
    lines: list[OcrLine] = field(default_factory=list)
    seconds: float = 0.0
    engine: str = ENGINE_ID
    error: str | None = None

    def to_dict(self) -> dict:
        return {"text": self.text, "seconds": self.seconds, "engine": self.engine, "error": self.error,
                "lines": [{"text": ln.text, "bbox": ln.bbox, "conf": ln.conf} for ln in self.lines]}

    @classmethod
    def from_dict(cls, d: dict) -> OcrPage:
        return cls(text=d.get("text", ""), seconds=d.get("seconds", 0.0), engine=d.get("engine", ENGINE_ID),
                   error=d.get("error"),
                   lines=[OcrLine(ln["text"], ln["bbox"], ln["conf"]) for ln in d.get("lines", [])])


def tsv_to_lines(tsv: str) -> tuple[str, list[OcrLine]]:
    """Group Tesseract TSV words into lines (block, par, line) with page-fraction bboxes."""
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE))
    page_w = page_h = None
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        try:
            level = int(r["level"])
        except (KeyError, TypeError, ValueError):
            continue
        if level == 1:
            page_w, page_h = int(r["width"]), int(r["height"])
        if level != 5 or not (r.get("text") or "").strip():
            continue
        groups.setdefault((int(r["block_num"]), int(r["par_num"]), int(r["line_num"])), []).append(r)
    if not page_w or not page_h:
        return "", []
    lines: list[OcrLine] = []
    for key in sorted(groups):
        ws = groups[key]
        x0 = min(int(w["left"]) for w in ws)
        y0 = min(int(w["top"]) for w in ws)
        x1 = max(int(w["left"]) + int(w["width"]) for w in ws)
        y1 = max(int(w["top"]) + int(w["height"]) for w in ws)
        confs = [float(w["conf"]) for w in ws if float(w["conf"]) >= 0]
        lines.append(OcrLine(" ".join(w["text"].strip() for w in ws),
                             [round(x0 / page_w, 4), round(y0 / page_h, 4), round(x1 / page_w, 4),
                              round(y1 / page_h, 4)],
                             round(sum(confs) / len(confs), 1) if confs else 0.0))
    return "\n".join(ln.text for ln in lines), lines


class Tesseract:
    """`with Tesseract() as t: t.ocr_page(path)` — starts (or reuses) one container."""

    def __init__(self, repo_root: Path = REPO_ROOT, image: str = IMAGE, container: str = CONTAINER,
                 cache_dir: Path | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.image, self.container = image, container
        self.cache = ContentCache(cache_dir or self.repo_root / "data/extract/cache/tesseract")
        self._started_by_us = False

    def _docker(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if shutil.which("docker") is None:
            raise DockerUnavailable("docker is not on PATH")
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)

    def start(self) -> Self:
        if self._docker("image", "inspect", self.image, check=False).returncode != 0:
            b = self._docker("compose", "--profile", "tools", "build", "ocr-tools", check=False)
            if b.returncode != 0:
                raise DockerUnavailable(f"failed to build {self.image}: {b.stderr[-1000:]}")
        r = self._docker("inspect", "-f", "{{.State.Running}}", self.container, check=False)
        if r.returncode == 0 and r.stdout.strip() == "true":
            return self
        self._docker("rm", "-f", self.container, check=False)
        self._docker("run", "-d", "--name", self.container, "-v", f"{self.repo_root}:/work", "-w", "/work",
                     self.image, "tail", "-f", "/dev/null")
        self._started_by_us = True
        return self

    def stop(self) -> None:
        if self._started_by_us:
            self._docker("rm", "-f", self.container, check=False)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def detect_orientation(self, image_path: Path) -> tuple[int, float]:
        """(clockwise rotation needed to make the page upright, confidence) from Tesseract OSD
        (--psm 0). (0, 0.0) when OSD fails. Cached by image content."""
        image_path = Path(image_path).resolve()
        key = content_hash(image_path.read_bytes(), ENGINE_ID, "osd", "psm0")
        cached = self.cache.get(key)
        if cached is not None:
            return int(cached["rotate"]), float(cached["confidence"])
        rel = image_path.relative_to(self.repo_root)
        r = self._docker("exec", self.container, "tesseract", f"/work/{rel.as_posix()}", "stdout", "--psm", "0",
                         check=False)
        rot = re.search(r"Rotate:\s*(\d+)", r.stdout or "")
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", r.stdout or "")
        out = (int(rot.group(1)) if rot else 0, float(conf.group(1)) if conf else 0.0)
        self.cache.set(key, {"rotate": out[0], "confidence": out[1]})
        return out

    def ocr_page(self, image_path: Path, lang: str = "tam+eng", psm: int = 4) -> OcrPage:
        image_path = Path(image_path).resolve()
        key = content_hash(image_path.read_bytes(), ENGINE_ID, lang, str(psm), "tsv-v1")
        cached = self.cache.get(key)
        if cached is not None:
            return OcrPage.from_dict(cached)
        rel = image_path.relative_to(self.repo_root)
        t0 = time.perf_counter()
        r = self._docker("exec", self.container, "tesseract", f"/work/{rel.as_posix()}", "stdout",
                         "-l", lang, "--psm", str(psm), "tsv", check=False)
        dt = round(time.perf_counter() - t0, 2)
        if r.returncode != 0:
            return OcrPage(text="", seconds=dt, error=r.stderr[-500:])
        text, lines = tsv_to_lines(r.stdout)
        page = OcrPage(text=text, lines=lines, seconds=dt)
        self.cache.set(key, page.to_dict())
        return page
