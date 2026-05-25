from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, UnidentifiedImageError

from app.config import JPEG_QUALITY, MAX_IMAGE_LONG_EDGE_PX


@dataclass(frozen=True)
class CompressStats:
    media_images: int
    compressed_images: int
    pixel_resized_images: int
    skipped_images: int
    bytes_before: int
    bytes_after: int


def compress_docx_images(
    source_path: Path,
    output_path: Path,
    max_long_edge_px: int = MAX_IMAGE_LONG_EDGE_PX,
    jpeg_quality: int = JPEG_QUALITY,
) -> CompressStats:
    media_images = 0
    compressed_images = 0
    pixel_resized_images = 0
    skipped_images = 0
    bytes_before = 0
    bytes_after = 0

    with ZipFile(source_path, "r") as src, ZipFile(output_path, "w", ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.startswith("word/media/"):
                media_images += 1
                bytes_before += len(data)
                processed = _compress_image(data, max_long_edge_px, jpeg_quality)
                if processed is None:
                    skipped_images += 1
                    output_data = data
                else:
                    output_data, resized = processed
                    if resized:
                        pixel_resized_images += 1
                    if len(output_data) < len(data):
                        compressed_images += 1
                    else:
                        output_data = data
                bytes_after += len(output_data)
                dst.writestr(item.filename, output_data)
            else:
                dst.writestr(item.filename, data)

    return CompressStats(
        media_images=media_images,
        compressed_images=compressed_images,
        pixel_resized_images=pixel_resized_images,
        skipped_images=skipped_images,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def _compress_image(
    data: bytes,
    max_long_edge_px: int,
    jpeg_quality: int,
) -> tuple[bytes, bool] | None:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            image_format = image.format
            if image_format not in {"JPEG", "PNG"}:
                return None

            resized = False
            work = image.copy()
            long_edge = max(work.width, work.height)
            if long_edge > max_long_edge_px:
                ratio = max_long_edge_px / long_edge
                new_size = (max(1, int(work.width * ratio)), max(1, int(work.height * ratio)))
                work = work.resize(new_size, Image.Resampling.LANCZOS)
                resized = True

            out = BytesIO()
            if image_format == "JPEG":
                if work.mode not in {"RGB", "L"}:
                    work = work.convert("RGB")
                work.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
            else:
                work.save(out, format="PNG", optimize=True)
            return out.getvalue(), resized
    except (UnidentifiedImageError, OSError, ValueError):
        return None

