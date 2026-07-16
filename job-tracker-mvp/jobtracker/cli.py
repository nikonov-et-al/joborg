from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import ensure_project_dirs, load_config
from .files import copy_or_move_file, ensure_dir
from .llm import default_extraction, extract_job_data_with_llm, test_llm
from .markdown import append_to_markdown_body, read_markdown_with_frontmatter, update_markdown_frontmatter, write_markdown_with_frontmatter
from .models import JobApplication, format_date_display, today_iso
from .reports import export_report, load_jobs, write_report
from .wizard import (
    ask_bool,
    ask_choice,
    ask_date,
    ask_job_description,
    ask_text,
    choose_file_from_input,
    choose_job_folder,
    choose_many_files_from_input,
    confirm_extracted_fields,
)

app = typer.Typer(help="Local job application tracker.")
console = Console()


@app.command()
def version() -> None:
    """Show version."""
    from . import __version__

    console.print(__version__)


@app.command()
def init() -> None:
    """Create required folders."""
    config = load_config()
    ensure_project_dirs(config)

    for path in [
        Path(config["paths"]["input"]),
        Path(config["paths"]["jobs"]),
        Path(config["paths"]["reports"]),
        Path(config["paths"]["profile"]),
    ]:
        console.print(f"[green]OK[/green] {path}/")

    console.print("[bold green]Project folders are ready.[/bold green]")


@app.command("ai-test")
def ai_test() -> None:
    """Test local LLM connection."""
    config = load_config()
    ok, message = test_llm(config)

    if ok:
        console.print(f"[bold green]OK[/bold green] {message}")
    else:
        console.print(f"[bold red]LLM test failed:[/bold red] {message}")
        raise typer.Exit(code=1)


def build_job_body(job_description: str) -> str:
    return f"""
# Job description

See `job_description.md`.

# Comments

# Timeline

| Date | Event | Evidence |
|---|---|---|
| {format_date_display(today_iso())} | Application record created | |

# Original pasted job description preview

{job_description[:2000]}
""".strip()


def attach_application_documents(job: JobApplication, job_dir: Path, input_dir: Path) -> JobApplication:
    """
    Attach files that exist at application time.

    Employer responses and later evidence should be added with `job attach`,
    because confirmations, rejections, interview invites, and screenshots
    usually arrive after the job record has already been created.
    """
    console.print()
    console.print("[bold]Attach application documents from input/[/bold]")
    console.print("This step is for documents you sent or used while applying.")
    console.print("Employer replies/evidence should be added later with: job attach")
    console.print("Skip any field by pressing Enter.")
    console.print()

    cv = choose_file_from_input(input_dir, "Select CV file")
    letter = choose_file_from_input(input_dir, "Select motivation letter file")
    extra_application_files = choose_many_files_from_input(
        input_dir,
        "Select other application files, e.g. job description PDF or portfolio",
    )

    planned: list[tuple[str, Path]] = []

    if cv:
        planned.append((cv, job_dir / "cv" / Path(cv).name))
    if letter:
        planned.append((letter, job_dir / "motivation_letter" / Path(letter).name))
    for filename in extra_application_files:
        planned.append((filename, job_dir / "attachments" / filename))

    if not planned:
        return job

    console.print()
    console.print("[bold]Move summary[/bold]")
    for filename, destination in planned:
        console.print(f"{input_dir / filename} -> {destination}")

    if not ask_bool("Move these files?", default=False):
        console.print("[yellow]No files moved.[/yellow]")
        return job

    if cv:
        moved = copy_or_move_file(input_dir, cv, job_dir / "cv" / Path(cv).name, move=True)
        job.cv_file = moved.relative_to(job_dir).as_posix()

    if letter:
        moved = copy_or_move_file(input_dir, letter, job_dir / "motivation_letter" / Path(letter).name, move=True)
        job.motivation_letter_file = moved.relative_to(job_dir).as_posix()

    for filename in extra_application_files:
        moved = copy_or_move_file(input_dir, filename, job_dir / "attachments" / filename, move=True)
        job.attachments.append(moved.relative_to(job_dir).as_posix())

    return job


@app.command()
def add(
    no_ai: bool = typer.Option(False, "--no-ai", help="Disable local LLM extraction."),
) -> None:
    """Add a new job application."""
    config = load_config()
    ensure_project_dirs(config)

    input_dir = Path(config["paths"]["input"])
    jobs_dir = Path(config["paths"]["jobs"])

    job_description = ask_job_description()

    if not job_description:
        console.print("[yellow]No job description pasted. Continuing with manual entry.[/yellow]")

    extracted = default_extraction()

    use_ai = (
        not no_ai
        and bool(config["llm"].get("enabled", True))
        and bool(job_description.strip())
    )

    if use_ai:
        console.print()
        console.print("[bold]Trying local LLM extraction...[/bold]")
        try:
            extracted = extract_job_data_with_llm(job_description, config)
            console.print("[green]LLM extraction finished.[/green]")
        except Exception as exc:
            console.print(f"[yellow]LLM extraction failed. Falling back to manual entry.[/yellow]")
            console.print(f"[dim]{exc}[/dim]")

    confirmed = confirm_extracted_fields(extracted)
    job = JobApplication.from_metadata(confirmed)

    job_dir = jobs_dir / job.folder_name()

    if job_dir.exists():
        console.print(f"[red]Job folder already exists:[/red] {job_dir}")
        raise typer.Exit(code=1)

    ensure_dir(job_dir)
    ensure_dir(job_dir / "evidence")
    ensure_dir(job_dir / "screenshots")
    ensure_dir(job_dir / "attachments")
    ensure_dir(job_dir / "cv")
    ensure_dir(job_dir / "motivation_letter")

    (job_dir / "job_description.md").write_text(
        f"# Job description\n\n{job_description.strip()}\n",
        encoding="utf-8",
    )

    job = attach_application_documents(job, job_dir, input_dir)

    write_markdown_with_frontmatter(
        job_dir / "job.md",
        job.to_metadata(),
        build_job_body(job_description),
    )

    report_path = Path("report.md")
    write_report(
        jobs_dir=jobs_dir,
        output_path=report_path,
        applicant_name=config["applicant"].get("name", ""),
    )

    console.print()
    console.print(f"[bold green]Created job folder:[/bold green] {job_dir}")
    console.print(f"[green]Updated report:[/green] {report_path}")


@app.command()
def report(
    month: Optional[str] = typer.Option(None, "--month", help="Month filter, e.g. 2026-06."),
) -> None:
    """Generate report.md or monthly report."""
    config = load_config()
    ensure_project_dirs(config)

    jobs_dir = Path(config["paths"]["jobs"])

    if month:
        output_path = Path(config["paths"]["reports"]) / month / "report.md"
    else:
        output_path = Path("report.md")

    write_report(
        jobs_dir=jobs_dir,
        output_path=output_path,
        applicant_name=config["applicant"].get("name", ""),
        month=month,
    )

    console.print(f"[bold green]Report written:[/bold green] {output_path}")


@app.command()
def export(
    month: Optional[str] = typer.Option(None, "--month", help="Month filter, e.g. 2026-06."),
) -> None:
    """Export report.md to DOCX/PDF using Pandoc."""
    config = load_config()

    if month:
        report_path = Path(config["paths"]["reports"]) / month / "report.md"
        if not report_path.exists():
            write_report(
                jobs_dir=Path(config["paths"]["jobs"]),
                output_path=report_path,
                applicant_name=config["applicant"].get("name", ""),
                month=month,
            )
    else:
        report_path = Path("report.md")
        if not report_path.exists():
            write_report(
                jobs_dir=Path(config["paths"]["jobs"]),
                output_path=report_path,
                applicant_name=config["applicant"].get("name", ""),
            )

    try:
        created = export_report(
            jobs_dir=Path(config["paths"]["jobs"]),
            report_md=report_path,
            applicant_name=config["applicant"].get("name", ""),
            month=month,
            create_docx=bool(config["export"].get("create_docx", True)),
            create_pdf=bool(config["export"].get("create_pdf", True)),
        )
    except RuntimeError as exc:
        console.print(f"[bold yellow]{exc}[/bold yellow]")
        console.print("Install Pandoc first, then run this command again.")
        console.print("On Ubuntu/Debian: sudo apt install pandoc")
        console.print("On macOS: brew install pandoc")
        raise typer.Exit(code=1)

    for path in created:
        console.print(f"[bold green]Created:[/bold green] {path}")


@app.command()
def attach() -> None:
    """Attach later evidence/employer responses to an existing job."""
    config = load_config()
    ensure_project_dirs(config)

    input_dir = Path(config["paths"]["input"])
    jobs_dir = Path(config["paths"]["jobs"])
    jobs = load_jobs(jobs_dir)

    if not jobs:
        console.print("[yellow]No jobs found. Create one first with: job add[/yellow]")
        raise typer.Exit(code=1)

    try:
        job_dir = choose_job_folder(jobs)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1)

    job_md = job_dir / "job.md"
    metadata, body = read_markdown_with_frontmatter(job_md)
    job = JobApplication.from_metadata(metadata)

    console.print()
    console.print(f"[bold]Selected:[/bold] {job.company} — {job.role}")
    console.print("Put employer replies/screenshots/PDFs into input/ first, then select them here.")
    console.print()

    category = ask_choice(
        "What are you attaching?",
        [
            "evidence",
            "screenshots",
            "attachments",
            "cv",
            "motivation_letter",
            "job_description",
        ],
        "evidence",
    )

    selected_files = choose_many_files_from_input(input_dir, f"Select files for {category}")

    if not selected_files:
        console.print("[yellow]No files selected.[/yellow]")
        return

    planned: list[tuple[str, Path]] = []

    if category == "cv":
        for filename in selected_files:
            planned.append((filename, job_dir / "cv" / Path(filename).name))
    elif category == "motivation_letter":
        for filename in selected_files:
            planned.append((filename, job_dir / "motivation_letter" / Path(filename).name))
    elif category == "job_description":
        for filename in selected_files:
            planned.append((filename, job_dir / "attachments" / Path(filename).name))
    else:
        for filename in selected_files:
            planned.append((filename, job_dir / category / Path(filename).name))

    console.print()
    console.print("[bold]Move summary[/bold]")
    for filename, destination in planned:
        console.print(f"{input_dir / filename} -> {destination}")

    if not ask_bool("Move these files?", default=False):
        console.print("[yellow]No files moved.[/yellow]")
        return

    moved_relative_paths: list[str] = []

    for filename, destination in planned:
        moved = copy_or_move_file(input_dir, filename, destination, move=True)
        moved_relative_paths.append(moved.relative_to(job_dir).as_posix())

    if category == "evidence":
        job.evidence.extend(moved_relative_paths)
    elif category == "screenshots":
        job.screenshots.extend(moved_relative_paths)
    elif category == "attachments" or category == "job_description":
        job.attachments.extend(moved_relative_paths)
    elif category == "cv":
        # Use the last selected CV as the active CV file.
        job.cv_file = moved_relative_paths[-1]
    elif category == "motivation_letter":
        # Use the last selected letter as the active motivation letter.
        job.motivation_letter_file = moved_relative_paths[-1]

    update_status = ask_bool("Update job status now?", default=True)
    status_note = ""

    if update_status:
        job.status = ask_choice("New status", [
            "draft",
            "applied",
            "confirmation_received",
            "interview_invited",
            "interview_done",
            "task_received",
            "offer_received",
            "rejected",
            "withdrawn",
            "no_response",
            "closed",
        ], job.status)
        job.status_date = ask_date("Status date", today_iso())
        status_note = f"Status changed to {job.status}"

    note = ask_text("Timeline note", status_note or f"Attached {category}")

    update_markdown_frontmatter(job_md, job.to_metadata())

    evidence_text = ", ".join(f"`{path}`" for path in moved_relative_paths)
    append_to_markdown_body(
        job_md,
        f"""
## Update — {format_date_display(today_iso())}

- Category: {category}
- Note: {note}
- Files: {evidence_text}
""",
    )

    write_report(
        jobs_dir=jobs_dir,
        output_path=Path("report.md"),
        applicant_name=config["applicant"].get("name", ""),
    )

    console.print()
    console.print(f"[bold green]Attached files to:[/bold green] {job_dir}")
    console.print("[green]Updated job.md and report.md[/green]")


@app.command("list")
def list_jobs(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    month: Optional[str] = typer.Option(None, "--month", help="Filter by month, e.g. 2026-06."),
) -> None:
    """List job applications."""
    config = load_config()
    jobs = load_jobs(Path(config["paths"]["jobs"]))

    if status:
        jobs = [(folder, job) for folder, job in jobs if job.status == status]

    if month:
        jobs = [(folder, job) for folder, job in jobs if job.date_applied.startswith(month)]

    table = Table(title="Job Applications")
    table.add_column("Date")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Folder")

    for folder, job in jobs:
        table.add_row(format_date_display(job.date_applied), job.company, job.role, job.status, folder.as_posix())

    console.print(table)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind."),
    port: int = typer.Option(8000, "--port", help="Port to bind."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto reload."),
) -> None:
    """Run FastAPI backend."""
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        console.print("[bold red]uvicorn is not installed.[/bold red]")
        console.print("Install dependencies, then run `job web` again.")
        raise typer.Exit(code=1) from exc

    uvicorn.run("jobtracker.webapp:app", host=host, port=port, reload=reload)
