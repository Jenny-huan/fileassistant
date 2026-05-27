from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image
from pypdf import PdfReader

from app.pdf_processor import PdfProcessingError, inspect_pdf_upload, process_existing_pdf_job, process_pdf_upload


class PdfProcessorTests(unittest.TestCase):
    def test_rejects_non_pdf(self) -> None:
        with self.assertRaises(PdfProcessingError):
            process_pdf_upload("bad.docx", b"not a pdf")

    def test_inspect_then_processes_pdf(self) -> None:
        source = _build_sample_pdf()
        inspected = inspect_pdf_upload("sample.pdf", source.read_bytes())
        self.assertIn("job_id", inspected)
        self.assertEqual(inspected["document"]["pages"], 1)
        self.assertGreaterEqual(inspected["document"]["images"], 1)

        report = process_existing_pdf_job(inspected["job_id"])
        self.assertEqual(report["job_id"], inspected["job_id"])
        self.assertTrue(Path(report["backup_file"]).exists())
        self.assertTrue(Path(report["output_file"]).exists())
        self.assertGreaterEqual(report["stats"]["images"], 1)

        reader = PdfReader(report["output_file"])
        self.assertEqual(len(reader.pages), 1)


def _build_sample_pdf() -> Path:
    tmp = Path(tempfile.mkdtemp())
    pdf_path = tmp / "sample.pdf"
    image = Image.new("RGB", (2200, 1400), "#427c9d")
    image.save(pdf_path, "PDF", resolution=100.0)
    return pdf_path


if __name__ == "__main__":
    unittest.main()

