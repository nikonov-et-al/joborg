from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
import webbrowser

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .application import JobTrackerService
from .config import ensure_project_dirs, load_config
from .doctor import SCHEMA_VERSION, DoctorFatalError, inspect_jobs
from .llm import (
    LLMError,
    classify_llm_endpoint,
    default_extraction,
    extract_job_data_with_llm,
    test_llm,
)
from .loading import LoadJobError
from .migration import execute_migration, preview_migration, verify_migration
from .models import JobApplication, format_date_display, today_iso
from .repositories import build_application_service
from .paths import AppPaths
from .services import AttachmentCopyError, ProjectContext, UploadedAsset
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
migrate_app = typer.Typer(help="Import read-only legacy Markdown data into SQLite.")
app.add_typer(migrate_app, name="migrate")
console = Console()


def _application_service(config: dict) -> JobTrackerService:
    paths = config["paths"]
    app_paths = AppPaths.resolve()
    uses_runtime_store = Path(paths["jobs"]).resolve() == app_paths.jobs_dir
    context = ProjectContext(
        config=config,
        input_dir=Path(paths.get("input", "input")),
        jobs_dir=Path(paths["jobs"]),
        reports_dir=Path(paths.get("reports", "reports")),
        profile_dir=Path(paths.get("profile", "profile")),
        database=app_paths.database if uses_runtime_store else None,
        files_dir=app_paths.files_dir if uses_runtime_store else None,
        legacy_jobs_dir=app_paths.legacy_jobs_dir,
    )
    return build_application_service(context)


def _migration_paths(
    legacy_path: Path | None,
    data_dir: Path | None,
) -> tuple[Path, AppPaths]:
    paths = AppPaths.resolve(data_dir=data_dir, legacy_jobs_dir=legacy_path)
    legacy = paths.legacy_jobs_dir
    if legacy is None:
        raise typer.BadParameter(
            "Provide --legacy-path or set JOBTRACKER_LEGACY_JOBS_DIR."
        )
    return legacy, paths


def _print_load_errors(errors: list[LoadJobError] | tuple[LoadJobError, ...]) -> None:
    console.print(f"[bold red]Failed records: {len(errors)}[/bold red]")
    for error in errors:
        console.print(f"[red]{error.path}[/red] [{error.code}]: {error.message}")
    console.print("Run [bold]job doctor[/bold] for details.")


def _print_load_summary(
    loaded_count: int,
    errors: list[LoadJobError] | tuple[LoadJobError, ...],
) -> None:
    console.print(f"Loaded records: {loaded_count}")
    if errors:
        _print_load_errors(errors)
    else:
        console.print("Failed records: 0")


@app.command()
def version() -> None:
    """Show version."""
    from . import __version__

    console.print(__version__)


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    legacy_path: Path | None = typer.Option(
        None, "--legacy-path", help="Legacy jobs directory to inspect."
    ),
) -> None:
    """Inspect legacy job records without changing them."""
    try:
        config = load_config()
        configured_jobs = legacy_path or AppPaths.resolve().legacy_jobs_dir or config["paths"]["jobs"]
        if not isinstance(configured_jobs, (str, Path)) or not str(configured_jobs).strip():
            raise TypeError("paths.jobs must be a non-empty filesystem path")
        report = inspect_jobs(Path(configured_jobs))
    except DoctorFatalError as exc:
        fatal = exc.to_dict()
    except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
        fatal = {
            "code": "configuration_error",
            "path": "config.yml",
            "message": f"{type(exc).__name__}: {exc}",
        }
    else:
        fatal = None

    if fatal is not None:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "summary": None,
                        "records": [],
                        "attachments": [],
                        "issues": [],
                        "fatal": fatal,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            console.print(f"[bold red]Doctor failed:[/bold red] {fatal['message']}")
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        console.print(
            f"Job directories: {report.job_directory_count}; "
            f"records parsed: {report.parsed_count}; "
            f"records failed: {report.failed_count}; "
            f"physical attachments: {len(report.attachments)}; "
            f"referenced attachments: {report.referenced_attachment_count}"
        )
        if report.issues:
            table = Table(title="Job Doctor Findings")
            table.add_column("Severity")
            table.add_column("Code")
            table.add_column("Record")
            table.add_column("Field")
            table.add_column("Message")
            for issue in report.issues:
                table.add_row(
                    issue.severity,
                    issue.code,
                    issue.record_path or "",
                    issue.field or "",
                    issue.message,
                )
            console.print(table)
        else:
            console.print("[green]No issues found.[/green]")


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


def _print_migration_report(report: object, json_output: bool) -> None:
    data = report.to_dict()
    if json_output:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    console.print(
        f"Mode: {data['mode']}; discovered: {data['records_discovered']}; "
        f"importable: {data['records_importable']}; attachments: {data['attachments']}; "
        f"imported: {data['imported']}; skipped: {data['skipped']}; verified: {data['verified']}"
    )
    for error in data["blocking_errors"]:
        console.print(f"[red]{error.get('code', 'error')}[/red]: {error.get('message', '')}")


@migrate_app.command("preview")
def migrate_preview(
    legacy_path: Path | None = typer.Option(None, "--legacy-path"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Inspect legacy data and expected destination changes without writing."""
    legacy, paths = _migration_paths(legacy_path, data_dir)
    report = preview_migration(legacy, paths.database)
    _print_migration_report(report, json_output)
    if not report.ok:
        raise typer.Exit(code=1)


@migrate_app.command("execute")
def migrate_execute(
    legacy_path: Path | None = typer.Option(None, "--legacy-path"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Copy verified legacy data into the SQLite store without changing sources."""
    legacy, paths = _migration_paths(legacy_path, data_dir)
    report = execute_migration(legacy, paths.database, paths.files_dir)
    _print_migration_report(report, json_output)
    if not report.ok:
        raise typer.Exit(code=1)


@migrate_app.command("verify")
def migrate_verify(
    legacy_path: Path | None = typer.Option(None, "--legacy-path"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Compare imported rows and copied-file hashes with legacy sources."""
    legacy, paths = _migration_paths(legacy_path, data_dir)
    report = verify_migration(legacy, paths.database, paths.files_dir)
    _print_migration_report(report, json_output)
    if not report.ok:
        raise typer.Exit(code=1)


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


def collect_application_documents(job_dir: Path, input_dir: Path) -> list[UploadedAsset]:
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

    planned: list[tuple[str, Path, str]] = []

    if cv:
        planned.append((cv, job_dir / "cv" / Path(cv).name, "cv"))
    if letter:
        planned.append((letter, job_dir / "motivation_letter" / Path(letter).name, "motivation_letter"))
    for filename in extra_application_files:
        planned.append((filename, job_dir / "attachments" / filename, "attachments"))

    if not planned:
        return []

    console.print()
    console.print("[bold]Copy summary[/bold]")
    for filename, destination, _category in planned:
        console.print(f"{input_dir / filename} -> {destination}")

    if not ask_bool("Copy these files?", default=False):
        console.print("[yellow]No files copied.[/yellow]")
        return []

    assets: list[UploadedAsset] = []
    for filename, _destination, category in planned:
        source = input_dir / filename
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise AttachmentCopyError(filename) from exc
        assets.append(UploadedAsset(filename=filename, content=content, category=category))

    return assets


@app.command()
def add(
    no_ai: bool = typer.Option(False, "--no-ai", help="Disable local LLM extraction."),
) -> None:
    """Add a new job application."""
    config = load_config()
    ensure_project_dirs(config)

    input_dir = Path(config["paths"]["input"])
    jobs_dir = Path(config["paths"]["jobs"])
    service = _application_service(config)

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
        remote_confirmation = False
        try:
            remote = classify_llm_endpoint(
                config["llm"].get("base_url", "http://localhost:11434/v1")
            ) == "remote"
            if remote:
                console.print(
                    "[bold yellow]Remote AI will send this job description outside this device.[/bold yellow]"
                )
                remote_confirmation = ask_bool(
                    "Send it to the configured remote provider?",
                    default=False,
                )
                if not remote_confirmation:
                    raise LLMError(
                        "llm_remote_confirmation_required",
                        "Remote disclosure was not confirmed.",
                    )
            else:
                console.print("[bold]Trying local LLM extraction...[/bold]")
            extracted = extract_job_data_with_llm(
                job_description,
                config,
                remote_confirmation=remote_confirmation,
            )
            console.print("[green]LLM extraction finished.[/green]")
        except LLMError as exc:
            console.print(f"[yellow]LLM extraction failed. Falling back to manual entry.[/yellow]")
            console.print(f"[dim]{exc.code}: {exc}[/dim]")
        except Exception:
            console.print("[yellow]LLM extraction failed. Falling back to manual entry.[/yellow]")
            console.print("[dim]llm_unavailable: LLM provider is unavailable.[/dim]")

    confirmed = confirm_extracted_fields(extracted)
    job = JobApplication.from_metadata(confirmed)

    job_dir = jobs_dir / job.folder_name()

    if job_dir.exists():
        console.print(f"[red]Job folder already exists:[/red] {job_dir}")
        raise typer.Exit(code=1)

    try:
        assets = collect_application_documents(job_dir, input_dir)
        result = service.create_job(job, job_description, assets)
    except OSError as exc:
        console.print(f"[bold red]Job creation failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    loaded = service.list_jobs()
    _print_load_summary(loaded.loaded_count, loaded.errors)

    console.print()
    console.print(f"[bold green]Created job:[/bold green] {result.application_id}")
    if result.report_warning:
        console.print(f"[yellow]{result.report_warning}[/yellow]")


@app.command()
def report(
    month: Optional[str] = typer.Option(None, "--month", help="Month filter, e.g. 2026-06."),
) -> None:
    """Generate report.md or monthly report."""
    config = load_config()
    ensure_project_dirs(config)
    service = _application_service(config)

    if month:
        output_path = Path(config["paths"]["reports"]) / month / "report.md"
    else:
        output_path = Path(config["paths"]["reports"]) / "report.md"

    service.write_report(month, output_path)
    loaded = service.list_jobs(month=month)
    _print_load_summary(loaded.loaded_count, loaded.errors)

    console.print(f"[bold green]Report written:[/bold green] {output_path}")
    if loaded.errors:
        raise typer.Exit(code=1)


@app.command()
def export(
    month: Optional[str] = typer.Option(None, "--month", help="Month filter, e.g. 2026-06."),
) -> None:
    """Export report.md to DOCX/PDF using Pandoc."""
    config = load_config()
    service = _application_service(config)

    try:
        created, loaded = service.export_report(month)
    except RuntimeError as exc:
        console.print(f"[bold yellow]{exc}[/bold yellow]")
        console.print("Install Pandoc first, then run this command again.")
        console.print("On Ubuntu/Debian: sudo apt install pandoc")
        console.print("On macOS: brew install pandoc")
        raise typer.Exit(code=1)

    for path in created:
        console.print(f"[bold green]Created:[/bold green] {path}")
    _print_load_summary(loaded.loaded_count, loaded.errors)
    if loaded.errors:
        raise typer.Exit(code=1)


@app.command()
def attach() -> None:
    """Attach later evidence/employer responses to an existing job."""
    config = load_config()
    ensure_project_dirs(config)

    input_dir = Path(config["paths"]["input"])
    service = _application_service(config)
    loaded = service.list_jobs()
    _print_load_summary(loaded.loaded_count, loaded.errors)
    if loaded.errors:
        raise typer.Exit(code=1)
    jobs = loaded.jobs

    if not jobs:
        console.print("[yellow]No jobs found. Create one first with: job add[/yellow]")
        raise typer.Exit(code=1)

    try:
        job_id = choose_job_folder(jobs)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1)

    job = service.get_job(job_id).job

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
            planned.append((filename, Path("managed") / job_id / "cv" / Path(filename).name))
    elif category == "motivation_letter":
        for filename in selected_files:
            planned.append((filename, Path("managed") / job_id / "motivation_letter" / Path(filename).name))
    elif category == "job_description":
        for filename in selected_files:
            planned.append((filename, Path("managed") / job_id / "attachments" / Path(filename).name))
    else:
        for filename in selected_files:
            planned.append((filename, Path("managed") / job_id / category / Path(filename).name))

    console.print()
    console.print("[bold]Move summary[/bold]")
    for filename, destination in planned:
        console.print(f"{input_dir / filename} -> {destination}")

    if not ask_bool("Move these files?", default=False):
        console.print("[yellow]No files moved.[/yellow]")
        return

    update_status = ask_bool("Update job status now?", default=True)
    status_note = ""
    new_status: str | None = None
    new_status_date: str | None = None

    if update_status:
        new_status = ask_choice("New status", [
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
        new_status_date = ask_date("Status date", today_iso())
        status_note = f"Status changed to {new_status}"

    note = ask_text("Timeline note", status_note or f"Attached {category}")
    assets: list[UploadedAsset] = []
    for filename in selected_files:
        try:
            content = (input_dir / filename).read_bytes()
        except OSError as exc:
            raise AttachmentCopyError(filename) from exc
        assets.append(UploadedAsset(filename=filename, content=content, category=category))

    try:
        result = service.attach_files(
            job_id,
            assets,
            status=new_status,
            status_date=new_status_date,
            note=note,
            category=category,
        )
        for filename in selected_files:
            (input_dir / filename).unlink()
    except OSError as exc:
        console.print(f"[bold red]Attachment failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print()
    console.print(f"[bold green]Attached files to:[/bold green] {result.application_id}")
    console.print("[green]Updated record and report.[/green]")


@app.command("list")
def list_jobs(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    month: Optional[str] = typer.Option(None, "--month", help="Filter by month, e.g. 2026-06."),
) -> None:
    """List job applications."""
    config = load_config()
    loaded = _application_service(config).list_jobs(status=status, month=month)
    jobs = loaded.jobs

    table = Table(title="Job Applications")
    table.add_column("Date")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("ID")

    for job_id, job in jobs:
        table.add_row(format_date_display(job.date_applied), job.company, job.role, job.status, job_id)

    console.print(table)
    _print_load_summary(loaded.loaded_count, loaded.errors)
    if loaded.errors:
        raise typer.Exit(code=1)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind."),
    port: int = typer.Option(8000, "--port", help="Port to bind."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto reload."),
    diagnostic: bool = typer.Option(
        False,
        "--diagnostic",
        help="Use disposable synthetic data and enable API docs.",
    ),
) -> None:
    """Run the authenticated local web app."""
    if host != "127.0.0.1":
        raise typer.BadParameter("Only 127.0.0.1 is supported", param_hint="--host")
    if not 1 <= port <= 65535:
        raise typer.BadParameter("Port must be between 1 and 65535", param_hint="--port")
    if reload:
        raise typer.BadParameter(
            "Reload cannot preserve the per-process API token",
            param_hint="--reload",
        )

    try:
        from .desktop import _start_application, _stop_application
    except ModuleNotFoundError as exc:
        console.print("[bold red]uvicorn is not installed.[/bold red]")
        console.print("Install dependencies, then run `job web` again.")
        raise typer.Exit(code=1) from exc

    temporary: TemporaryDirectory[str] | None = TemporaryDirectory() if diagnostic else None
    data_dir = Path(temporary.name) if temporary else AppPaths.resolve().data_dir
    handle = None
    try:
        handle = _start_application(data_dir, port=port, diagnostic=diagnostic)
        path = "/docs" if diagnostic else f"/?token={handle.api_token}"
        url = f"http://127.0.0.1:{handle.port}{path}"
        if not webbrowser.open(url):
            console.print("[bold red]Could not open the local browser.[/bold red]")
            raise typer.Exit(code=1)
        console.print(f"Job Tracker opened at http://127.0.0.1:{handle.port}.")
        while handle.thread.is_alive():
            handle.thread.join(timeout=0.25)
            if handle.errors:
                raise RuntimeError("Local server stopped unexpectedly") from handle.errors[0]
    except KeyboardInterrupt:
        pass
    finally:
        _stop_application(handle)
        if temporary:
            temporary.cleanup()
