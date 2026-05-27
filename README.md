# Word / WPS Image Organizer

A local web tool for organizing images in Word/WPS `.docx` files and compressing images in PDF files.

## Features

- Upload Word/WPS `.docx` files.
- Inspect documents before processing.
- Resize inline images to a user-selected body-width ratio.
- Adjust image width with a slider, preset buttons, or manual percentage input.
- Set image alignment to left, center, or right.
- Preview the selected width on a simulated Word page.
- Report compatibility signals, including inline images, floating drawings, text boxes, and embedded objects.
- Compress embedded images with Pillow.
- Upload PDFs and compress/downsample embedded images.
- Keep the original file as a backup and generate a JSON processing report.

PDF mode only compresses/downsamples embedded images. It does not change image layout, width, or alignment.

## Run Locally

Use Python 3.12+.

```powershell
python -m app.server
```

Then open:

```text
http://127.0.0.1:8000
```

The app also exposes `GET /health` for health checks.

## Test

```powershell
python -m unittest discover -s tests
```

## Runtime Files

Processed jobs are written under `storage/`:

- `storage/backups/`: original uploaded files
- `storage/outputs/`: formatted documents
- `storage/reports/`: JSON reports

`storage/` is ignored by git.

## Deploy

This repo includes `render.yaml` for Render Blueprint deploys:

- Build command: `pip install -r requirements.txt`
- Start command: `python -m app.server --host 0.0.0.0`
- Health check: `/health`

## API

- `POST /api/docx/inspect`: upload `file`, returns document stats, image stats, and a reusable `job_id`
- `POST /api/docx/format`: submit `job_id`, `max_width_ratio`, and `alignment`
- `GET /api/docx/result/{job_id}`: download formatted document
- `GET /api/docx/backup/{job_id}`: download original backup
- `GET /api/docx/report/{job_id}`: download processing report
- `POST /api/pdf/inspect`: upload `file`, returns PDF page and image stats
- `POST /api/pdf/format`: submit `job_id`, returns compressed PDF report
- `GET /api/pdf/result/{job_id}`: download compressed PDF
- `GET /api/pdf/backup/{job_id}`: download original PDF
- `GET /api/pdf/report/{job_id}`: download PDF processing report

## Feishu Import

Feishu online document import is currently disabled in the public UI and API surface.

The codebase keeps an experimental Feishu client for future OAuth-based integration, but the current product flow expects users to export Feishu documents as Word `.docx` files and upload them manually.
