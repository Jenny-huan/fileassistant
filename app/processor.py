from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from app.config import BACKUP_DIR, MAX_UPLOAD_BYTES, OUTPUT_DIR, REPORT_DIR, ensure_storage_dirs
from app.docx_compressor import compress_docx_images
from app.docx_formatter import format_docx_images
from app.docx_inspector import inspect_docx


class ProcessingError(Exception):
    pass


MIN_WIDTH_RATIO = 0.4
MAX_WIDTH_RATIO = 1.0
DEFAULT_WIDTH_RATIO = 0.8
DEFAULT_ALIGNMENT = "center"
VALID_ALIGNMENTS = {"left", "center", "right"}


def inspect_upload(filename: str, data: bytes) -> dict:
    ensure_storage_dirs()
    _validate_upload(filename, data)

    job_id = uuid4().hex
    original_name = _safe_filename(filename)
    backup_path = BACKUP_DIR / f"{job_id}__{original_name}"
    report_path = REPORT_DIR / f"{job_id}.inspect.json"
    backup_path.write_bytes(data)

    try:
        info = inspect_docx(backup_path)
    except Exception as exc:
        raise ProcessingError(f"Unable to inspect this .docx: {exc}") from exc

    report = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": original_name,
        "backup_file": str(backup_path),
        "format_url": "/api/docx/format",
        "settings": {
            "min_width_ratio": MIN_WIDTH_RATIO,
            "max_width_ratio": MAX_WIDTH_RATIO,
            "default_width_ratio": DEFAULT_WIDTH_RATIO,
            "default_alignment": DEFAULT_ALIGNMENT,
            "alignments": sorted(VALID_ALIGNMENTS),
        },
        "document": {
            "body_width_emu": info.body_width_emu,
            "inline_images": info.inline_image_count,
            "media_images": info.media_image_count,
            "drawing_count": info.drawing_count,
            "floating_drawing_count": info.floating_drawing_count,
            "unsupported_object_count": info.unsupported_object_count,
            "compatibility_notes": info.compatibility_notes,
            "file_size": backup_path.stat().st_size,
        },
        "images": [asdict(image) for image in info.images],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def process_existing_job(
    job_id: str,
    max_width_ratio: float = DEFAULT_WIDTH_RATIO,
    alignment: str = DEFAULT_ALIGNMENT,
) -> dict:
    backup_path = get_job_file(job_id, "backup")
    return _process_docx(
        backup_path.name.split("__", 1)[-1],
        backup_path.read_bytes(),
        max_width_ratio,
        alignment,
        job_id=job_id,
    )


def process_upload(
    filename: str,
    data: bytes,
    max_width_ratio: float = DEFAULT_WIDTH_RATIO,
    alignment: str = DEFAULT_ALIGNMENT,
) -> dict:
    ensure_storage_dirs()
    _validate_upload(filename, data)
    return _process_docx(filename, data, max_width_ratio, alignment)


def _process_docx(
    filename: str,
    data: bytes,
    max_width_ratio: float,
    alignment: str,
    job_id: str | None = None,
) -> dict:
    _validate_width_ratio(max_width_ratio)
    _validate_alignment(alignment)
    ensure_storage_dirs()
    if job_id is None:
        job_id = uuid4().hex
    original_name = _safe_filename(filename)
    stem = Path(original_name).stem

    backup_path = BACKUP_DIR / f"{job_id}__{original_name}"
    output_path = OUTPUT_DIR / f"{job_id}__{stem}_formatted.docx"
    report_path = REPORT_DIR / f"{job_id}.json"

    backup_path.write_bytes(data)
    before_size = backup_path.stat().st_size

    try:
        before_info = inspect_docx(backup_path)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatted_path = Path(tmpdir) / "formatted.docx"
            format_stats = format_docx_images(
                backup_path,
                formatted_path,
                max_width_ratio=max_width_ratio,
                alignment=alignment,
            )
            compress_stats = compress_docx_images(formatted_path, output_path)
        after_info = inspect_docx(output_path)
    except Exception as exc:
        if output_path.exists():
            output_path.unlink()
        raise ProcessingError(f"Unable to process this .docx: {exc}") from exc

    after_size = output_path.stat().st_size
    report = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": original_name,
        "backup_file": str(backup_path),
        "output_file": str(output_path),
        "result_download_url": f"/api/docx/result/{job_id}",
        "backup_download_url": f"/api/docx/backup/{job_id}",
        "report_url": f"/api/docx/report/{job_id}",
        "settings": {
            "max_width_ratio": max_width_ratio,
            "alignment": alignment,
            "max_long_edge_px": 1600,
            "jpeg_quality": 85,
        },
        "stats": {
            "inline_images": before_info.inline_image_count,
            "media_images": before_info.media_image_count,
            "drawing_count": before_info.drawing_count,
            "floating_drawing_count": before_info.floating_drawing_count,
            "unsupported_object_count": before_info.unsupported_object_count,
            "scaled_images": format_stats.resized_images,
            "resized_images": format_stats.resized_images,
            "image_paragraphs": format_stats.image_paragraphs,
            "centered_paragraphs": format_stats.centered_paragraphs,
            "compressed_images": compress_stats.compressed_images,
            "pixel_resized_images": compress_stats.pixel_resized_images,
            "skipped_images": compress_stats.skipped_images,
            "file_size_before": before_size,
            "file_size_after": after_size,
            "file_size_delta": after_size - before_size,
            "body_width_emu": before_info.body_width_emu,
            "max_width_emu": format_stats.max_width_emu,
        },
        "before": asdict(before_info),
        "after": asdict(after_info),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def get_job_file(job_id: str, kind: str) -> Path:
    _validate_job_id(job_id)
    if kind == "report":
        path = REPORT_DIR / f"{job_id}.json"
    elif kind == "output":
        matches = list(OUTPUT_DIR.glob(f"{job_id}__*.docx"))
        path = matches[0] if matches else OUTPUT_DIR / f"{job_id}.missing"
    elif kind == "backup":
        matches = list(BACKUP_DIR.glob(f"{job_id}__*.docx"))
        path = matches[0] if matches else BACKUP_DIR / f"{job_id}.missing"
    else:
        raise ValueError(f"Unknown job file kind: {kind}")

    if not path.exists():
        raise FileNotFoundError(job_id)
    return path


def _validate_upload(filename: str, data: bytes) -> None:
    if not filename.lower().endswith(".docx"):
        raise ProcessingError("Only .docx files are supported.")
    if len(data) == 0:
        raise ProcessingError("Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProcessingError("Uploaded file exceeds the 50MB limit.")


def _validate_width_ratio(max_width_ratio: float) -> None:
    if max_width_ratio < MIN_WIDTH_RATIO or max_width_ratio > MAX_WIDTH_RATIO:
        raise ProcessingError("Image width ratio must be between 40% and 100%.")


def _validate_alignment(alignment: str) -> None:
    if alignment not in VALID_ALIGNMENTS:
        raise ProcessingError("Image alignment must be left, center, or right.")


def _validate_job_id(job_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise FileNotFoundError(job_id)


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "document.docx"
