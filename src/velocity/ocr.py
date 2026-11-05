"""Math-aware OCR of PDF pages with marker (optional `ocr` dependency group).

Without an NVIDIA GPU, marker's recognition model runs through llama.cpp's
`llama-server`; set LLAMA_CPP_BINARY to its path if it is not on PATH.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from velocity.dropcheck import Block, block_text, crop_pdf


@dataclass(frozen=True)
class OcrPage:
    markdown: str
    blocks: list[Block]  # marker's blocks, in reading order, for the dropped-text check


class PageOcr(Protocol):
    def __call__(
        self, pdf_path: Path, first_page: int, last_page: int, image_dir: Path
    ) -> dict[int, "str | OcrPage"]:
        """OCR pages first_page..last_page (1-indexed, inclusive), per page.

        A page is markdown, or an OcrPage that also has the blocks (then dropped text is
        checked for and repaired). Figures are saved in image_dir and linked from the
        markdown by absolute path. An OCR may also offer `ocr_regions` (see MarkerOcr).
        """
        ...


_PAGE_SEPARATOR = re.compile(r"^\{(\d+)\}-{10,}$", re.MULTILINE)
_PAGE_ID = re.compile(r"/page/(\d+)/")  # marker block ids, e.g. /page/135/Page/0
_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _blocks(block: Any) -> list[Block]:
    """A marker JSON block's descendants, in reading order (parents before children)."""
    out = []
    for child in block.children or []:
        out.append(Block(child.block_type, tuple(child.bbox), block_text(child.html)))
        out += _blocks(child)
    return out


def split_paginated(markdown: str) -> dict[int, str]:
    """Split marker's paginated output into {1-indexed page: markdown}."""
    parts = _PAGE_SEPARATOR.split(markdown)
    return {int(parts[i]) + 1: parts[i + 1].strip() for i in range(1, len(parts), 2)}


class MarkerOcr:
    """PageOcr backed by marker with forced OCR. Models load on first use and are reused."""

    def __init__(self) -> None:
        self._models: dict[str, Any] | None = None

    def _converter(self, config: dict) -> Any:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        if self._models is None:
            self._models = create_model_dict()
        return PdfConverter(
            artifact_dict=self._models,
            config={"force_ocr": True, "paginate_output": True, **config},
        )

    def __call__(
        self, pdf_path: Path, first_page: int, last_page: int, image_dir: Path
    ) -> dict[int, str | OcrPage]:
        from marker.renderers.json import JSONRenderer
        from marker.renderers.markdown import MarkdownRenderer

        converter = self._converter(
            {"page_range": list(range(first_page - 1, last_page))}
        )
        document = converter.build_document(str(pdf_path))
        rendered = converter.resolve_dependencies(MarkdownRenderer)(document)
        structure = converter.resolve_dependencies(JSONRenderer)(document)

        image_dir.mkdir(parents=True, exist_ok=True)
        for name, image in rendered.images.items():
            image.save(image_dir / name)
        markdown = _IMAGE_LINK.sub(
            lambda m: f"![{m[1]}]({(image_dir / m[2]).as_posix()})", rendered.markdown
        )
        pages = split_paginated(markdown)
        for page in structure.children:
            page_number = int(_PAGE_ID.search(page.id)[1]) + 1
            if page_number in pages:
                pages[page_number] = OcrPage(pages[page_number], _blocks(page))
        return pages

    def ocr_regions(
        self, pdf_path: Path, regions: list[tuple[int, tuple]]
    ) -> list[str]:
        """OCR each (page, bbox) region on its own; "" where marker outputs nothing."""
        import pymupdf

        if not regions:
            return []
        with pymupdf.open(pdf_path) as src, tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regions.pdf"
            crop_pdf(src, regions).save(path)
            markdown = self._converter({})(str(path)).markdown
        pages = split_paginated(markdown)
        return [pages.get(i + 1, "") for i in range(len(regions))]
