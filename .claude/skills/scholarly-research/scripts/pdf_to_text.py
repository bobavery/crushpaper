#!/usr/bin/env python3
"""Convert a PDF to text with page markers, so quotations can carry page numbers.

Usage: python3 pdf_to_text.py book.pdf out.txt [--offset N]

Each page is preceded by '=== PAGE n ===' where n is the printed page number,
computed as pdf_page_index - offset. Find the offset by locating a page whose
printed number you know (e.g. the first page of chapter 1) and subtracting.

Prefers the `pdftotext` CLI (poppler) when present, because its layout handling is
better; otherwise uses PyMuPDF (`fitz`), installing it from PyPI if needed. Package
registries are normally reachable even when the rest of the web is blocked. PyMuPDF
is chosen over pypdf because its wheel is self-contained; pypdf imports the system
`cryptography` package, which is broken in some containers.
"""
import shutil
import subprocess
import sys


def via_pdftotext(src):
    out = subprocess.run(["pdftotext", "-layout", src, "-"], capture_output=True, text=True, check=True)
    return out.stdout.split("\f")


def via_pymupdf(src):
    try:
        import fitz  # noqa: F401
    except Exception:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pymupdf"], check=True)
    import fitz
    doc = fitz.open(src)
    return [page.get_text() for page in doc]


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    src, dst = args[0], args[1]
    offset = 0
    if "--offset" in args:
        offset = int(args[args.index("--offset") + 1])
    pages = via_pdftotext(src) if shutil.which("pdftotext") else via_pymupdf(src)
    with open(dst, "w") as fh:
        for i, text in enumerate(pages, start=1):
            fh.write(f"\n\n=== PAGE {i - offset} (pdf {i}) ===\n\n")
            fh.write(text.strip())
    print(f"{src} -> {dst}: {len(pages)} pages, printed page = pdf page - {offset}")


if __name__ == "__main__":
    main()
