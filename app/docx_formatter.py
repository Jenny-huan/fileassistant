from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.config import MAX_IMAGE_WIDTH_RATIO
from app.docx_inspector import get_body_width_emu

ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}


@dataclass(frozen=True)
class FormatStats:
    inline_images: int
    resized_images: int
    image_paragraphs: int
    centered_paragraphs: int
    max_width_emu: int


def format_docx_images(
    source_path: Path,
    output_path: Path,
    max_width_ratio: float = MAX_IMAGE_WIDTH_RATIO,
    alignment: str = "center",
) -> FormatStats:
    if max_width_ratio <= 0:
        raise ValueError("max_width_ratio must be greater than 0.")
    if alignment not in ALIGNMENTS:
        raise ValueError("alignment must be left, center, or right.")
    target_alignment = ALIGNMENTS[alignment]
    document = Document(source_path)
    body_width_emu = get_body_width_emu(document)
    max_width_emu = int(body_width_emu * max_width_ratio)

    resized_images = 0
    for shape in document.inline_shapes:
        current_width = int(shape.width)
        current_height = int(shape.height)
        if current_width <= 0 or current_width == max_width_emu:
            continue
        ratio = max_width_emu / current_width
        shape.width = max_width_emu
        shape.height = max(1, int(current_height * ratio))
        resized_images += 1

    image_paragraphs = 0
    centered_paragraphs = 0
    for paragraph in document.paragraphs:
        if not _paragraph_has_inline_image(paragraph):
            continue
        image_paragraphs += 1
        if paragraph.alignment != target_alignment:
            paragraph.alignment = target_alignment
            centered_paragraphs += 1

    document.save(output_path)
    return FormatStats(
        inline_images=len(document.inline_shapes),
        resized_images=resized_images,
        image_paragraphs=image_paragraphs,
        centered_paragraphs=centered_paragraphs,
        max_width_emu=max_width_emu,
    )


def _paragraph_has_inline_image(paragraph) -> bool:
    return bool(paragraph._p.xpath(".//w:drawing"))
