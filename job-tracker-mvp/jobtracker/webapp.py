from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.status import HTTP_201_CREATED

from .markdown import read_markdown_with_frontmatter
from .models import JobApplication
from .llm import generate_cv_draft_with_llm
from .services import (
    UploadedAsset,
    apply_job_update,
    create_job_record,
    job_record_to_dict,
    list_job_records,
    load_profile,
    load_project_context,
    save_profile,
    store_profile_cv,
    write_project_report,
)


STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"

app = FastAPI(title="jobtracker web backend")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON payload") from exc


def _normalize_upload_categories(
    files: list[UploadFile] | None,
    raw_categories: str | None,
    payload: Any = None,
) -> list[str] | None:
    uploads = list(files or [])
    if not uploads:
        return []

    if raw_categories:
        parsed = _parse_json(raw_categories, default=None)
    else:
        parsed = payload

    if parsed is None:
        return ["attachments"] * len(uploads)

    if isinstance(parsed, str):
        return [parsed] * len(uploads)

    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        if len(parsed) == 1 and len(uploads) > 1:
            return parsed * len(uploads)
        if len(parsed) != len(uploads):
            raise HTTPException(status_code=422, detail="file category count mismatch")
        return parsed

    if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        mapping = {
            Path(str(item.get("name", ""))).name: str(item.get("category", "attachments"))
            for item in parsed
        }
        return [mapping.get(Path(upload.filename or "").name, "attachments") for upload in uploads]

    raise HTTPException(status_code=422, detail="Invalid file category payload")


async def _collect_assets(files: list[UploadFile] | None, categories: list[str] | None) -> list[UploadedAsset]:
    uploads = list(files or [])
    normalized = list(categories or [])

    if len(normalized) != len(uploads):
        raise HTTPException(status_code=422, detail="file category count mismatch")

    assets: list[UploadedAsset] = []
    for upload, category in zip(uploads, normalized):
        if not upload.filename:
            continue
        assets.append(
            UploadedAsset(
                filename=Path(upload.filename).name,
                content=await upload.read(),
                category=category,
            )
        )
        await upload.close()

    return assets


def _build_job_payload(flat_data: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(flat_data)
    merged.update(payload)
    merged["required_skills"] = payload.get("required_skills", flat_data["required_skills"])
    merged["nice_to_have_skills"] = payload.get("nice_to_have_skills", flat_data["nice_to_have_skills"])
    merged["language_requirements"] = payload.get("language_requirements", flat_data["language_requirements"])
    return merged


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    context = load_project_context()
    return {
        "applicant": context.config.get("applicant", {}),
        "paths": dict(context.config.get("paths", {})),
        "llm": dict(context.config.get("llm", {})),
        "export": dict(context.config.get("export", {})),
    }


@app.get("/api/jobs")
def api_list_jobs(
    status: str | None = None,
    month: str | None = None,
) -> list[dict[str, Any]]:
    context = load_project_context()
    jobs = list_job_records(context.jobs_dir)

    if status:
        jobs = [(folder, job) for folder, job in jobs if job.status == status]

    if month:
        jobs = [(folder, job) for folder, job in jobs if job.date_applied.startswith(month)]

    return [job_record_to_dict(folder, job) for folder, job in jobs]


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    context = load_project_context()
    job_dir = context.jobs_dir / job_id
    job_md = job_dir / "job.md"

    if not job_md.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    metadata, body = read_markdown_with_frontmatter(job_md)
    job = JobApplication.from_metadata(metadata)
    result = job_record_to_dict(job_dir, job)
    result["body"] = body
    description_path = job_dir / "job_description.md"
    result["job_description"] = (
        description_path.read_text(encoding="utf-8") if description_path.exists() else ""
    )
    result["files"] = {
        "cv": [job.cv_file] if job.cv_file else [],
        "motivation_letter": [job.motivation_letter_file] if job.motivation_letter_file else [],
        "attachments": job.attachments,
        "evidence": job.evidence,
        "screenshots": job.screenshots,
    }
    draft_path = job_dir / "cv" / "generated_cv.md"
    result["cv_draft"] = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
    return result


@app.patch("/api/jobs/{job_id}")
def api_update_job(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    context = load_project_context()
    job_dir = context.jobs_dir / job_id
    job_md = job_dir / "job.md"

    if not job_md.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    metadata, _body = read_markdown_with_frontmatter(job_md)
    job = JobApplication.from_metadata(metadata)

    try:
        job, _stored = apply_job_update(
            job_dir,
            job,
            status=payload.get("status"),
            status_date=payload.get("status_date"),
            note=str(payload.get("note", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    write_project_report(context)
    return job_record_to_dict(job_dir, job)


@app.post("/api/jobs", status_code=HTTP_201_CREATED)
async def api_create_job(
    payload: str = Form(""),
    company: str = Form(""),
    role: str = Form(""),
    location: str = Form(""),
    job_url: str = Form(""),
    source: str = Form(""),
    application_method: str = Form("company website"),
    date_applied: str = Form(""),
    status: str = Form("applied"),
    status_date: str = Form(""),
    contact_person: str = Form(""),
    contact_email: str = Form(""),
    required_skills: str = Form(""),
    nice_to_have_skills: str = Form(""),
    language_requirements: str = Form(""),
    summary: str = Form(""),
    next_action: str = Form(""),
    next_action_date: str = Form(""),
    relevant_for_arbeitsagentur: bool = Form(True),
    job_description: str = Form(""),
    files: list[UploadFile] | None = File(None),
    file_categories: str = Form(""),
) -> dict[str, Any]:
    context = load_project_context()
    parsed_payload = _parse_json(payload, default={})
    if not isinstance(parsed_payload, dict):
        raise HTTPException(status_code=422, detail="Invalid job payload")

    flat_payload = {
        "company": company,
        "role": role,
        "location": location,
        "job_url": job_url,
        "source": source,
        "application_method": application_method,
        "date_applied": date_applied,
        "status": status,
        "status_date": status_date,
        "contact_person": contact_person,
        "contact_email": contact_email,
        "required_skills": _split_csv(required_skills),
        "nice_to_have_skills": _split_csv(nice_to_have_skills),
        "language_requirements": _split_csv(language_requirements),
        "summary": summary,
        "next_action": next_action,
        "next_action_date": next_action_date,
        "relevant_for_arbeitsagentur": relevant_for_arbeitsagentur,
    }
    job = JobApplication.from_metadata(_build_job_payload(flat_payload, parsed_payload))

    categories = _normalize_upload_categories(files, file_categories)
    assets = await _collect_assets(files, categories)
    job_dir = context.jobs_dir / job.folder_name()

    try:
        job, stored_paths = create_job_record(job_dir, job, job_description, assets)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    report_path = write_project_report(context)
    result = job_record_to_dict(job_dir, job)
    result["stored_files"] = stored_paths
    result["report_path"] = report_path.as_posix()
    return result


async def _attach_impl(
    job_id: str,
    payload: str,
    status: str | None,
    status_date: str,
    note: str,
    files: list[UploadFile] | None,
    file_categories: str,
) -> dict[str, Any]:
    context = load_project_context()
    job_dir = context.jobs_dir / job_id
    job_md = job_dir / "job.md"

    if not job_md.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    metadata, _body = read_markdown_with_frontmatter(job_md)
    job = JobApplication.from_metadata(metadata)
    parsed_payload = _parse_json(payload, default=[])

    if isinstance(parsed_payload, dict):
        status = parsed_payload.get("status", status)
        status_date = parsed_payload.get("status_date", status_date)
        note = str(parsed_payload.get("note", note))

    categories = _normalize_upload_categories(files, file_categories, parsed_payload)
    assets = await _collect_assets(files, categories)

    try:
        job, stored_paths = apply_job_update(
            job_dir,
            job,
            assets,
            status=status,
            status_date=status_date or None,
            note=note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    report_path = write_project_report(context)
    result = job_record_to_dict(job_dir, job)
    result["stored_files"] = stored_paths
    result["report_path"] = report_path.as_posix()
    return result


@app.post("/api/jobs/{job_id}/files")
async def api_attach_job(
    job_id: str,
    payload: str = Form(""),
    status: str | None = Form(None),
    status_date: str = Form(""),
    note: str = Form(""),
    files: list[UploadFile] | None = File(None),
    file_categories: str = Form(""),
) -> dict[str, Any]:
    return await _attach_impl(job_id, payload, status, status_date, note, files, file_categories)


@app.post("/api/jobs/{job_id}/attachments")
async def api_attach_job_compat(
    job_id: str,
    payload: str = Form(""),
    status: str | None = Form(None),
    status_date: str = Form(""),
    note: str = Form(""),
    files: list[UploadFile] | None = File(None),
    file_categories: str = Form(""),
) -> dict[str, Any]:
    return await _attach_impl(job_id, payload, status, status_date, note, files, file_categories)


@app.post("/api/report")
def api_report(month: str | None = None) -> dict[str, Any]:
    context = load_project_context()
    report_path = write_project_report(context, month=month)
    return {
        "report_path": report_path.as_posix(),
        "content": report_path.read_text(encoding="utf-8"),
    }


@app.get("/api/profile")
def api_get_profile() -> dict[str, Any]:
    context = load_project_context()
    return load_profile(context.profile_dir)


@app.put("/api/profile")
def api_put_profile(payload: dict[str, Any]) -> dict[str, Any]:
    context = load_project_context()
    try:
        return save_profile(context.profile_dir, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/profile/cvs", status_code=HTTP_201_CREATED)
async def api_upload_profile_cvs(files: list[UploadFile] | None = File(None)) -> dict[str, Any]:
    context = load_project_context()
    uploaded: list[str] = []
    for upload in files or []:
        if not upload.filename:
            continue
        asset = UploadedAsset(filename=Path(upload.filename).name, content=await upload.read())
        await upload.close()
        try:
            uploaded.append(store_profile_cv(context.profile_dir, asset))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"uploaded": uploaded, "profile": load_profile(context.profile_dir)}


@app.post("/api/jobs/{job_id}/cv-draft")
def api_generate_cv_draft(job_id: str) -> dict[str, Any]:
    context = load_project_context()
    job_dir = context.jobs_dir / job_id
    job_md = job_dir / "job.md"
    description_path = job_dir / "job_description.md"
    if not job_md.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    metadata, _body = read_markdown_with_frontmatter(job_md)
    profile = load_profile(context.profile_dir)
    try:
        content = generate_cv_draft_with_llm(
            profile=profile,
            job_description=description_path.read_text(encoding="utf-8") if description_path.exists() else "",
            job=metadata,
            config=context.config,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CV generation failed: {exc}") from exc

    draft_path = job_dir / "cv" / "generated_cv.md"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(content, encoding="utf-8")
    return {"job_id": job_id, "content": content, "path": draft_path.relative_to(job_dir).as_posix()}


@app.put("/api/jobs/{job_id}/cv-draft")
def api_save_cv_draft(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    context = load_project_context()
    job_dir = context.jobs_dir / job_id
    if not (job_dir / "job.md").exists():
        raise HTTPException(status_code=404, detail="Job not found")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be text")

    draft_path = job_dir / "cv" / "generated_cv.md"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return {"job_id": job_id, "content": content, "path": draft_path.relative_to(job_dir).as_posix()}
