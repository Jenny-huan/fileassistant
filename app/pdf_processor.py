from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.config import BACKUP_DIR, JPEG_QUALITY, MAX_IMAGE_LONG_EDGE_PX, MAX_PDF_UPLOAD_BYTES, OUTPUT_DIR, REPORT_DIR, ensure_storage_dirs


class PdfProcessingError(Exception):
    pass


@dataclass(frozen=True)
class PdfImageInfo:
    page: int
    name: str
    width_px: int | None
    height_px: int | None
    mode: str | None
    supported: bool


@dataclass(frozen=True)
class PdfInfo:
    pages: int
    images: list[PdfImageInfo]
    inline_or_unsupported_images: int


def inspect_pdf_upload(filename: str, data: bytes) -> dict:
    ensure_storage_dirs()
    _validate_pdf_upload(filename, data)
    job_id = uuid4().hex
    original_name = _safe_filename(filename)
    backup_path = BACKUP_DIR / f"{job_id}__{original_name}"
    report_path = REPORT_DIR / f"{job_id}.pdf.inspect.json"
    backup_path.write_bytes(data)
    info = inspect_pdf(backup_path)
    report = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": original_name,
        "backup_file": str(backup_path),
        "format_url": "/api/pdf/format",
        "settings": {
            "max_long_edge_px": MAX_IMAGE_LONG_EDGE_PX,
            "jpeg_quality": JPEG_QUALITY,
        },
        "document": {
            "pages": info.pages,
            "images": len(info.images),
            "inline_or_unsupported_images": info.inline_or_unsupported_images,
            "file_size": backup_path.stat().st_size,
        },
        "images": [asdict(image) for image in info.images],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def process_existing_pdf_job(job_id: str) -> dict:
    backup_path = get_pdf_job_file(job_id, "backup")
    return _process_pdf(backup_path.name.split("__", 1)[-1], backup_path.read_bytes(), job_id=job_id)


def process_pdf_upload(filename: str, data: bytes) -> dict:
    _validate_pdf_upload(filename, data)
    return _process_pdf(filename, data)


def inspect_pdf(path: Path) -> PdfInfo:
    try:
        reader = PdfReader(path)
    except Exception as exc:
        raise PdfProcessingError(f"Unable to read this PDF: {exc}") from exc
    images: list[PdfImageInfo] = []
    unsupported = 0
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            page_images = list(page.images)
        except Exception:
            unsupported += 1
            continue
        for image_file in page_images:
            image = image_file.image
            if image is None:
                unsupported += 1
                images.append(PdfImageInfo(page_index, image_file.name, None, None, None, False))
                continue
            images.append(
                PdfImageInfo(
                    page=page_index,
                    name=image_file.name,
                    width_px=image.width,
                    height_px=image.height,
                    mode=image.mode,
                    supported=image_file.indirect_reference is not None,
                )
            )
    return PdfInfo(pages=len(reader.pages), images=images, inline_or_unsupported_images=unsupported)


def get_pdf_job_file(job_id: str, kind: str) -> Path:
    _validate_job_id(job_id)
    if kind == "report":
        path = REPORT_DIR / f"{job_id}.pdf.json"
    elif kind == "output":
        matches = list(OUTPUT_DIR.glob(f"{job_id}__*.pdf"))
        path = matches[0] if matches else OUTPUT_DIR / f"{job_id}.missing"
    elif kind == "backup":
        matches = list(BACKUP_DIR.glob(f"{job_id}__*.pdf"))
        path = matches[0] if matches else BACKUP_DIR / f"{job_id}.missing"
    else:
        raise ValueError(f"Unknown PDF job file kind: {kind}")
    if not path.exists():
        raise FileNotFoundError(job_id)
    return path


def _process_pdf(filename: str, data: bytes, job_id: str | None = None) -> dict:
    ensure_storage_dirs()
    if job_id is None:
        job_id = uuid4().hex
    original_name = _safe_filename(filename)
    stem = Path(original_name).stem
    backup_path = BACKUP_DIR / f"{job_id}__{original_name}"
    output_path = OUTPUT_DIR / f"{job_id}__{stem}_compressed.pdf"
    report_path = REPORT_DIR / f"{job_id}.pdf.json"
    backup_path.write_bytes(data)
    before_size = backup_path.stat().st_size

    before = inspect_pdf(backup_path)
    reader = PdfReader(backup_path)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    processed = 0
    resized = 0
    skipped = 0
    for page in writer.pages:
        for image_file in page.images:
            image = image_file.image
            if image is None or image_file.indirect_reference is None:
                skipped += 1
                continue
            new_image, did_resize = _prepare_pdf_image(image)
            try:
                image_file.replace(new_image, quality=JPEG_QUALITY, optimize=True)
            except Exception:
                skipped += 1
                continue
            processed += 1
            if did_resize:
                resized += 1

    with output_path.open("wb") as handle:
        writer.write(handle)

    after_size = output_path.stat().st_size
    after = inspect_pdf(output_path)
    report = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": original_name,
        "backup_file": str(backup_path),
        "output_file": str(output_path),
        "result_download_url": f"/api/pdf/result/{job_id}",
        "backup_download_url": f"/api/pdf/backup/{job_id}",
        "report_url": f"/api/pdf/report/{job_id}",
        "settings": {
            "max_long_edge_px": MAX_IMAGE_LONG_EDGE_PX,
            "jpeg_quality": JPEG_QUALITY,
            "note": "PDF MVP compresses/downsamples embedded images only. It does not resize layout boxes or change alignment.",
        },
        "stats": {
            "pages": before.pages,
            "images": len(before.images),
            "processed_images": processed,
            "pixel_resized_images": resized,
            "skipped_images": skipped + before.inline_or_unsupported_images,
            "file_size_before": before_size,
            "file_size_after": after_size,
            "file_size_delta": after_size - before_size,
        },
        "before": asdict(before),
        "after": asdict(after),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _prepare_pdf_image(image: Image.Image) -> tuple[Image.Image, bool]:
    work = image.copy()
    if work.mode not in {"RGB", "L"}:
        work = work.convert("RGB")
    long_edge = max(work.width, work.height)
    if long_edge <= MAX_IMAGE_LONG_EDGE_PX:
        return work, False
    ratio = MAX_IMAGE_LONG_EDGE_PX / long_edge
    size = (max(1, int(work.width * ratio)), max(1, int(work.height * ratio)))
    return work.resize(size, Image.Resampling.LANCZOS), True


def _validate_pdf_upload(filename: str, data: bytes) -> None:
    if not filename.lower().endswith(".pdf"):
        raise PdfProcessingError("Only .pdf files are supported.")
    if len(data) == 0:
        raise PdfProcessingError("Uploaded PDF is empty.")
    if len(data) > MAX_PDF_UPLOAD_BYTES:
        raise PdfProcessingError("Uploaded PDF exceeds the 100MB limit.")


def _validate_job_id(job_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise FileNotFoundError(job_id)


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "document.pdf"
