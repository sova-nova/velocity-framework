"""Sources uploaded from the web page (M9, 2026-11-09).

A browser can't tell the server where a file is, so an upload is copied into velocity's
data dir (`<data>/uploads/<subject>/`), outside the vault and the repo, and then added like
`velocity source add` does. The file name is reduced to a safe base name (never a path
from the request), and the content must match its type.
"""

import re
import zipfile
from pathlib import Path

from velocity.config import SOURCE_SUFFIXES, data_dir

MAX_UPLOAD_BYTES = 1024**3  # 1 GB; a 1,400-page textbook is about 35 MB


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def uploads_dir(subject_slug: str) -> Path:
    return data_dir() / "uploads" / subject_slug


def upload_path(subject_slug: str, filename: str) -> Path:
    """Where to keep an uploaded file: its base name with only safe characters, in the
    subject's uploads folder, with "-2", "-3"... if the name is taken."""
    base = Path(filename.replace("\\", "/")).name
    suffix = Path(base).suffix.lower()
    if suffix not in SOURCE_SUFFIXES:
        raise UploadError(
            f"{base or 'This file'}: unsupported file type (use PDF, EPUB, Markdown or HTML)."
        )
    stem = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", Path(base).stem).strip(" ._") or "source"
    folder = uploads_dir(subject_slug)
    path = folder / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}{suffix}"
        n += 1
    return path


def check_content(path: Path, suffix: str) -> None:
    """Refuse a file whose content doesn't match its type `suffix` (a renamed or broken
    file, say). `path` is where it was received, which may have another suffix (.part)."""
    suffix = suffix.lower()
    with path.open("rb") as f:
        head = f.read(1024)
    if not head:
        raise UploadError("The file is empty.")
    if suffix == ".pdf":
        if b"%PDF-" not in head:
            raise UploadError("This file isn't a PDF, although its name ends in .pdf.")
        _check_pdf_opens(path)
    elif suffix == ".epub":
        if not zipfile.is_zipfile(path):
            raise UploadError(
                "This file isn't an EPUB, although its name ends in .epub."
            )
    elif b"\x00" in head:
        raise UploadError("This file isn't text, although its name says it is.")


def _check_pdf_opens(path: Path) -> None:
    import pymupdf

    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            if doc.page_count == 0:
                raise UploadError("This PDF has no pages.")
    except UploadError:
        raise
    except Exception:  # noqa: BLE001 - PyMuPDF raises several kinds for broken files
        raise UploadError("This PDF is damaged and can't be opened.") from None
