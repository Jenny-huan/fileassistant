# Word 图片整理助手

A local web tool for formatting images inside `.docx` files.

## Features

- Upload a Word `.docx` file locally.
- Inspect a Word `.docx` before processing.
- Resize inline images to a user-selected body-width ratio.
- Adjust image width with a slider, preset buttons, or manual percentage input.
- Preview the selected width on a simulated Word page.
- Keep image aspect ratios while applying a uniform displayed width.
- Center paragraphs that contain inline images.
- Compress embedded images with Pillow.
- Save the original file as a backup and generate a JSON processing report.

## Run

```powershell
cd E:\AI-practice\wendangzhushou
C:\Users\13151\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m app.server
```

Then open:

```text
http://127.0.0.1:8000
```

Cloud platforms can set `PORT`; the app also exposes `GET /health` for health checks.

## Test

```powershell
C:\Users\13151\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s tests
```

## Runtime Files

Processed jobs are written under `storage/`:

- `storage/backups/`: original uploaded files
- `storage/outputs/`: formatted documents
- `storage/reports/`: JSON reports

## Deploy

This repo includes `render.yaml` for Render Blueprint deploys:

- Build command: `pip install -r requirements.txt`
- Start command: `python -m app.server --host 0.0.0.0`
- Health check: `/health`

## API

- `POST /api/docx/inspect`: upload `file`, returns document stats, image stats, and a reusable `job_id`
- `POST /api/docx/format`: submit `job_id` and `max_width_ratio` such as `0.8`
- `GET /api/docx/result/{job_id}`: download formatted document
- `GET /api/docx/backup/{job_id}`: download original backup
- `GET /api/docx/report/{job_id}`: download processing report
