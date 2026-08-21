from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class ResumeReadError(RuntimeError):
    pass


def load_resume_text(path: str) -> str:
    """Read text, Markdown, DOCX, or PDF without putting the resume in the repo."""
    resume_path = Path(path).expanduser()
    if not resume_path.is_file():
        raise ResumeReadError(f"Resume file does not exist: {resume_path}")
    suffix = resume_path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return resume_path.read_text(encoding="utf-8")

    if suffix == ".docx" and shutil.which("textutil"):
        command = ["textutil", "-convert", "txt", "-stdout", str(resume_path)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout

    if suffix == ".pdf" and shutil.which("pdftotext"):
        command = ["pdftotext", "-layout", str(resume_path), "-"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout

    raise ResumeReadError(
        f"Could not extract {resume_path}. Use TXT/Markdown, or install a local "
        "extractor for DOCX/PDF."
    )


def clean_resume_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
