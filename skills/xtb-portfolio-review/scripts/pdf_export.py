#!/usr/bin/env python3
"""Export an XTB HTML portfolio review to a visually verified PDF."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


DEFAULT_PDF_SCALE = 0.8
DEFAULT_PDF_MARGIN = {
    "top": "0.4in",
    "right": "0.4in",
    "bottom": "0.4in",
    "left": "0.4in",
}
DEFAULT_PRINT_SETTLE_SECONDS = 4.0
DEFAULT_CANVAS_TIMEOUT_SECONDS = 20.0
DEFAULT_REQUIRED_CHARTS = 3
DEFAULT_MIN_PAGE_INK_RATIO = 0.01
DEFAULT_MIN_CHART_PAGE_COLOR_RATIO = 0.001
CHART_PAGE_MARKERS = (
    "PORTFOLIO EVOLUTION",
    "CHARTS",
    "HOLDINGS ALLOCATION",
    "CASH FLOWS",
    "INCOME OVER TIME",
)

CANVAS_READINESS_SCRIPT = r"""
(requiredCharts) => {
  const fontsReady = !document.fonts || document.fonts.status === "loaded";
  void (document.fonts && document.fonts.ready);
  const canvases = Array.from(document.querySelectorAll("canvas"));
  const states = canvases.map((canvas) => {
    const width = canvas.width || 0;
    const height = canvas.height || 0;
    let nonblank = false;
    let sampleCount = 0;
    if (width > 0 && height > 0) {
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (ctx) {
        const data = ctx.getImageData(0, 0, width, height).data;
        const step = Math.max(4, Math.floor(data.length / 12000) * 4);
        for (let i = 0; i < data.length; i += step) {
          sampleCount += 1;
          const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
          const coloredOrText = a > 0 && !(r > 248 && g > 248 && b > 248);
          if (coloredOrText) {
            nonblank = true;
            break;
          }
        }
      }
    }
    return {
      id: canvas.id || "",
      width,
      height,
      nonblank,
      blank: !nonblank,
      sampleCount,
    };
  });
  const nonblankCount = states.filter((state) => state.nonblank).length;
  return {
    ready: fontsReady && nonblankCount >= requiredCharts,
    fontsReady,
    requiredCharts,
    nonblankCount,
    blank: states.filter((state) => state.blank).map((state) => state.id),
    states,
  };
}
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a self-contained XTB portfolio review HTML file to PDF using "
            "Playwright's PDF API, then render PDF pages to PNG for visual checks."
        )
    )
    parser.add_argument("html", help="Path to results/<stem>_review.html")
    parser.add_argument(
        "-o", "--output",
        help="PDF output path. Defaults to the HTML path with .pdf extension.",
    )
    parser.add_argument(
        "--png-dir",
        help="Directory for rendered PDF page PNGs. Defaults beside the PDF.",
    )
    parser.add_argument(
        "--scale", type=float, default=DEFAULT_PDF_SCALE,
        help=f"True Playwright PDF print scale. Default: {DEFAULT_PDF_SCALE}.",
    )
    parser.add_argument(
        "--settle-seconds", type=float, default=DEFAULT_PRINT_SETTLE_SECONDS,
        help=(
            "Pause after load/network idle/fonts/canvas readiness before printing. "
            f"Default: {DEFAULT_PRINT_SETTLE_SECONDS}."
        ),
    )
    parser.add_argument(
        "--canvas-timeout", type=float, default=DEFAULT_CANVAS_TIMEOUT_SECONDS,
        help=f"Seconds to wait for nonblank Chart.js canvases. Default: {DEFAULT_CANVAS_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--required-charts", type=int, default=DEFAULT_REQUIRED_CHARTS,
        help=(
            "Minimum nonblank canvases required before printing. Use 0 only for "
            f"HTML files without charts. Default: {DEFAULT_REQUIRED_CHARTS}."
        ),
    )
    parser.add_argument(
        "--no-open-report",
        action="store_true",
        help="Accepted for compatibility; the exporter never opens a GUI viewer.",
    )
    return parser.parse_args(argv)


def file_url(path: Path) -> str:
    return path.resolve().as_uri()


def wait_for_canvas_charts(page, *, required_charts: int, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict | None = None
    while time.monotonic() < deadline:
        last_state = page.evaluate(CANVAS_READINESS_SCRIPT, required_charts)
        if required_charts <= 0 or last_state.get("ready"):
            return last_state
        time.sleep(0.25)
    details = json.dumps(last_state or {}, indent=2)
    raise RuntimeError(
        "Chart.js canvases did not become nonblank before PDF export. "
        f"Last canvas state:\n{details}"
    )


def render_pdf_pages(pdf_path: Path, png_dir: Path) -> list[Path]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - exercised by CLI users.
        raise RuntimeError(
            "Missing PyMuPDF. Install skill dependencies with scripts/setup-env.sh."
        ) from exc

    png_dir.mkdir(parents=True, exist_ok=True)
    png_paths: list[Path] = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            out = png_dir / f"{pdf_path.stem}_page_{page_index:02d}.png"
            pixmap.save(out)
            png_paths.append(out)
    if not png_paths:
        raise RuntimeError(f"PDF rendered no pages: {pdf_path}")
    return png_paths


def detect_chart_pages(pdf_path: Path) -> set[int]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - exercised by CLI users.
        raise RuntimeError(
            "Missing PyMuPDF. Install skill dependencies with scripts/setup-env.sh."
        ) from exc

    chart_pages: set[int] = set()
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            text = page.get_text().upper()
            if any(marker in text for marker in CHART_PAGE_MARKERS):
                chart_pages.add(page_index)
    return chart_pages


def page_ink_ratios(png_path: Path) -> tuple[float, float]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - exercised by CLI users.
        raise RuntimeError(
            "Missing PyMuPDF. Install skill dependencies with scripts/setup-env.sh."
        ) from exc

    pixmap = fitz.Pixmap(str(png_path))
    components = pixmap.n
    samples = pixmap.samples
    pixels = max(1, pixmap.width * pixmap.height)
    ink = 0
    color = 0
    for offset in range(0, len(samples), components):
        r = samples[offset]
        g = samples[offset + 1]
        b = samples[offset + 2]
        if r < 245 or g < 245 or b < 245:
            ink += 1
        if max(r, g, b) - min(r, g, b) > 20 and (r < 245 or g < 245 or b < 245):
            color += 1
    return ink / pixels, color / pixels


def check_rendered_pages(png_paths: list[Path], chart_pages: set[int]) -> None:
    failures: list[str] = []
    for index, path in enumerate(png_paths, start=1):
        ink_ratio, color_ratio = page_ink_ratios(path)
        if ink_ratio < DEFAULT_MIN_PAGE_INK_RATIO:
            failures.append(
                f"page {index} looks blank after PDF rendering "
                f"(ink ratio {ink_ratio:.4f})"
            )
        if index in chart_pages and color_ratio < DEFAULT_MIN_CHART_PAGE_COLOR_RATIO:
            failures.append(
                f"chart-heavy page {index} has too little chart color "
                f"(color ratio {color_ratio:.4f})"
            )
    if failures:
        raise RuntimeError("; ".join(failures))


def export_pdf(args: argparse.Namespace) -> tuple[Path, list[Path]]:
    html_path = Path(args.html)
    if not html_path.exists():
        raise FileNotFoundError(f"HTML report not found: {html_path}")
    if not 0.1 <= args.scale <= 2.0:
        raise ValueError("--scale must be between 0.1 and 2.0")
    if not 3.0 <= args.settle_seconds <= 5.0:
        raise ValueError("--settle-seconds must stay between 3 and 5 seconds")

    pdf_path = Path(args.output) if args.output else html_path.with_suffix(".pdf")
    png_dir = (
        Path(args.png_dir)
        if args.png_dir
        else pdf_path.with_name(f"{pdf_path.stem}_pdf_pages")
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised by CLI users.
        raise RuntimeError(
            "Missing Playwright. Install skill dependencies with scripts/setup-env.sh, "
            "then run: .venv/bin/python -m playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        try:
            page.goto(file_url(html_path), wait_until="load")
            page.wait_for_load_state("networkidle")
            page.evaluate("() => document.fonts ? document.fonts.ready : true")
            canvas_state = wait_for_canvas_charts(
                page,
                required_charts=args.required_charts,
                timeout_seconds=args.canvas_timeout,
            )
            time.sleep(args.settle_seconds)
            page.emulate_media(media="print")
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                scale=args.scale,
                margin=DEFAULT_PDF_MARGIN,
                prefer_css_page_size=False,
            )
        finally:
            browser.close()

    png_paths = render_pdf_pages(pdf_path, png_dir)
    chart_pages = detect_chart_pages(pdf_path)
    if canvas_state.get("nonblankCount", 0) and not chart_pages:
        raise RuntimeError("PDF contains rendered canvases but no detectable chart page titles")
    check_rendered_pages(png_paths, chart_pages)
    return pdf_path, png_paths


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        pdf_path, png_paths = export_pdf(args)
    except Exception as exc:
        print(f"PDF export failed: {exc}", file=sys.stderr)
        return 1
    print(f"PDF written to {pdf_path}")
    print(f"Rendered {len(png_paths)} PDF page PNG(s) to {png_paths[0].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
