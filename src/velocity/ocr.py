"""Math-aware OCR of PDF pages with marker (optional `ocr` dependency group).

Without an NVIDIA GPU, marker's recognition model runs through llama.cpp's
`llama-server`; set LLAMA_CPP_BINARY to its path if it is not on PATH.
"""

import re
from pathlib import Path
from typing import Any, Protocol


class PageOcr(Protocol):
    def __call__(
        self, pdf_path: Path, first_page: int, last_page: int, image_dir: Path
    ) -> dict[int, str]:
        """OCR pages first_page..last_page (1-indexed, inclusive) to markdown per page.

        Figures are saved in image_dir and linked from the markdown by absolute path.
        """
        ...


_PAGE_SEPARATOR = re.compile(r"^\{(\d+)\}-{10,}$", re.MULTILINE)
_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def split_paginated(markdown: str) -> dict[int, str]:
    """Split marker's paginated output into {1-indexed page: markdown}."""
    parts = _PAGE_SEPARATOR.split(markdown)
    return {int(parts[i]) + 1: parts[i + 1].strip() for i in range(1, len(parts), 2)}


class MarkerOcr:
    """PageOcr backed by marker with forced OCR. Models load on first use and are reused."""

    def __init__(self) -> None:
        self._models: dict[str, Any] | None = None

    def __call__(
        self, pdf_path: Path, first_page: int, last_page: int, image_dir: Path
    ) -> dict[int, str]:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        if self._models is None:
            self._models = create_model_dict()
        converter = PdfConverter(
            artifact_dict=self._models,
            config={
                "force_ocr": True,
                "paginate_output": True,
                "page_range": list(range(first_page - 1, last_page)),
            },
        )
        rendered = converter(str(pdf_path))

        image_dir.mkdir(parents=True, exist_ok=True)
        for name, image in rendered.images.items():
            image.save(image_dir / name)
        markdown = _IMAGE_LINK.sub(
            lambda m: f"![{m[1]}]({(image_dir / m[2]).as_posix()})", rendered.markdown
        )
        return split_paginated(markdown)
