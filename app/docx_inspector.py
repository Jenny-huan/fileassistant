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
    drawing_count: int
    inline_drawing_count: int
    floating_drawing_count: int
    textbox_count: int
    ole_object_count: int
    unsupported_object_count: int
    compatibility_notes: list[str]
    images: list[ImageInfo]


def get_body_width_emu(document: Document) -> int:
    section = document.sections[0]
    return int(section.page_width - section.left_margin - section.right_margin)


def inspect_docx(path: Path) -> DocumentInfo:
    document = Document(path)
    images = _inspect_media_images(path)
    compatibility = _inspect_compatibility(path)
    return DocumentInfo(
        body_width_emu=get_body_width_emu(document),
        inline_image_count=len(document.inline_shapes),
        media_image_count=len(images),
        drawing_count=compatibility["drawing_count"],
        inline_drawing_count=compatibility["inline_drawing_count"],
        floating_drawing_count=compatibility["floating_drawing_count"],
        textbox_count=compatibility["textbox_count"],
        ole_object_count=compatibility["ole_object_count"],
        unsupported_object_count=compatibility["unsupported_object_count"],
        compatibility_notes=compatibility["compatibility_notes"],
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


def _inspect_compatibility(path: Path) -> dict:
    with ZipFile(path, "r") as archive:
        xml = "".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        )

    inline = xml.count("<wp:inline")
    floating = xml.count("<wp:anchor")
    drawings = xml.count("<w:drawing")
    textboxes = xml.count("<w:txbxContent") + xml.count("<v:textbox")
    ole_objects = xml.count("<o:OLEObject") + xml.count("<w:object")
    unsupported = floating + textboxes + ole_objects
    notes: list[str] = []
    if floating:
        notes.append(f"Detected {floating} floating image/object(s). Only inline images are resized in this version.")
    if textboxes:
        notes.append(f"Detected {textboxes} text box object(s). Images inside text boxes may be skipped.")
    if ole_objects:
        notes.append(f"Detected {ole_objects} embedded/OLE object(s). These are not modified.")
    if not notes:
        notes.append("No unsupported Word/WPS drawing objects detected.")

    return {
        "drawing_count": drawings,
        "inline_drawing_count": inline,
        "floating_drawing_count": floating,
        "textbox_count": textboxes,
        "ole_object_count": ole_objects,
        "unsupported_object_count": unsupported,
        "compatibility_notes": notes,
    }
