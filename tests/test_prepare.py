"""Preparing a PDF ahead of time: batches, the OCR lock, the job's progress file, the
command's scope, and the web endpoints."""

import json
import os
import threading
import time

import pymupdf
import pytest
from starlette.testclient import TestClient

from velocity.cli import prepare_scope
from velocity.config import add_source, create_subject, load_config
from velocity.outline import Section
from velocity.pdf_source import PdfSource
from velocity.prepare import (
    PrepareError,
    cancel_file,
    format_duration,
    progress_file,
    read_progress,
    run_job,
)
from velocity.web import build_app

OUTLINE = [
    Section("ch1", "Ch 1: Limits", 1, None, 1, 25),
    Section("1.1", "1.1: Tangents", 2, "ch1", 2, 12),
    Section("1.2", "1.2: Continuity", 2, "ch1", 12, 25),
]
ORIGIN = {"Origin": "http://127.0.0.1:8765"}


class FakeOcr:
    """Records each OCR call; `delay` makes a call slow (to hold the lock a while)."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[int, int]] = []
        self.delay = delay

    def __call__(self, pdf_path, first, last, image_dir):
        self.calls.append((first, last))
        time.sleep(self.delay)
        return {p: f"## Page {p}\n\nText of page {p}." for p in range(first, last + 1)}


def source(tmp_path, ocr) -> PdfSource:
    return PdfSource("book.pdf", ocr, cache_dir=tmp_path / "cache", outline=OUTLINE)


def cache(pdf: PdfSource, *pages: int) -> None:
    (pdf.cache_dir / "pages").mkdir(parents=True, exist_ok=True)
    for p in pages:
        (pdf.cache_dir / "pages" / f"{p}.md").write_text(f"cached {p}")


# -- batches and the lock ------------------------------------------------------------------


def test_prepare_ocrs_missing_pages_in_batches(tmp_path):
    ocr = FakeOcr()
    pdf = source(tmp_path, ocr)
    cache(pdf, 5, 6)
    batches = []
    done = pdf.prepare(1, 25, batch=10, on_batch=batches.append)
    assert ocr.calls == [(1, 4), (7, 16), (17, 25)]  # 5 and 6 were cached
    assert done == [p for p in range(1, 26) if p not in (5, 6)]
    assert [len(b) for b in batches] == [4, 10, 9]
    assert pdf.missing_pages(1, 25) == []


def test_prepare_stops_between_batches(tmp_path):
    ocr = FakeOcr()
    pdf = source(tmp_path, ocr)
    stop = []
    pdf.prepare(1, 25, batch=10, on_batch=stop.append, should_stop=lambda: bool(stop))
    assert ocr.calls == [(1, 10)]


def test_read_section_waits_for_the_batch_in_progress_and_reuses_it(tmp_path):
    job_ocr = FakeOcr(delay=0.5)
    tutor_ocr = FakeOcr()
    job = source(tmp_path, job_ocr)
    tutor = source(tmp_path, tutor_ocr)  # another process in real life: its own lock fd
    thread = threading.Thread(target=job.prepare, args=(2, 11), kwargs={"batch": 10})
    thread.start()
    time.sleep(0.1)  # the job holds the lock now
    started = time.monotonic()
    text = tutor.read_section("1.1").markdown
    waited = time.monotonic() - started
    thread.join()
    assert waited > 0.3  # it waited for the job's batch
    assert "Page 2" in text
    assert job_ocr.calls == [(2, 11)]
    assert tutor_ocr.calls == [(12, 12)]  # only the page the job didn't do


# -- the job and its progress file ---------------------------------------------------------


def test_run_job_writes_progress_and_finishes(tmp_path):
    pdf = source(tmp_path, FakeOcr())
    updates = []
    result = run_job(
        pdf, 1, 25, "Ch 1: Limits", on_update=lambda p: updates.append(dict(p))
    )
    assert result["state"] == "done" and result["done"] == result["total"] == 25
    assert result["kind"] == "prepare"
    assert updates[0]["state"] == "running" and updates[0]["done"] == 0
    assert updates[0]["next_section"] == "Ch 1: Limits"
    assert [u["done"] for u in updates[1:-1]] == [10, 20, 25]
    assert read_progress(pdf)["state"] == "done"


def test_run_job_can_be_cancelled_and_resumed(tmp_path):
    pdf = source(tmp_path, FakeOcr())

    def cancel_after_first_batch(progress):
        if progress["state"] == "running" and progress["done"] == 10:
            cancel_file(pdf).touch()

    result = run_job(pdf, 1, 25, "Ch 1", on_update=cancel_after_first_batch)
    assert result["state"] == "cancelled" and result["done"] == 10
    assert not cancel_file(pdf).exists()
    again = run_job(pdf, 1, 25, "Ch 1")
    assert again["state"] == "done" and again["total"] == 15  # only what was left


def test_run_job_records_failures(tmp_path):
    def broken(*args):
        raise RuntimeError("GPU out of memory")

    pdf = source(tmp_path, broken)
    with pytest.raises(RuntimeError):
        run_job(pdf, 1, 25, "Ch 1")
    progress = read_progress(pdf)
    assert progress["state"] == "failed" and "out of memory" in progress["error"]


def test_one_job_per_source_and_dead_jobs_are_interrupted(tmp_path):
    pdf = source(tmp_path, FakeOcr())
    pdf.cache_dir.mkdir(parents=True)
    running = {"state": "running", "pid": os.getpid(), "done": 0, "total": 5}
    progress_file(pdf).write_text(json.dumps(running))
    with pytest.raises(PrepareError, match="already being prepared"):
        run_job(pdf, 1, 25, "Ch 1")
    progress_file(pdf).write_text(json.dumps({**running, "pid": 2**22 + 12345}))
    assert read_progress(pdf)["state"] == "interrupted"


def test_format_duration():
    assert format_duration(20) == "under a minute"
    assert format_duration(7 * 60 + 10) == "about 7 min"
    assert format_duration(2 * 3600 + 32 * 60) == "about 2 h 32 min"


# -- the command's scope -------------------------------------------------------------------


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for n in range(1, 26):
        doc.new_page().insert_text((72, 72), f"Page {n}")
    doc.set_toc(
        [[1, "Ch 1: Limits", 1], [2, "1.1: Tangents", 2], [2, "1.2: Continuity", 12]]
    )
    doc.save(path)
    return path


def test_prepare_scope(tmp_path, book):
    pdf = PdfSource(book, ocr=None, cache_dir=tmp_path / "cache")
    assert prepare_scope(pdf, "ch1", None) == (1, 25, "Ch 1: Limits")
    assert prepare_scope(pdf, "1.1", None) == (2, 12, "1.1: Tangents")
    assert prepare_scope(pdf, None, "3-7") == (3, 7, "PDF pp. 3-7")
    assert prepare_scope(pdf, None, None) == (1, 25, "the whole book")
    with pytest.raises(ValueError, match="No section"):
        prepare_scope(pdf, "9.9", None)
    with pytest.raises(ValueError, match="within 1-25"):
        prepare_scope(pdf, None, "20-30")


# -- the web endpoints ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch, book):
    (tmp_path / "vault").mkdir()
    (tmp_path / "cfg/velocity").mkdir(parents=True)
    (tmp_path / "cfg/velocity/config.toml").write_text(
        f"vault_path = '{tmp_path / 'vault'}'\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    config = load_config()
    create_subject(config, "Calculus")
    add_source(config, "calculus", book, "Book")
    notes = tmp_path / "notes.md"
    notes.write_text("# Limits\n")
    add_source(config, "calculus", notes, "Notes")
    return TestClient(build_app(config), base_url="http://127.0.0.1:8765")


LAUNCHED: list[list[str]] = []


class FakeProcess:
    """Stands in for subprocess.Popen: records the command, and stays "running"."""

    def __init__(self, args, **kwargs):
        LAUNCHED.append(args)
        assert kwargs["start_new_session"]  # outlives the server

    def poll(self):
        return None  # still running


def test_web_prepare_status_start_and_cancel(client, book, monkeypatch):
    import velocity.web

    monkeypatch.setattr(velocity.web.subprocess, "Popen", FakeProcess)
    url = "/api/subjects/calculus/sources/Book/prepare"
    status = client.get(url).json()
    assert status["pages"] == 25 and status["prepared"] == []
    assert status["progress"] is None and status["seconds_per_page"] > 0

    started = client.post(url, json={"section": "1.1"}, headers=ORIGIN)
    assert started.status_code == 200 and started.json()["starting"]
    command = " ".join(LAUNCHED[-1])
    assert command.endswith(
        "-m velocity.cli source prepare calculus --source Book --section 1.1"
    )
    again = client.post(url, json={}, headers=ORIGIN)
    assert again.status_code == 409  # one job per source

    assert client.post(f"{url}/cancel", headers=ORIGIN).status_code == 200
    pdf = PdfSource(book, ocr=None)
    assert cancel_file(pdf).exists()


def test_web_prepare_refuses_bad_requests(client):
    base = "/api/subjects/calculus/sources"
    assert client.get(f"{base}/Notes/prepare").status_code == 400  # not a PDF
    assert client.get(f"{base}/Nope/prepare").status_code == 404
    response = client.post(
        f"{base}/Book/prepare", json={"section": "9.9"}, headers=ORIGIN
    )
    assert response.status_code == 404
    assert client.post(f"{base}/Book/prepare", json={}).status_code == 403  # no Origin


# -- reading prepared pages again (recheck, M10) -------------------------------------------


def test_run_job_can_ocr_prepared_pages_again(tmp_path):
    ocr = FakeOcr()
    pdf = source(tmp_path, ocr)
    cache(pdf, 3, 4, 5, 9)
    updates = []
    result = run_job(
        pdf,
        1,
        25,
        "4 pages again",
        on_update=lambda p: updates.append(dict(p)),
        again=[3, 4, 5, 9],
    )
    assert result["state"] == "done" and result["total"] == result["done"] == 4
    assert result["kind"] == "recheck"  # the page words it as "Reading again"
    assert ocr.calls == [(3, 5), (9, 9)]  # the cached pages, OCR'd again
    assert updates[0]["next_page"] == 3


def test_web_starts_a_recheck(client, monkeypatch):
    import velocity.web

    monkeypatch.setattr(velocity.web.subprocess, "Popen", FakeProcess)
    url = "/api/subjects/calculus/sources/Book/prepare"
    assert client.get(url).json()["unchecked"] == 0
    client.post(url, json={"recheck": "all"}, headers=ORIGIN)
    command = " ".join(LAUNCHED[-1])
    assert command.endswith("velocity.cli source recheck calculus --source Book --all")


# -- preparing a library book (the Library tab) --------------------------------------------


def test_web_prepares_a_library_book(client, book, tmp_path, monkeypatch):
    import velocity.web
    from velocity.library import entry_id
    from velocity.uploads import library_dir

    monkeypatch.setattr(velocity.web.subprocess, "Popen", FakeProcess)
    # A book in no subject: uploaded to the library only.
    spare = library_dir() / "spare.pdf"
    spare.parent.mkdir(parents=True, exist_ok=True)
    spare.write_bytes(book.read_bytes() + b"\n% another copy\n")
    url = f"/api/library/{entry_id(spare)}/prepare"
    status = client.get(url).json()
    assert status["pages"] == 25 and status["prepared"] == []

    started = client.post(url, headers=ORIGIN)
    assert started.status_code == 200 and started.json()["starting"]
    tail = ["velocity.cli", "source", "prepare", "--file", spare.as_posix(), "--all"]
    assert LAUNCHED[-1][-len(tail) :] == tail
    assert client.post(url, headers=ORIGIN).status_code == 409  # one job per book

    assert client.post(f"{url}/cancel", headers=ORIGIN).status_code == 200
    assert cancel_file(PdfSource(spare, ocr=None)).exists()

    notes = entry_id((tmp_path / "notes.md").resolve())
    assert client.get(f"/api/library/{notes}/prepare").status_code == 400  # not a PDF
    assert client.get("/api/library/nope/prepare").status_code == 404
    assert client.post(url, headers={}).status_code == 403  # no Origin
