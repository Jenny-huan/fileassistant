from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
BACKUP_DIR = STORAGE_DIR / "backups"
OUTPUT_DIR = STORAGE_DIR / "outputs"
REPORT_DIR = STORAGE_DIR / "reports"

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_IMAGE_WIDTH_RATIO = 0.8
MAX_IMAGE_LONG_EDGE_PX = 1600
JPEG_QUALITY = 85


def ensure_storage_dirs() -> None:
    for path in (BACKUP_DIR, OUTPUT_DIR, REPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)

