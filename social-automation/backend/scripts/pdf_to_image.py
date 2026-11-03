#!/usr/bin/env python3
"""PDF -> PNG/JPG page images (PyMuPDF).

    python pdf_to_image.py cert.pdf --out cert            # all pages
    python pdf_to_image.py deck.pdf --out slide --zoom 2  # every page
    python pdf_to_image.py doc.pdf --pages 1 --format jpg --quality 90

Runs anywhere PyMuPDF is installed (host .venv, or inside social-api:
`docker compose exec -T social-api python3 /app/...`). Output files are
named <out>.<ext> for single-page PDFs or <out>-p<N>.<ext> for multi-page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def convert(pdf: Path, out: Path, zoom: float, pages: list[int] | None, fmt: str, quality: int) -> list[Path]:
    import fitz  # pymupdf

    doc = fitz.open(str(pdf))
    wanted = pages or list(range(1, len(doc) + 1))
    made: list[Path] = []
    multi = len(wanted) > 1 or len(doc) > 1
    for pno in wanted:
        page = doc[pno - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        target = out.with_name(f"{out.name}-p{pno}" if multi else out.name).with_suffix(f".{fmt}")
        if fmt == "png":
            pix.save(str(target))
        else:
            pix.save(str(target), jpg_quality=quality)
        made.append(target)
        print(f"{target}  ({pix.width}x{pix.height})")
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="Output path prefix (extension added)")
    ap.add_argument("--zoom", type=float, default=2.5, help="Render scale factor (default 2.5 ~= 180dpi)")
    ap.add_argument("--pages", type=int, nargs="+", help="1-based page numbers (default: all)")
    ap.add_argument("--format", choices=("png", "jpg"), default="png")
    ap.add_argument("--quality", type=int, default=90, help="JPEG quality (jpg only)")
    args = ap.parse_args()
    if not args.pdf.is_file():
        sys.exit(f"not found: {args.pdf}")
    convert(args.pdf, args.out, args.zoom, args.pages, args.format, args.quality)


if __name__ == "__main__":
    main()
