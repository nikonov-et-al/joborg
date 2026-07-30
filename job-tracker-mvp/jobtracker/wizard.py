from __future__ import annotations

from pathlib import Path
from typing import Iterable

from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from rich.console import Console

from .files import list_input_files
from .models import VALID_STATUSES, format_date_display, normalize_date_input, today_iso

console = Console()


def ask_text(label: str, default: str = "") -> str:
    if default:
        value = prompt(f"{label} [{default}]: ")
        return value.strip() or default

    return prompt(f"{label}: ").strip()


def ask_bool(label: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = prompt(f"{label} {suffix}: ").strip().lower()

    if not value:
        return default

    return value in {"y", "yes", "true", "1"}


def ask_choice(label: str, choices: Iterable[str], default: str = "") -> str:
    choices = list(choices)
    completer = WordCompleter(choices, ignore_case=True)
    shown_default = f" [{default}]" if default else ""

    while True:
        value = prompt(
            f"{label}{shown_default}: ",
            completer=completer,
            complete_while_typing=True,
        ).strip()

        if not value and default:
            value = default

        if value in choices:
            return value

        console.print(f"[red]Invalid value.[/red] Choose one of: {', '.join(choices)}")


def ask_list(label: str, default: list[str] | None = None) -> list[str]:
    default = default or []
    default_text = ", ".join(default)
    value = ask_text(label, default_text)

    if not value:
        return []

    return [item.strip() for item in value.split(",") if item.strip()]


def ask_date(label: str, default: str = "") -> str:
    shown_default = format_date_display(default)
    while True:
        value = ask_text(f"{label} (DD-MM-YYYY)", shown_default)
        try:
            return normalize_date_input(value)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")


def ask_job_description() -> str:
    console.print()
    console.print("[bold]Paste job description below.[/bold]")
    console.print("Finish by typing [bold]END[/bold] on its own line.")
    console.print()

    lines: list[str] = []

    while True:
        try:
            line = input()
        except EOFError:
            break

        if line.strip() == "END":
            break

        lines.append(line)

    return "\n".join(lines).strip()


def choose_file_from_input(input_dir: Path, label: str, default: str = "") -> str:
    files = list_input_files(input_dir)
    completer = WordCompleter(files, ignore_case=True)

    if not files:
        console.print(f"[yellow]No files found in {input_dir}/[/yellow]")
        return ""

    shown_default = f" [{default}]" if default else ""

    while True:
        value = prompt(
            f"{label}{shown_default}: ",
            completer=completer,
            complete_while_typing=True,
        ).strip()

        if not value and default:
            return default

        if not value:
            return ""

        if value in files:
            return value

        console.print(f"[red]File not found in {input_dir}/:[/red] {value}")
        console.print("Press Tab to autocomplete available files.")


def choose_many_files_from_input(input_dir: Path, label: str) -> list[str]:
    files = list_input_files(input_dir)

    if not files:
        console.print(f"[yellow]No files found in {input_dir}/[/yellow]")
        return []

    console.print()
    console.print(f"[bold]{label}[/bold]")
    console.print("Enter one filename at a time. Press Enter on an empty line to finish.")
    console.print("Use Tab to autocomplete.")
    console.print()

    selected: list[str] = []

    while True:
        filename = choose_file_from_input(input_dir, "File")
        if not filename:
            break

        if filename in selected:
            console.print(f"[yellow]Already selected:[/yellow] {filename}")
            continue

        selected.append(filename)

    return selected


def confirm_extracted_fields(data: dict) -> dict:
    console.print()
    console.print("[bold]Confirm extracted fields[/bold]")
    console.print("Press Enter to accept a suggested value.")
    console.print()

    confirmed = dict(data)

    confirmed["company"] = ask_text("Company", str(data.get("company", "")))
    confirmed["role"] = ask_text("Role", str(data.get("role", "")))
    confirmed["location"] = ask_text("Location", str(data.get("location", "")))
    confirmed["job_url"] = ask_text("Job URL", str(data.get("job_url", "")))
    confirmed["source"] = ask_text("Source", str(data.get("source", "")))
    confirmed["application_method"] = ask_text(
        "Application method",
        str(data.get("application_method", "")) or "company website",
    )
    confirmed["date_applied"] = ask_date("Date applied", str(data.get("date_applied", "")) or today_iso())
    confirmed["status"] = ask_choice("Status", VALID_STATUSES, str(data.get("status", "")) or "applied")
    confirmed["status_date"] = ask_date("Status date", str(data.get("status_date", "")) or today_iso())
    confirmed["contact_person"] = ask_text("Contact person", str(data.get("contact_person", "")))
    confirmed["contact_email"] = ask_text("Contact email", str(data.get("contact_email", "")))
    confirmed["required_skills"] = ask_list(
        "Required skills, comma-separated",
        data.get("required_skills") or [],
    )
    confirmed["nice_to_have_skills"] = ask_list(
        "Nice-to-have skills, comma-separated",
        data.get("nice_to_have_skills") or [],
    )
    confirmed["language_requirements"] = ask_list(
        "Language requirements, comma-separated",
        data.get("language_requirements") or [],
    )
    confirmed["summary"] = ask_text("Short summary", str(data.get("summary", "")))
    confirmed["next_action"] = ask_text("Next action", str(data.get("next_action", "")))
    confirmed["next_action_date"] = ask_date("Next action date", str(data.get("next_action_date", "")))
    confirmed["relevant_for_arbeitsagentur"] = ask_bool(
        "Relevant for Arbeitsagentur/Arbeitsamt report?",
        bool(data.get("relevant_for_arbeitsagentur", True)),
    )

    return confirmed


def choose_job_folder(jobs: list[tuple[str, object]]) -> str:
    """
    Let the user select a job folder from loaded jobs.
    `jobs` is a list of (application ID, JobApplication).
    """
    if not jobs:
        raise ValueError("No jobs found.")

    choices: list[str] = []
    choice_to_folder: dict[str, str] = {}

    for index, (folder, job) in enumerate(jobs, start=1):
        label = f"{index}. {format_date_display(job.date_applied)} | {job.company} | {job.role} | {job.status}"
        choices.append(label)
        choice_to_folder[label] = folder

    console.print()
    console.print("[bold]Select job[/bold]")
    for choice in choices:
        console.print(choice)

    completer = WordCompleter(choices, ignore_case=True)

    while True:
        value = prompt(
            "Job: ",
            completer=completer,
            complete_while_typing=True,
        ).strip()

        if value in choice_to_folder:
            return choice_to_folder[value]

        if value.isdigit():
            idx = int(value)
            if 1 <= idx <= len(jobs):
                return jobs[idx - 1][0]

        console.print("[red]Invalid selection.[/red] Type the number or use Tab completion.")
