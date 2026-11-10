"""Preparing a PDF source ahead of time: OCR a chapter or the whole book in the background,
so that reading it later is instant (decided 2026-11-09, M9).

One job per source at a time, run by `velocity source prepare` (also when the web page
starts it, as a detached process, so it outlives the page and the server). The job
writes its progress to `prepare.json` in the source's cache dir, which the terminal and
the web page both read; a `prepare.cancel` file there asks it to stop after the current
batch. Pages already OCR'd are skipped, so a cancelled or interrupted job resumes where it
stopped. The tutor keeps working meanwhile: the OCR lock makes a `read_section` on
unprepared pages wait for the current batch (see PdfSource.prepare).
"""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from velocity.pdf_source import PREPARE_BATCH, PdfSource
from velocity.search import section_at

# Measured on the user's GPU (AMD RX 7700 XT, 2026-11-09): §14.3, 17 pages, 107 s, and
# §14.1, 16 pages, 91 s, including the dropped-text check.
SECONDS_PER_PAGE = 6.5
STARTUP_SECONDS = 30  # loading marker's models, once per job


class PrepareError(Exception):
    pass


def progress_file(pdf: PdfSource) -> Path:
    return pdf.cache_dir / "prepare.json"


def cancel_file(pdf: PdfSource) -> Path:
    return pdf.cache_dir / "prepare.cancel"


def estimate_seconds(pages: int, seconds_per_page: float = SECONDS_PER_PAGE) -> int:
    return round(STARTUP_SECONDS + pages * seconds_per_page) if pages else 0


def read_progress(pdf: PdfSource) -> dict | None:
    """The source's last or current job, or None. A job whose process is gone (killed, or
    the computer restarted) is reported as "interrupted"."""
    try:
        progress = json.loads(progress_file(pdf).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if progress.get("state") == "running" and not _alive(progress.get("pid", 0)):
        progress["state"] = "interrupted"
    return progress


def running(pdf: PdfSource) -> bool:
    progress = read_progress(pdf)
    return bool(progress and progress["state"] == "running")


def request_cancel(pdf: PdfSource) -> None:
    pdf.cache_dir.mkdir(parents=True, exist_ok=True)
    cancel_file(pdf).touch()


def run_job(
    pdf: PdfSource,
    first: int,
    last: int,
    label: str,
    on_update: Callable[[dict], None] | None = None,
    clock: Callable[[], float] = time.time,
    again: list[int] | None = None,
) -> dict:
    """OCR the pages of first..last that aren't cached yet, keeping prepare.json up to
    date after each batch. Returns the final progress. With `again`, OCR those cached
    pages again instead (recheck: the dropped-text check on pages prepared before it, or
    every prepared page)."""
    if running(pdf):
        raise PrepareError("This source is already being prepared.")
    finished: set[int] = set()

    def remaining() -> list[int]:
        if again is None:
            return pdf.missing_pages(first, last)
        return [p for p in sorted(again) if p not in finished]

    missing = remaining()
    cancel_file(pdf).unlink(missing_ok=True)
    started = clock()
    progress = {
        "state": "running",
        "pid": os.getpid(),
        "label": label,
        "kind": "prepare" if again is None else "recheck",
        "first": first,
        "last": last,
        "total": len(missing),
        "done": 0,
        "started": started,
        "updated": started,
        "next_page": missing[0] if missing else None,
        "next_section": _section(pdf, missing[0]) if missing else None,
        "eta_seconds": estimate_seconds(len(missing)),
        "error": None,
    }

    def save() -> None:
        _write(progress_file(pdf), progress)
        if on_update:
            on_update(progress)

    def batch_done(pages: list[int]) -> None:
        now = clock()
        finished.update(pages)
        progress["done"] += len(pages)
        progress["updated"] = now
        left = remaining()
        progress["next_page"] = left[0] if left else None
        progress["next_section"] = _section(pdf, left[0]) if left else None
        # From the real speed so far (the first batch includes loading the models).
        per_page = (now - started) / max(progress["done"], 1)
        progress["eta_seconds"] = round(
            per_page * (progress["total"] - progress["done"])
        )
        save()

    save()
    try:
        if again is None:
            pdf.prepare(first, last, PREPARE_BATCH, batch_done, cancel_file(pdf).exists)
        else:
            pdf.ocr_again(again, PREPARE_BATCH, batch_done, cancel_file(pdf).exists)
    except Exception as e:
        progress.update(state="failed", error=str(e) or type(e).__name__)
        save()
        raise
    stopped = cancel_file(pdf).exists()
    cancel_file(pdf).unlink(missing_ok=True)
    progress.update(state="cancelled" if stopped else "done", eta_seconds=0)
    progress["updated"] = clock()
    save()
    return progress


def _section(pdf: PdfSource, page: int) -> str | None:
    """The title of the section a page is in (titles carry their number: "14.3: ...")."""
    section = section_at(pdf.outline(), page)
    return section.title if section else None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)  # readers never see a half-written file


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def format_duration(seconds: float) -> str:
    """'about 2 h 10 min', 'about 7 min', 'under a minute'."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"about {minutes} min"
    return f"about {minutes // 60} h {minutes % 60:02d} min"
