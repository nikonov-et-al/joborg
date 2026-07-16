from __future__ import annotations

import json
import re
from typing import Any

from rich.console import Console

console = Console()


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


def extract_job_data_with_llm(job_description: str, config: dict[str, Any]) -> dict[str, Any]:
    """
    Calls a local OpenAI-compatible LLM endpoint.

    Works with:
    - Ollama OpenAI-compatible endpoint
    - LM Studio local server
    - llama.cpp OpenAI-compatible server
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The 'openai' Python package is not installed.") from exc

    llm_config = config["llm"]

    client = OpenAI(
        base_url=llm_config.get("base_url", "http://localhost:11434/v1"),
        api_key=llm_config.get("api_key", "local-not-needed"),
    )

    user_prompt = f"""
Extract the following fields from this job description.

Return JSON with exactly these keys:
{json.dumps(default_extraction(), indent=2)}

Job description:
{job_description}
""".strip()

    response = client.chat.completions.create(
        model=llm_config["model"],
        temperature=0.1,
        messages=[
            {"role": "system", "content": JOB_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content or ""
    data = _extract_json_object(content)
    return normalize_extraction(data)


def test_llm(config: dict[str, Any]) -> tuple[bool, str]:
    try:
        from openai import OpenAI

        client = OpenAI(
            base_url=config["llm"].get("base_url", "http://localhost:11434/v1"),
            api_key=config["llm"].get("api_key", "local-not-needed"),
        )

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

        return False, f"LLM responded, but not with expected JSON: {content}"

    except Exception as exc:
        return False, str(exc)


def generate_cv_draft_with_llm(
    *,
    profile: dict[str, Any],
    job_description: str,
    job: dict[str, Any],
    config: dict[str, Any],
) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The 'openai' Python package is not installed.") from exc

    llm_config = config["llm"]
    client = OpenAI(
        base_url=llm_config.get("base_url", "http://localhost:11434/v1"),
        api_key=llm_config.get("api_key", "local-not-needed"),
    )
    prompt = build_cv_draft_prompt(profile=profile, job_description=job_description, job=job)
    response = client.chat.completions.create(
        model=llm_config["model"],
        temperature=0.2,
        messages=[
            {"role": "system", "content": CV_DRAFT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    content = re.sub(r"^```(?:markdown|md)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)
    if not content:
        raise ValueError("LLM returned an empty CV draft")
    return content + "\n"
