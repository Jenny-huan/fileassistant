# Document Image Organizer

A lightweight local web app for organizing images in Word/WPS `.docx` files and compressing embedded images in PDF files.

## What It Does

For Word/WPS `.docx` files:

- Inspect the document before processing.
- Resize inline images to a selected body-width ratio.
- Set image alignment to left, center, or right.
- Preview the selected width on a simulated page.
- Compress embedded images.
- Report compatibility signals such as inline images, floating drawings, text boxes, and embedded objects.

For PDF files:

- Inspect page and image counts.
- Compress and downsample embedded images.
- Keep the original PDF layout unchanged.

Every processed job keeps the original file as a backup and writes a JSON report.

## Current Scope

Supported:

- Word/WPS `.docx`
- PDF image compression

Not currently supported:

- Legacy `.doc` or `.wps` files
- Editing PDF page layout
- Online document import
- Writing results back to third-party document platforms

## Requirements

- Python 3.12+
- Dependencies listed in `requirements.txt`

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run Locally

```bash
python -m app.server
```

Then open:

```text
http://127.0.0.1:8000
```

The app exposes a health check at:

```text
GET /health
```

## Test

```bash
python -m unittest discover -s tests
```

## Runtime Files

Processed files are written under `storage/`:

- `storage/backups/`: original uploaded files
- `storage/outputs/`: processed files
- `storage/reports/`: JSON processing reports

`storage/` is ignored by git.

## API

Word/WPS `.docx`:

- `POST /api/docx/inspect`
- `POST /api/docx/format`
- `GET /api/docx/result/{job_id}`
- `GET /api/docx/backup/{job_id}`
- `GET /api/docx/report/{job_id}`

PDF:

- `POST /api/pdf/inspect`
- `POST /api/pdf/format`
- `GET /api/pdf/result/{job_id}`
- `GET /api/pdf/backup/{job_id}`
- `GET /api/pdf/report/{job_id}`

## Deploy

This repo includes `render.yaml` for Render Blueprint deployments.

Default settings:

- Build command: `pip install -r requirements.txt`
- Start command: `python -m app.server --host 0.0.0.0`
- Health check: `/health`

