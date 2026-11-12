"""Copy the page's JavaScript libraries into velocity (security review, 2026-11-12: no
scripts from a CDN). Each library is the npm package at a pinned version, checked against
the npm registry's sha512 before anything is extracted; only the files the page uses are
kept, with the package's license.

    uv run python packaging/vendor.py

Writes src/velocity/web_static/vendor/<package>/ (replacing what is there). To update a
library, change its version below, run this, and check the page (chat math, a PDF book).
"""

import base64
import hashlib
import io
import json
import shutil
import sys
import tarfile
import urllib.request
from fnmatch import fnmatch
from pathlib import Path

REGISTRY = "https://registry.npmjs.org"
VENDOR = Path(__file__).resolve().parent.parent / "src/velocity/web_static/vendor"

# package: (version, files to keep, as patterns relative to the package folder)
LIBRARIES = {
    "katex": (
        "0.16.11",
        ["LICENSE", "dist/katex.min.js", "dist/katex.min.css", "dist/fonts/*.woff2"],
    ),
    "marked": ("12.0.2", ["LICENSE.md", "marked.min.js"]),
    "dompurify": ("3.1.6", ["LICENSE", "dist/purify.min.js"]),
    "pdfjs-dist": (
        "6.4.299",
        [
            "LICENSE",
            "legacy/build/pdf.min.mjs",
            "legacy/build/pdf.worker.min.mjs",
            "legacy/web/pdf_viewer.mjs",
            "legacy/web/pdf_viewer.css",
            "legacy/web/images/*",
        ],
    ),
}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def tarball(package: str, version: str) -> bytes:
    """The package's tarball, checked against the registry's sha512 integrity."""
    meta = json.loads(fetch(f"{REGISTRY}/{package}/{version}"))
    integrity = meta["dist"]["integrity"]
    algorithm, expected = integrity.split("-", 1)
    if algorithm != "sha512":
        sys.exit(f"{package}: unexpected integrity {algorithm}")
    data = fetch(meta["dist"]["tarball"])
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != expected:
        sys.exit(f"{package}@{version}: the tarball doesn't match the registry's hash")
    return data


def vendor(package: str, version: str, patterns: list[str]) -> None:
    out = VENDOR / package
    if out.exists():
        shutil.rmtree(out)
    kept = 0
    with tarfile.open(fileobj=io.BytesIO(tarball(package, version))) as tar:
        for member in tar.getmembers():
            name = member.name.removeprefix("package/")
            if not member.isfile() or not any(fnmatch(name, p) for p in patterns):
                continue
            target = out / name
            if not target.resolve().is_relative_to(out.resolve()):
                sys.exit(f"{package}: {member.name} points outside its folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
            kept += 1
    (out / "VERSION").write_text(f"{package}@{version}\n", encoding="utf-8")
    print(f"{package}@{version}: {kept} files")


def main() -> None:
    for package, (version, patterns) in LIBRARIES.items():
        vendor(package, version, patterns)


if __name__ == "__main__":
    main()
