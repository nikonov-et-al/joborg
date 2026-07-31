from __future__ import annotations

import json
import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlsplit

from rich.console import Console

console = Console()


class LLMError(RuntimeError):
    """Stable LLM failure suitable for UI and CLI handling."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_LOOPBACK = "loopback"
_REMOTE = "remote"


def classify_llm_endpoint(base_url: str) -> str:
    """Return ``loopback`` or ``remote`` after validating an HTTP(S) endpoint."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise LLMError("llm_endpoint_invalid", "LLM endpoint must be a valid HTTP(S) URL.")
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname
        port = parsed.port  # Validate malformed ports.
    except ValueError as exc:
        raise LLMError("llm_endpoint_invalid", "LLM endpoint must be a valid HTTP(S) URL.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not host
        or parsed.query
        or parsed.fragment
        or port is not None and not 0 < port <= 65535
    ):
        raise LLMError("llm_endpoint_invalid", "LLM endpoint must be a valid HTTP(S) URL.")
    if host.lower() == "localhost":
        return _LOOPBACK
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _is_valid_hostname(host):
            raise LLMError("llm_endpoint_invalid", "LLM endpoint must use a valid host.")
        return _REMOTE
    return _LOOPBACK if address.is_loopback else _REMOTE


def _is_valid_hostname(host: str) -> bool:
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return False
    return all(
        0 < len(label) <= 63
        and label[0] != "-"
        and label[-1] != "-"
        and all(char.isascii() and (char.isalnum() or char == "-") for char in label)
        for label in host.split(".")
    )


def llm_endpoint_policy(
    config: dict[str, Any], *, remote_confirmation: bool = False, sends_personal_content: bool = False
) -> tuple[str, str]:
    """Validate endpoint gates and return ``(base_url, api_key)`` for OpenAI."""
    llm_config = config.get("llm", {})
    base_url = llm_config.get("base_url", "http://localhost:11434/v1")
    endpoint_kind = classify_llm_endpoint(base_url)
    if endpoint_kind == _LOOPBACK:
        return base_url, os.environ.get("JOBTRACKER_LLM_API_KEY") or "local-not-needed"
    if urlsplit(base_url).scheme != "https":
        raise LLMError("llm_remote_https_required", "Remote LLM endpoints must use HTTPS.")
    if llm_config.get("remote_disclosure_consent", False) is not True:
        raise LLMError("llm_remote_consent_required", "Remote LLM disclosure consent is required.")
    if sends_personal_content and not remote_confirmation:
        raise LLMError("llm_remote_confirmation_required", "Confirm remote disclosure before sending personal content.")
    api_key = os.environ.get("JOBTRACKER_LLM_API_KEY")
    if not api_key:
        raise LLMError("llm_remote_credential_missing", "JOBTRACKER_LLM_API_KEY is required for remote LLM endpoints.")
    return base_url, api_key


def _openai_client(config: dict[str, Any], *, remote_confirmation: bool = False, sends_personal_content: bool = False) -> Any:
    base_url, api_key = llm_endpoint_policy(
        config,
        remote_confirmation=remote_confirmation,
        sends_personal_content=sends_personal_content,
    )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMError("llm_unavailable", "LLM provider is unavailable.") from exc
    try:
        return OpenAI(base_url=base_url, api_key=api_key)
    except Exception as exc:
        raise LLMError("llm_unavailable", "LLM provider is unavailable.") from exc


JOB_EXTRACTION_SYSTEM_PROMPT = """
You extract structured job application data from a job description.

Rules:
- Return only valid JSON.
- Do not use Markdown.
- Do not add explanations.
- Do not invent facts.
- If a field is missing, use an empty string or empty list.
- The JSON must use exactly the requested keys.
""".strip()

CV_DRAFT_SYSTEM_PROMPT = """
You write truthful CVs.
Return Markdown only.
Never invent facts.
Never include Certifications or Languages & Certifications sections.
Follow the user’s section order and formatting constraints exactly.
""".strip()


def build_cv_draft_prompt(*, profile: dict[str, Any], job_description: str, job: dict[str, Any]) -> str:
    return f"""
Create a truthful CV tailored to this job.

Rules:
- Return Markdown only.
- No tables, icons, or images.
- No code fences.
- Keep it factual; do not invent employers, dates, degrees, skills, or achievements.
- Use exactly these sections in this order: Summary, Experience, Education, Skills, Projects, Languages.
- Use Markdown level-2 section headings exactly: ## Summary, ## Experience, ## Education, ## Skills, ## Projects, ## Languages.
- Do not include Certifications, Languages & Certifications, or any other section.
- Format for one A4 page with body text at 11pt and margins of 8-10mm.
- Prefer scannable content that can fit on one page.
- Summary: max 350 characters.
- Experience: max 3 jobs; max 4 bullets per job; fewer bullets if needed for one page; max 120 characters per bullet.
- Bold employer names in Experience with Markdown, for example **Employer**.
- Education: max 2 entries; one compact line per entry; bold university names with Markdown.
- Skills: bullet list grouped by subtype, for example - **ML/Data:** Python, pandas.
- Include all job-required skills truthfully supported by PROFILE facts; do not invent unsupported skills.
- Projects: max 2 bullets; include only highly relevant projects.
- Languages: one compact line.

PROFILE:
{json.dumps(profile, ensure_ascii=False, indent=2)}

JOB:
{json.dumps(job, ensure_ascii=False, indent=2)}

JOB DESCRIPTION:
{job_description}
""".strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    """
    Local LLMs sometimes wrap JSON in text or Markdown.
    This function tries strict JSON first, then extracts the first JSON object.
    """
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Remove common markdown fences.
    text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Last-resort extraction: first {...} block.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("LLM response did not contain a JSON object.")

    return json.loads(match.group(0))


def default_extraction() -> dict[str, Any]:
    return {
        "company": "",
        "role": "",
        "location": "",
        "job_url": "",
        "source": "",
        "application_method": "",
        "date_applied": "",
        "status": "applied",
        "status_date": "",
        "contact_person": "",
        "contact_email": "",
        "required_skills": [],
        "nice_to_have_skills": [],
        "language_requirements": [],
        "summary": "",
        "next_action": "",
        "next_action_date": "",
        "relevant_for_arbeitsagentur": True,
    }


def normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    result = default_extraction()

    for key in result:
        if key in data and data[key] is not None:
            result[key] = data[key]

    for key in ["required_skills", "nice_to_have_skills", "language_requirements"]:
        value = result.get(key)
        if isinstance(value, str):
            result[key] = [item.strip() for item in value.split(",") if item.strip()]
        elif not isinstance(value, list):
            result[key] = []

    if not isinstance(result.get("relevant_for_arbeitsagentur"), bool):
        result["relevant_for_arbeitsagentur"] = True

    return result


def extract_job_data_with_llm(
    job_description: str,
    config: dict[str, Any],
    *,
    remote_confirmation: bool = False,
) -> dict[str, Any]:
    """
    Calls a local OpenAI-compatible LLM endpoint.

    Works with:
    - Ollama OpenAI-compatible endpoint
    - LM Studio local server
    - llama.cpp OpenAI-compatible server
    """
    client = _openai_client(
        config, remote_confirmation=remote_confirmation, sends_personal_content=True
    )
    llm_config = config["llm"]

    user_prompt = f"""
Extract the following fields from this job description.

Return JSON with exactly these keys:
{json.dumps(default_extraction(), indent=2)}

Job description:
{job_description}
""".strip()

    try:
        response = client.chat.completions.create(
            model=llm_config["model"],
            temperature=0.1,
            messages=[
                {"role": "system", "content": JOB_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError("llm_unavailable", "LLM provider is unavailable.") from exc

    content = response.choices[0].message.content or ""
    data = _extract_json_object(content)
    return normalize_extraction(data)


def test_llm(config: dict[str, Any]) -> tuple[bool, str]:
    try:
        client = _openai_client(config)

        response = client.chat.completions.create(
            model=config["llm"]["model"],
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": 'Return this JSON exactly and nothing else: {"status": "ok"}',
                }
            ],
        )

        content = response.choices[0].message.content or ""
        data = _extract_json_object(content)

        if data.get("status") == "ok":
            return True, "Local LLM responded with valid JSON."

        return False, "llm_unavailable: LLM response was invalid."

    except LLMError as exc:
        return False, f"{exc.code}: {exc}"
    except Exception:
        return False, "llm_unavailable: LLM provider is unavailable."


def generate_cv_draft_with_llm(
    *,
    profile: dict[str, Any],
    job_description: str,
    job: dict[str, Any],
    config: dict[str, Any],
    remote_confirmation: bool = False,
) -> str:
    client = _openai_client(
        config, remote_confirmation=remote_confirmation, sends_personal_content=True
    )
    llm_config = config["llm"]
    prompt = build_cv_draft_prompt(profile=profile, job_description=job_description, job=job)
    try:
        response = client.chat.completions.create(
            model=llm_config["model"],
            temperature=0.2,
            messages=[
                {"role": "system", "content": CV_DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError("llm_unavailable", "LLM provider is unavailable.") from exc
    content = (response.choices[0].message.content or "").strip()
    content = re.sub(r"^```(?:markdown|md)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)
    if not content:
        raise ValueError("LLM returned an empty CV draft")
    return content + "\n"
