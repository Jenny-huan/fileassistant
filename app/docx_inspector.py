from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class ImageInfo:
    path: str
    width_px: int | None
    height_px: int | None
    format: str | None
    supported: bool


@dataclass(frozen=True)
class DocumentInfo:
    body_width_emu: int
    inline_image_count: int
    media_image_count: int
    images: list[ImageInfo]


def get_body_width_emu(document: Document) -> int:
    section = document.sections[0]
    return int(section.page_width - section.left_margin - section.right_margin)


def inspect_docx(path: Path) -> DocumentInfo:
    document = Document(path)
    images = _inspect_media_images(path)
    return DocumentInfo(
        body_width_emu=get_body_width_emu(document),
        inline_image_count=len(document.inline_shapes),
        media_image_count=len(images),
        images=images,
    )


def _inspect_media_images(path: Path) -> list[ImageInfo]:
    results: list[ImageInfo] = []
    with ZipFile(path, "r") as archive:
        for name in archive.namelist():
            if not name.startswith("word/media/"):
                continue
            data = archive.read(name)
            try:
                from io import BytesIO

                with Image.open(BytesIO(data)) as image:
                    results.append(
                        ImageInfo(
                            path=name,
                            width_px=image.width,
                            height_px=image.height,
                            format=image.format,
                            supported=True,
                        )
                    )
            except (UnidentifiedImageError, OSError):
                results.append(
                    ImageInfo(
                        path=name,
                        width_px=None,
                        height_px=None,
                        format=None,
                        supported=False,
                    )
                )
    return results

