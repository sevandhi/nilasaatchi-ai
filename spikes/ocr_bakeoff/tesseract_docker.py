"""Tesseract access via the `nilasaatchi-ocr-tools` docker image.

Tesseract is not installed on the host (see T0.2 / `phase0-bootstrap`). We build the
image once with `docker compose --profile tools build ocr-tools` and then talk to it
through a single long-running container (`docker exec`), which avoids paying the
~0.3-0.5s `docker run` container-creation overhead on every page.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import cv2
import numpy as np

CELL_WHITELIST = "0123456789./-,"

IMAGE = "nilasaatchi-ocr-tools:latest"
CONTAINER = "nilasaatchi-ocr-bakeoff"


@dataclass
class TesseractResult:
    text: str
    seconds: float
    returncode: int
    stderr: str


class DockerUnavailable(RuntimeError):
    pass


class TesseractDocker:
    """Context manager: `with TesseractDocker(repo_root) as tess: tess.ocr(...)`."""

    def __init__(self, repo_root: Path, image: str = IMAGE, container: str = CONTAINER):
        self.repo_root = Path(repo_root).resolve()
        self.image = image
        self.container = container
        self._started_by_us = False

    # -- lifecycle ----------------------------------------------------------
    def _docker(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if shutil.which("docker") is None:
            raise DockerUnavailable("docker is not on PATH")
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)

    def ensure_image(self) -> None:
        r = self._docker("image", "inspect", self.image, check=False)
        if r.returncode != 0:
            build = self._docker(
                "compose", "--profile", "tools", "build", "ocr-tools", check=False
            )
            if build.returncode != 0:
                raise DockerUnavailable(
                    f"failed to build {self.image}: {build.stderr[-2000:]}"
                )

    def start(self) -> Self:
        self.ensure_image()
        inspect = self._docker("inspect", "-f", "{{.State.Running}}", self.container, check=False)
        if inspect.returncode == 0 and inspect.stdout.strip() == "true":
            return self
        # remove any stale (stopped) container with the same name, then start fresh
        self._docker("rm", "-f", self.container, check=False)
        self._docker(
            "run", "-d", "--name", self.container,
            "-v", f"{self.repo_root}:/work", "-w", "/work",
            self.image, "tail", "-f", "/dev/null",
        )
        self._started_by_us = True
        return self

    def stop(self) -> None:
        if self._started_by_us:
            self._docker("rm", "-f", self.container, check=False)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- OCR ------------------------------------------------------------------
    def ocr_file(
        self,
        image_path: Path,
        lang: str = "tam+eng",
        psm: int = 6,
        whitelist: str | None = None,
    ) -> TesseractResult:
        rel = Path(image_path).resolve().relative_to(self.repo_root)
        cmd = [
            "exec", self.container, "tesseract", f"/work/{rel.as_posix()}", "stdout",
            "-l", lang, "--psm", str(psm),
        ]
        if whitelist:
            cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
        t0 = time.perf_counter()
        r = self._docker(*cmd, check=False)
        dt = time.perf_counter() - t0
        return TesseractResult(text=r.stdout, seconds=dt, returncode=r.returncode, stderr=r.stderr)

    def ocr_cell_array(
        self,
        image: np.ndarray,
        psm: int = 7,
        whitelist: str = CELL_WHITELIST,
        tmp_dir: Path | None = None,
    ) -> TesseractResult:
        """OCR a small numpy crop (a grid cell) with a digit whitelist, per T0.5's
        candidate (c): numeric-looking table columns get Tesseract with
        `-c tessedit_char_whitelist=0123456789./-,` at psm 7 (single text line)."""
        tmp_dir = tmp_dir or (self.repo_root / "data" / "bakeoff" / "tmp_cells")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # Upscale small crops -- tesseract needs a reasonable line height.
        scale = 2 if max(image.shape[:2]) < 200 else 1
        if scale != 1:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        tmp_path = tmp_dir / f"cell_{uuid.uuid4().hex}.png"
        cv2.imwrite(str(tmp_path), image)
        try:
            return self.ocr_file(tmp_path, lang="eng", psm=psm, whitelist=whitelist)
        finally:
            tmp_path.unlink(missing_ok=True)
