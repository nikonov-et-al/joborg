from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from typing import BinaryIO, Sequence
from xml.sax.saxutils import escape

from importlib.resources import as_file, files

from pypdf import PdfReader
from reportlab.lib.colors import black
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.platypus.doctemplate import LayoutError


PAGE_SIZE = A4
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE
MARGIN = 10 * mm
BODY_FONT_SIZE = 11
FONT_FAMILY = "NotoSansDisplay"
FONT_REGULAR = "NotoSansDisplay-Regular"
FONT_BOLD = "NotoSansDisplay-Bold"
FONT_ITALIC = "NotoSansDisplay-Italic"
# Noto Sans Display covers Latin/Cyrillic here; bundled fonts use SIL OFL.
_FONT_FILES = {
    FONT_REGULAR: "NotoSans-DisplayRegular.ttf",
    FONT_BOLD: "NotoSans-DisplayBold.ttf",
    FONT_ITALIC: "NotoSans-DisplayItalic.ttf",
}
_REGISTERED = False
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
_CV_LAYOUTS = (
    {
        "margin": 10 * mm,
        "body_space_after": 1.8 * mm,
        "body_leading": 13,
        "heading_space_before": 2 * mm,
        "heading_space_after": 0.8 * mm,
        "header_gap": 1.5 * mm,
        "title_size": 15,
        "title_leading": 17,
        "subtitle_size": 11,
        "subtitle_leading": 13,
        "heading_size": 13,
        "heading_leading": 15,
    },
    {
        "margin": 8 * mm,
        "body_space_after": 1.2 * mm,
        "body_leading": 12,
        "heading_space_before": 1.2 * mm,
        "heading_space_after": 0.5 * mm,
        "header_gap": 1.0 * mm,
        "title_size": 14.5,
        "title_leading": 16,
        "subtitle_size": 10.5,
        "subtitle_leading": 12,
        "heading_size": 13,
        "heading_leading": 15,
    },
)


def _font_asset_path(filename: str):
    return files("jobtracker").joinpath("assets", "fonts", filename)


def _register_ttfont(font_name: str, filename: str) -> None:
    with as_file(_font_asset_path(filename)) as path:
        pdfmetrics.registerFont(TTFont(font_name, str(path)))


def register_pdf_fonts() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    for font_name, filename in _FONT_FILES.items():
        _register_ttfont(font_name, filename)

    pdfmetrics.registerFontFamily(
        FONT_FAMILY,
        normal=FONT_REGULAR,
        bold=FONT_BOLD,
        italic=FONT_ITALIC,
        boldItalic=FONT_BOLD,
    )
    _REGISTERED = True


def get_basic_styles() -> dict[str, ParagraphStyle]:
    register_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "jobtracker-title",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=16,
            leading=19,
            textColor=black,
            alignment=TA_LEFT,
            spaceAfter=4 * mm,
        ),
        "heading": ParagraphStyle(
            "jobtracker-heading",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=14,
            textColor=black,
            alignment=TA_LEFT,
            spaceBefore=2 * mm,
            spaceAfter=1 * mm,
        ),
        "body": ParagraphStyle(
            "jobtracker-body",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=BODY_FONT_SIZE,
            leading=13,
            textColor=black,
            alignment=TA_LEFT,
            spaceAfter=2 * mm,
        ),
        "note": ParagraphStyle(
            "jobtracker-note",
            parent=base["BodyText"],
            fontName=FONT_ITALIC,
            fontSize=10,
            leading=12,
            textColor=black,
            alignment=TA_LEFT,
            spaceAfter=2 * mm,
        ),
    }


def safe_filename(value: str, default: str = "document.pdf") -> str:
    parts = [part for part in re.split(r"[\\/]+", str(value)) if part]
    name = parts[-1] if parts else ""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", " ", name, flags=re.UNICODE).strip(" ._")
    if not name:
        return default

    stem = name.split(".", 1)[0].rstrip(" .")
    if stem.upper() in _RESERVED_NAMES:
        name = f"_{name}"

    return name


_INLINE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_INLINE_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^()\s]+)\)")
_INLINE_BOLD_RE = re.compile(r"(?<!\w)(\*\*|__)(.+?)\1(?!\w)", flags=re.DOTALL)
_INLINE_ITALIC_RE = re.compile(r"(?<!\w)(\*|_)(.+?)\1(?!\w)", flags=re.DOTALL)


def _flatten_markdown_line(line: str) -> str:
    line = re.sub(r"^>\s*", "", line)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^\d+\.\s+", "", line)
    line = line.replace("|", " ")
    return re.sub(r"\s+", " ", line).strip()


def _render_inline_markdown(text: str) -> str:
    placeholders: list[tuple[str, str]] = []

    def replace_link(match: re.Match[str]) -> str:
        label = _render_inline_markdown(match.group(1))
        href = escape(match.group(2).strip(), {'"': "&quot;"})
        placeholder = f"@@LINK{len(placeholders)}@@"
        placeholders.append((placeholder, f'<a href="{href}">{label}</a>'))
        return placeholder

    text = _INLINE_IMAGE_RE.sub("", text)
    text = _INLINE_LINK_RE.sub(replace_link, text)
    text = escape(text)
    text = text.replace("`", "")
    text = _INLINE_BOLD_RE.sub(lambda match: f"<b>{match.group(2)}</b>", text)
    text = _INLINE_ITALIC_RE.sub(lambda match: f"<i>{match.group(2)}</i>", text)

    for placeholder, markup in placeholders:
        text = text.replace(placeholder, markup)

    return text


def _parse_cv_blocks(markdown_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph = _flatten_markdown_line(" ".join(paragraph_lines))
        paragraph_lines.clear()
        if paragraph:
            blocks.append(("paragraph", paragraph))

    for raw_line in markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            continue

        heading = re.match(r"^(#{1,2})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            blocks.append((f"heading{len(heading.group(1))}", heading.group(2).strip()))
            continue

        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            blocks.append(("bullet", bullet.group(1).strip()))
            continue

        paragraph_lines.append(_flatten_markdown_line(line))

    flush_paragraph()
    return blocks


def _cv_styles(layout: dict[str, float]) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "jobtracker-cv-title",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=layout["title_size"],
            leading=layout["title_leading"],
            textColor=black,
            alignment=TA_LEFT,
            spaceAfter=1.5 * mm,
        ),
        "subtitle": ParagraphStyle(
            "jobtracker-cv-subtitle",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=layout["subtitle_size"],
            leading=layout["subtitle_leading"],
            textColor=black,
            alignment=TA_LEFT,
            spaceAfter=layout["header_gap"],
        ),
        "heading": ParagraphStyle(
            "jobtracker-cv-heading",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=layout["heading_size"],
            leading=layout["heading_leading"],
            textColor=black,
            alignment=TA_LEFT,
            spaceBefore=layout["heading_space_before"],
            spaceAfter=layout["heading_space_after"],
        ),
        "body": ParagraphStyle(
            "jobtracker-cv-body",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=BODY_FONT_SIZE,
            leading=layout["body_leading"],
            textColor=black,
            alignment=TA_LEFT,
            spaceAfter=layout["body_space_after"],
            allowWidows=1,
            allowOrphans=1,
        ),
        "bullet": ParagraphStyle(
            "jobtracker-cv-bullet",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=BODY_FONT_SIZE,
            leading=layout["body_leading"],
            textColor=black,
            alignment=TA_LEFT,
            leftIndent=5 * mm,
            firstLineIndent=0,
            bulletIndent=0,
            spaceAfter=layout["body_space_after"],
            allowWidows=1,
            allowOrphans=1,
        ),
    }


def _pdf_bytes(source: bytes | bytearray | str | Path | BinaryIO) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, Path):
        return source.read_bytes()
    if isinstance(source, str):
        return Path(source).read_bytes()

    data = source.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    return bytes(data)


def pdf_page_count(source: bytes | bytearray | str | Path | BinaryIO) -> int:
    return len(PdfReader(BytesIO(_pdf_bytes(source))).pages)


def validate_pdf_page_count(
    source: bytes | bytearray | str | Path | BinaryIO,
    expected_pages: int = 1,
) -> None:
    actual_pages = pdf_page_count(source)
    if actual_pages != expected_pages:
        raise ValueError(f"Expected {expected_pages} page(s), got {actual_pages}.")


def render_basic_pdf(title: str, paragraphs: Sequence[str]) -> bytes:
    register_pdf_fonts()
    styles = get_basic_styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=title,
        author="jobtracker",
    )

    story = [Paragraph(escape(title), styles["title"])]
    for paragraph in paragraphs:
        story.append(Paragraph(escape(paragraph).replace("\n", "<br/>"), styles["body"]))

    document.build(story)
    pdf = buffer.getvalue()
    validate_pdf_page_count(pdf, 1)
    return pdf


def _build_cv_story(
    markdown_text: str,
    applicant_name: str,
    job_title: str,
    *,
    styles: dict[str, ParagraphStyle],
) -> list[Paragraph | Spacer]:
    story: list[Paragraph | Spacer] = []

    if applicant_name.strip():
        story.append(Paragraph(_render_inline_markdown(applicant_name.strip()), styles["title"]))
    if job_title.strip():
        story.append(Paragraph(_render_inline_markdown(job_title.strip()), styles["subtitle"]))

    if story:
        story.append(Spacer(1, 0.25 * mm))

    for kind, text in _parse_cv_blocks(markdown_text):
        rendered = _render_inline_markdown(text)
        if kind.startswith("heading"):
            story.append(Paragraph(rendered, styles["heading"]))
        elif kind == "bullet":
            story.append(Paragraph(rendered, styles["bullet"], bulletText="•"))
        else:
            story.append(Paragraph(rendered, styles["body"]))

    return story


def _render_cv_pdf_once(
    markdown_text: str,
    applicant_name: str,
    job_title: str,
    *,
    layout: dict[str, float],
) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=layout["margin"],
        rightMargin=layout["margin"],
        topMargin=layout["margin"],
        bottomMargin=layout["margin"],
        title=applicant_name or job_title or "CV",
        author="jobtracker",
    )
    story = _build_cv_story(markdown_text, applicant_name, job_title, styles=_cv_styles(layout))
    document.build(story)
    pdf = buffer.getvalue()
    if pdf_page_count(pdf) != 1:
        raise ValueError("CV content does not fit one page")
    return pdf


def render_cv_pdf(markdown_text: str, applicant_name: str = "", job_title: str = "") -> bytes:
    register_pdf_fonts()

    last_error: Exception | None = None
    for layout in _CV_LAYOUTS:
        try:
            return _render_cv_pdf_once(
                markdown_text,
                applicant_name,
                job_title,
                layout=layout,
            )
        except LayoutError as exc:
            last_error = exc
        except ValueError as exc:
            if str(exc) == "CV content does not fit one page":
                last_error = exc
            else:
                raise

    raise ValueError("CV content does not fit one page") from last_error
