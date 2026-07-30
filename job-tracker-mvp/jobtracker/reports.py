from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
import calendar
import logging
import shutil
import subprocess
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .loading import (
    LoadJobError,
    LoadJobsResult,
    make_job_load_error,
    validate_job_load_metadata,
)
from .markdown import atomic_write_text, read_markdown_with_frontmatter
from .models import JobApplication, format_date_display
from .pdf import FONT_BOLD, FONT_REGULAR, register_pdf_fonts


REPORT_PAGE_SIZE = landscape(A4)
REPORT_MARGIN = 10 * mm
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportRow:
    date: str
    company: str
    role: str
    location: str
    method: str
    status: str
    evidence: str
    folder: str
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReportData:
    applicant_name: str
    month: str
    year: str
    month_number: str
    period_label: str
    generated_at: str
    loaded_count: int
    failed_count: int
    total: int
    status_counts: tuple[tuple[str, int], ...]
    rows: tuple[ReportRow, ...]
    load_errors: tuple[LoadJobError, ...] = ()


def find_job_files(jobs_dir: Path) -> list[Path]:
    if not jobs_dir.exists():
        return []
    return sorted(
        directory / "job.md"
        for directory in jobs_dir.iterdir()
        if directory.is_dir() and not directory.name.startswith(".")
    )


def load_jobs(jobs_dir: Path) -> LoadJobsResult:
    jobs: list[tuple[Path, JobApplication]] = []
    errors: list[LoadJobError] = []

    for path in find_job_files(jobs_dir):
        try:
            metadata, _body = read_markdown_with_frontmatter(path)
            validate_job_load_metadata(metadata)
            jobs.append((path.parent, JobApplication.from_metadata(metadata)))
        except Exception as exc:
            error = make_job_load_error(path, jobs_dir, exc)
            errors.append(error)
            log = logger.warning if error.code == "unexpected_load_error" else logger.info
            log(
                "Job record load failed path=%s code=%s exception_type=%s",
                error.path,
                error.code,
                error.exception_type,
            )

    return LoadJobsResult(
        jobs=sorted(jobs, key=lambda item: item[1].date_applied or ""),
        errors=errors,
    )


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _detail_items(job: JobApplication) -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = [
        ("Status", job.status),
        ("Location", job.location),
        ("Source", job.source),
        ("Application method", job.application_method),
    ]
    if job.job_url:
        items.append(("Job URL", job.job_url))
    if job.summary:
        items.append(("Summary", job.summary))
    if job.required_skills:
        items.append(("Required skills", ", ".join(job.required_skills)))
    if job.language_requirements:
        items.append(("Languages", ", ".join(job.language_requirements)))
    if job.cv_file:
        items.append(("CV", job.cv_file))
    if job.motivation_letter_file:
        items.append(("Motivation letter", job.motivation_letter_file))
    if job.evidence:
        items.append(("Evidence", ", ".join(job.evidence)))
    if job.screenshots:
        items.append(("Screenshots", ", ".join(job.screenshots)))
    return tuple(items)


def _period_parts(
    month: str | None,
    year: str | int | None,
    month_number: str | int | None,
) -> tuple[str, str, str]:
    legacy_year = ""
    legacy_month = ""
    if month:
        parts = month.split("-", 1)
        if len(parts) == 2 and len(parts[0]) == 4 and parts[0].isdigit() and parts[1].isdigit():
            legacy_year, legacy_month = parts

    selected_year = str(year).strip() if year is not None else legacy_year
    if selected_year and (len(selected_year) != 4 or not selected_year.isdigit()):
        raise ValueError("Year must use YYYY format.")

    raw_month = str(month_number).strip() if month_number is not None else legacy_month
    if raw_month:
        if not raw_month.isdigit() or not 1 <= int(raw_month) <= 12:
            raise ValueError("Month must be between 1 and 12.")
        selected_month = f"{int(raw_month):02d}"
    else:
        selected_month = ""

    if selected_year and selected_month:
        period_label = f"{calendar.month_name[int(selected_month)]} {selected_year}"
    elif selected_year:
        period_label = selected_year
    elif selected_month:
        period_label = f"{calendar.month_name[int(selected_month)]} (all years)"
    else:
        period_label = "All"
    return selected_year, selected_month, period_label


def build_report_data(
    jobs_dir: Path,
    applicant_name: str = "",
    month: str | None = None,
    year: str | int | None = None,
    month_number: str | int | None = None,
) -> ReportData:
    return build_report_data_from_jobs(
        load_jobs(jobs_dir),
        applicant_name=applicant_name,
        month=month,
        year=year,
        month_number=month_number,
    )


def build_report_data_from_jobs(
    loaded: LoadJobsResult,
    applicant_name: str = "",
    month: str | None = None,
    year: str | int | None = None,
    month_number: str | int | None = None,
) -> ReportData:
    jobs = loaded.jobs
    selected_year, selected_month, period_label = _period_parts(month, year, month_number)
    if selected_year:
        jobs = [(folder, job) for folder, job in jobs if job.date_applied[:4] == selected_year]
    if selected_month:
        jobs = [(folder, job) for folder, job in jobs if job.date_applied[5:7] == selected_month]

    generated_at = datetime.now().strftime("%d-%m-%Y %H:%M")
    counts = Counter(job.status for _folder, job in jobs)

    rows = tuple(
        ReportRow(
            date=format_date_display(job.date_applied),
            company=job.company,
            role=job.role,
            location=job.location,
            method=job.application_method,
            status=job.status,
            evidence=yes_no(bool(job.evidence or job.screenshots or job.attachments)),
            folder=folder.name if isinstance(folder, Path) else folder,
            details=_detail_items(job),
        )
        for folder, job in jobs
    )

    return ReportData(
        applicant_name=applicant_name,
        month=(month or (f"{selected_year}-{selected_month}" if selected_year and selected_month else "")),
        year=selected_year,
        month_number=selected_month,
        period_label=period_label,
        generated_at=generated_at,
        loaded_count=loaded.loaded_count,
        failed_count=loaded.failed_count,
        total=len(rows),
        status_counts=tuple(sorted(counts.items())),
        rows=rows,
        load_errors=tuple(loaded.errors),
    )


def _markdown_lines(data: ReportData) -> list[str]:
    lines: list[str] = ["# Job Application Report", ""]
    if data.applicant_name:
        lines.append(f"Applicant: {data.applicant_name}  ")
    lines.append(f"Reporting period: {data.period_label}  ")
    lines.append(f"Generated: {data.generated_at}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total applications: **{data.total}**")
    lines.append(f"- Loaded records: **{data.loaded_count}**")
    lines.append(f"- Failed records: **{data.failed_count}**")
    for status, count in data.status_counts:
        percentage = count / data.total * 100
        filled = int(percentage / 5 + 0.5)
        bar = "█" * filled + "░" * (20 - filled)
        lines.append(f"- {status}: **{count}** ({percentage:.1f}%) `{bar}`")
    lines.append("")
    if data.load_errors:
        lines.append("## Load errors")
        lines.append("")
        for error in data.load_errors:
            lines.append(f"- `{error.path}` (`{error.code}`): {error.message}")
        lines.append("")
    lines.append("## Applications")
    lines.append("")
    if data.rows:
        lines.append("| Date | Company | Role | Location | Status |")
        lines.append("|---|---|---|---|---|")
        for row in data.rows:
            lines.append(f"| {row.date} | {row.company} | {row.role} | {row.location} | {row.status} |")
    else:
        lines.append("No applications found.")
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for row in data.rows:
        lines.append(f"### {row.date} — {row.company} — {row.role}")
        lines.append("")
        for label, value in row.details:
            if value:
                lines.append(f"- {label}: {value}")
        lines.append("")
    return lines


def report_data_to_markdown(data: ReportData) -> str:
    return "\n".join(_markdown_lines(data)).strip() + "\n"


def generate_report_md(
    jobs_dir: Path,
    applicant_name: str = "",
    month: str | None = None,
    year: str | int | None = None,
    month_number: str | int | None = None,
) -> str:
    return report_data_to_markdown(
        build_report_data(
            jobs_dir,
            applicant_name=applicant_name,
            month=month,
            year=year,
            month_number=month_number,
        )
    )


def write_report_data(data: ReportData, output_path: Path) -> None:
    atomic_write_text(output_path, report_data_to_markdown(data))


def write_report(
    jobs_dir: Path,
    output_path: Path,
    applicant_name: str = "",
    month: str | None = None,
    year: str | int | None = None,
    month_number: str | int | None = None,
) -> ReportData:
    data = build_report_data(
        jobs_dir,
        applicant_name=applicant_name,
        month=month,
        year=year,
        month_number=month_number,
    )
    atomic_write_text(output_path, report_data_to_markdown(data))
    return data


def _report_styles() -> dict[str, ParagraphStyle]:
    register_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "jobtracker-report-title",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=18,
            leading=22,
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=3 * mm,
        ),
        "body": ParagraphStyle(
            "jobtracker-report-body",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=10,
            leading=12,
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=1.5 * mm,
        ),
        "small": ParagraphStyle(
            "jobtracker-report-small",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9,
            leading=10.5,
            textColor=colors.black,
            alignment=TA_LEFT,
        ),
    }


def _cell(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _summary_chart(data: ReportData) -> Drawing:
    chart_height = max(18 * mm, len(data.status_counts) * 8 * mm)
    drawing = Drawing(250 * mm, chart_height + 8 * mm)
    chart = HorizontalBarChart()
    chart.x = 48 * mm
    chart.y = 4 * mm
    chart.width = 165 * mm
    chart.height = chart_height
    chart.data = [[count / data.total * 100 for _status, count in data.status_counts]]
    chart.categoryAxis.categoryNames = [status for status, _count in data.status_counts]
    chart.categoryAxis.labels.fontName = FONT_REGULAR
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 20
    chart.valueAxis.labels.fontName = FONT_REGULAR
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labelTextFormat = "%d%%"
    chart.bars[0].fillColor = colors.HexColor("#526d9d")
    chart.barLabels.fontName = FONT_REGULAR
    chart.barLabels.fontSize = 7
    chart.barLabels.fillColor = colors.black
    chart.barLabelFormat = "%.1f%%"
    drawing.add(chart)
    return drawing


def render_report_pdf(data: ReportData) -> bytes:
    styles = _report_styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=REPORT_PAGE_SIZE,
        leftMargin=REPORT_MARGIN,
        rightMargin=REPORT_MARGIN,
        topMargin=REPORT_MARGIN,
        bottomMargin=REPORT_MARGIN,
        title="Job Application Report",
        author="jobtracker",
    )

    story: list[Any] = [
        Paragraph("Job Application Report", styles["title"]),
        Paragraph(escape(f"Generated: {data.generated_at}"), styles["body"]),
    ]
    if data.applicant_name:
        story.append(Paragraph(escape(f"Applicant: {data.applicant_name}"), styles["body"]))
    story.append(Paragraph(escape(f"Reporting period: {data.period_label}"), styles["body"]))

    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Summary", styles["body"]))
    story.append(Paragraph(escape(f"Total applications: {data.total}"), styles["small"]))
    story.append(Paragraph(escape(f"Loaded records: {data.loaded_count}"), styles["small"]))
    story.append(Paragraph(escape(f"Failed records: {data.failed_count}"), styles["small"]))
    if data.status_counts:
        story.append(
            Paragraph(
                escape(
                    ", ".join(
                        f"{status}: {count} ({count / data.total * 100:.1f}%)"
                        for status, count in data.status_counts
                    )
                ),
                styles["small"],
            )
        )
        story.append(Spacer(1, 1 * mm))
        story.append(_summary_chart(data))

    story.append(Spacer(1, 2 * mm))
    if not data.rows:
        story.append(Paragraph("No applications found.", styles["small"]))
        document.build(story)
        return buffer.getvalue()

    table_data = [
        [
            _cell("Date", styles["small"]),
            _cell("Company", styles["small"]),
            _cell("Role", styles["small"]),
            _cell("Location", styles["small"]),
            _cell("Status", styles["small"]),
        ]
    ]
    for row in data.rows:
        table_data.append(
            [
                _cell(row.date, styles["small"]),
                _cell(row.company, styles["small"]),
                _cell(row.role, styles["small"]),
                _cell(row.location, styles["small"]),
                _cell(row.status, styles["small"]),
            ]
        )

    table = Table(
        table_data,
        colWidths=[35 * mm, 60 * mm, 75 * mm, 55 * mm, 40 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b8c2d6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)

    document.build(story)
    return buffer.getvalue()


def write_report_pdf(data: ReportData, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(render_report_pdf(data))


def export_report(
    *,
    jobs_dir: Path,
    report_md: Path,
    applicant_name: str = "",
    month: str | None = None,
    year: str | int | None = None,
    month_number: str | int | None = None,
    create_docx: bool = True,
    create_pdf: bool = True,
) -> list[Path]:
    if not report_md.exists():
        raise FileNotFoundError(f"Report file not found: {report_md}")

    created: list[Path] = []

    if create_docx:
        pandoc = shutil.which("pandoc")
        if not pandoc:
            if not create_pdf:
                raise RuntimeError("Pandoc is not installed or not found in PATH.")
        else:
            docx_path = report_md.with_suffix(".docx")
            subprocess.run([pandoc, str(report_md), "-o", str(docx_path)], check=True)
            created.append(docx_path)

    if create_pdf:
        data = build_report_data(
            jobs_dir,
            applicant_name=applicant_name,
            month=month,
            year=year,
            month_number=month_number,
        )
        pdf_path = report_md.with_suffix(".pdf")
        write_report_pdf(data, pdf_path)
        created.append(pdf_path)

    return created


def report_data_to_dict(data: ReportData) -> dict[str, Any]:
    return asdict(data)
