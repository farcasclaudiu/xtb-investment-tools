import importlib.util
from pathlib import Path


PDF_EXPORT_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "xtb-portfolio-review"
    / "scripts"
    / "pdf_export.py"
)


def load_pdf_export():
    spec = importlib.util.spec_from_file_location("xtb_pdf_export", PDF_EXPORT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pdf_export_defaults_match_skill_validation_contract():
    pdf_export = load_pdf_export()

    assert pdf_export.DEFAULT_PDF_SCALE == 0.8
    assert pdf_export.DEFAULT_PDF_MARGIN == {
        "top": "0.4in",
        "right": "0.4in",
        "bottom": "0.4in",
        "left": "0.4in",
    }
    assert 3.0 <= pdf_export.DEFAULT_PRINT_SETTLE_SECONDS <= 5.0
    assert pdf_export.DEFAULT_REQUIRED_CHARTS == 3


def test_canvas_check_script_detects_nonblank_pixels_and_reports_blank_canvas():
    pdf_export = load_pdf_export()

    script = pdf_export.CANVAS_READINESS_SCRIPT

    assert "document.fonts.ready" in script
    assert "getImageData" in script
    assert "nonblank" in script
    assert "blank" in script


def test_pdf_export_source_uses_playwright_pdf_scale_and_pdf_png_rendering():
    source = PDF_EXPORT_PATH.read_text(encoding="utf-8")

    assert "page.pdf(" in source
    assert "scale=args.scale" in source
    assert "margin=DEFAULT_PDF_MARGIN" in source
    assert "--force-device-scale-factor" not in source
    assert "detect_chart_pages" in source
    assert "CHART_PAGE_MARKERS" in source
    assert "fitz.open" in source
    assert "get_pixmap" in source
