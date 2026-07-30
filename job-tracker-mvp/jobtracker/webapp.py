from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.status import HTTP_201_CREATED

from .application import JobTrackerService, RecordNotFoundError
from .llm import generate_cv_draft_with_llm
from .pdf import render_cv_pdf
from .reports import build_report_data, report_data_to_dict, render_report_pdf, write_report_data
from .repositories import (
    LegacyDocumentGateway,
    LegacyReportGateway,
    build_application_service,
)
from .schemas import CvDraftSchema, JobCreateSchema, JobUpdateSchema, ProfileUpdateSchema
from .services import UploadedAsset, job_record_to_dict, load_project_context, write_project_report


STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
router = APIRouter()


def _build_application_service() -> JobTrackerService:
    context = load_project_context()
    return build_application_service(context)


def get_application_service(request: Request) -> JobTrackerService:
    return request.app.state.jobtracker_service or _build_application_service()


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


@router.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/api/config")
def api_config(
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    context = service.context
    applicant = dict(context.config.get("applicant", {}))
    applicant["name"] = context.applicant_name
    return {
        "applicant": applicant,
        "paths": dict(context.config.get("paths", {})),
        "llm": dict(context.config.get("llm", {})),
        "export": dict(context.config.get("export", {})),
    }


@router.get("/api/jobs")
def api_list_jobs(
    response: Response,
    status: str | None = None,
    month: str | None = None,
    include_errors: bool = False,
    service: JobTrackerService = Depends(get_application_service),
) -> list[dict[str, Any]] | dict[str, Any]:
    loaded = service.list_jobs(status=status, month=month)
    serialized_jobs = [job_record_to_dict(job_id, job) for job_id, job in loaded.jobs]
    response.headers["X-Jobtracker-Loaded-Count"] = str(len(serialized_jobs))
    response.headers["X-Jobtracker-Failed-Count"] = str(loaded.failed_count)
    if loaded.errors:
        response.headers["Warning"] = '199 jobtracker "Some job records failed to load; run job doctor"'
    if not include_errors:
        return serialized_jobs
    return {
        "jobs": serialized_jobs,
        "loaded_count": len(serialized_jobs),
        "failed_count": loaded.failed_count,
        "errors": [asdict(error) for error in loaded.errors],
    }


@router.get("/api/jobs/{job_id}")
def api_get_job(
    job_id: str,
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    try:
        details = service.get_job(job_id)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    job = details.job
    result = job_record_to_dict(details.application_id, job)
    result["body"] = details.body
    result["job_description"] = details.job_description
    result["files"] = {
        category: [asdict(document) for document in details.documents if document.category == category]
        for category in ("cv", "motivation_letter", "attachments", "evidence", "screenshots")
    }
    result["cv_draft"] = details.cv_draft
    return result


@router.patch("/api/jobs/{job_id}")
def api_update_job(
    job_id: str,
    payload: JobUpdateSchema,
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    changes = payload.changes()
    note = changes.pop("note", "")
    job_description = changes.pop("job_description", None)
    try:
        result = service.update_job(
            job_id,
            updates=changes,
            note=note,
            job_description=job_description,
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return job_record_to_dict(result.application_id, result.job)


@router.post("/api/jobs", status_code=HTTP_201_CREATED)
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
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
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
    try:
        job = JobCreateSchema.model_validate(
            _build_job_payload(flat_payload, parsed_payload)
        ).to_job()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    categories = _normalize_upload_categories(files, file_categories)
    assets = await _collect_assets(files, categories)
    try:
        result = service.create_job(job, job_description, assets)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = job_record_to_dict(result.application_id, result.job)
    response["documents"] = [asdict(document) for document in result.documents]
    response["report_warning"] = result.report_warning
    return response


async def _attach_impl(
    job_id: str,
    payload: str,
    status: str | None,
    status_date: str,
    note: str,
    files: list[UploadFile] | None,
    file_categories: str,
    service: JobTrackerService,
) -> dict[str, Any]:
    parsed_payload = _parse_json(payload, default=[])

    if isinstance(parsed_payload, dict):
        status = parsed_payload.get("status", status)
        status_date = parsed_payload.get("status_date", status_date)
        note = str(parsed_payload.get("note", note))

    categories = _normalize_upload_categories(files, file_categories, parsed_payload)
    assets = await _collect_assets(files, categories)

    try:
        result = service.attach_files(
            job_id,
            assets,
            status=status,
            status_date=status_date or None,
            note=note,
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = job_record_to_dict(result.application_id, result.job)
    response["documents"] = [asdict(document) for document in result.documents]
    response["report_warning"] = result.report_warning
    return response


@router.post("/api/jobs/{job_id}/files")
async def api_attach_job(
    job_id: str,
    payload: str = Form(""),
    status: str | None = Form(None),
    status_date: str = Form(""),
    note: str = Form(""),
    files: list[UploadFile] | None = File(None),
    file_categories: str = Form(""),
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    return await _attach_impl(
        job_id, payload, status, status_date, note, files, file_categories, service
    )


@router.post("/api/jobs/{job_id}/attachments")
async def api_attach_job_compat(
    job_id: str,
    payload: str = Form(""),
    status: str | None = Form(None),
    status_date: str = Form(""),
    note: str = Form(""),
    files: list[UploadFile] | None = File(None),
    file_categories: str = Form(""),
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    return await _attach_impl(
        job_id, payload, status, status_date, note, files, file_categories, service
    )


@router.post("/api/report")
def api_report(
    month: str | None = None,
    year: str | None = None,
    month_number: str | None = None,
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    try:
        artifact = service.create_report(
            month=month,
            year=year,
            month_number=month_number,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "artifact_id": artifact.artifact_id,
        "content": artifact.content,
        "data": report_data_to_dict(artifact.data),
    }


@router.get("/api/report/pdf")
def api_report_pdf(
    month: str | None = None,
    year: str | None = None,
    month_number: str | None = None,
    service: JobTrackerService = Depends(get_application_service),
) -> Response:
    try:
        pdf, filename = service.render_report_pdf(
            month=month,
            year=year,
            month_number=month_number,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/profile")
def api_get_profile(
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    return service.get_profile()


@router.put("/api/profile")
def api_put_profile(
    payload: ProfileUpdateSchema,
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    try:
        return service.save_profile(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/profile/cvs", status_code=HTTP_201_CREATED)
async def api_upload_profile_cvs(
    files: list[UploadFile] | None = File(None),
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    assets: list[UploadedAsset] = []
    for upload in files or []:
        if not upload.filename:
            continue
        assets.append(UploadedAsset(filename=Path(upload.filename).name, content=await upload.read()))
        await upload.close()
    try:
        uploaded, profile = service.upload_profile_cvs(assets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"uploaded": uploaded, "profile": profile}


@router.post("/api/jobs/{job_id}/cv-draft")
def api_generate_cv_draft(
    job_id: str,
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    try:
        content = service.generate_cv_draft(job_id)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CV generation failed: {exc}") from exc
    return {"job_id": job_id, "content": content}


@router.post("/api/jobs/{job_id}/cv-pdf")
def api_generate_cv_pdf(
    job_id: str,
    payload: CvDraftSchema | None = Body(default=None),
    service: JobTrackerService = Depends(get_application_service),
) -> Response:
    try:
        pdf = service.render_cv_pdf(job_id, payload.content if payload else None)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="generated_cv.pdf"'},
    )


@router.put("/api/jobs/{job_id}/cv-draft")
def api_save_cv_draft(
    job_id: str,
    payload: CvDraftSchema,
    service: JobTrackerService = Depends(get_application_service),
) -> dict[str, Any]:
    try:
        document_version_id = service.save_cv_draft(job_id, payload.content)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return {"job_id": job_id, "content": payload.content, "document_version_id": document_version_id}


def create_app(service: JobTrackerService | None = None) -> FastAPI:
    api = FastAPI(title="jobtracker web backend")
    api.state.jobtracker_service = service
    api.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
    api.router.routes.extend(router.routes)

    @api.middleware("http")
    async def disable_ui_cache(request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    return api


app = create_app()
