# Job Tracker MVP

Local job application tracker for keeping applications, CVs, motivation letters, screenshots, and evidence organized.

## What it does

- Creates one folder per job application
- Saves job metadata in `job.md`
- Saves full job description in `job_description.md`
- Creates job records first
- Moves CV / motivation letter / job description files from `input/` during job creation
- Adds employer responses, screenshots, confirmations, rejections, and interview invites later with `job attach`
- Supports tab completion for selecting files from `input/`
- Optionally uses a local LLM through an OpenAI-compatible endpoint
- Generates `report.md`
- Exports report to DOCX/PDF through Pandoc if installed

## Install

From inside this folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Initialize folders

```bash
job init
```

## Daily workflow

1. Put files into `input/`:
   - CV PDF/DOCX
   - motivation letter
   - screenshots
   - confirmation emails
   - job description PDF if available

2. Add a job application record:

```bash
job add
```

This creates the job folder and stores the CV / motivation letter if you already have them.

3. Later, when the employer replies, put the response files into `input/` and attach them:

```bash
job attach
```

Examples of later evidence:

- confirmation email PDF
- rejection email PDF
- interview invitation
- employer task
- portal screenshots
- email screenshots

4. Generate report:

```bash
job report
```

5. Export report:

```bash
job export
```

## Web app

```bash
job web
```

Open `http://127.0.0.1:8000`. The web app includes:

- dashboard statistics and pending interviews
- separate add-job, all-jobs, report, and profile views
- right-side job details with status and file updates
- profile text and multiple reference CV uploads
- local-LLM CV drafts saved inside each job folder
- browser Print / Save as PDF for reports and generated CVs

## Local LLM

Default config uses Ollama:

```yaml
llm:
  base_url: "http://localhost:11434/v1"
  model: "gemma4:12b-it-q4_K_M"
```

Test it:

```bash
job ai-test
```

If you use LM Studio, change `config.yml`:

```yaml
llm:
  base_url: "http://localhost:1234/v1"
  model: "your-loaded-model"
```

## Commands

```bash
job init
job add
job add --no-ai
job list
job report
job export
job attach
job ai-test
```

## Notes

The local LLM is only used to suggest fields from the job description. You confirm/edit everything before it is saved.
## Correct evidence workflow

`job add` is for creating the application record and storing files you already have at the time of applying:

- CV sent
- motivation letter sent
- pasted job description
- optional job description PDF
- optional portfolio/application attachment

`job attach` is for later employer responses:

- confirmation email
- rejection email
- interview invitation
- task assignment
- portal status screenshot
- email screenshots

This is intentional because evidence usually arrives after the application is created.
