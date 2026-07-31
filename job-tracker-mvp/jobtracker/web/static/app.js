import { createApiClient, tokenFromLocation } from "./api-client.js";

const { request, download } = createApiClient({ token: tokenFromLocation() });
const STATUSES = [
  "draft", "applied", "confirmation_received", "interview_invited",
  "interview_done", "task_received", "offer_received", "rejected",
  "withdrawn", "no_response", "closed",
];
const CATEGORIES = ["attachments", "cv", "motivation_letter", "job_description", "evidence", "screenshots"];
const CLOSED = new Set(["rejected", "withdrawn", "no_response", "closed"]);
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const state = {
  jobs: [], loadErrors: [], detail: null, newFiles: [], profile: null, report: null,
  workspaces: null, storage: null, workspaceFolder: "", notice: "", llm: null,
};
const app = document.querySelector("#app");
const apiStatus = document.querySelector("#apiStatus");
const today = new Date().toISOString().slice(0, 10);

const esc = (value = "") => String(value)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const label = (value = "") => String(value).replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
const list = value => Array.isArray(value) ? value : [];
const csvList = value => String(value || "").split(",").map(item => item.trim()).filter(Boolean);
const ageDays = value => Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86400000));
const formatBytes = value => {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "unknown";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = -1;
  do { size /= 1024; unit += 1; } while (size >= 1024 && unit < units.length - 1);
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
};

function displayDate(value = "") {
  const text = String(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})(.*)$/.exec(text);
  if (!match) return text;
  const [, year, month, day, suffix] = match;
  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (parsed.getUTCFullYear() !== Number(year) || parsed.getUTCMonth() + 1 !== Number(month) || parsed.getUTCDate() !== Number(day)) return text;
  return `${day}-${month}-${year}${suffix}`;
}

function setStatus(text, tone = "") {
  apiStatus.textContent = text;
  apiStatus.dataset.tone = tone;
}

function routeInfo() {
  const raw = location.hash.slice(1) || "/home";
  const [path, query = ""] = raw.split("?");
  return { route: path.replace(/^\/+/, "") || "home", query: new URLSearchParams(query) };
}

function pageHead(title, text, action = "") {
  return `<header class="page-head"><div><p class="eyebrow">Job application workspace</p><h1>${esc(title)}</h1><p>${esc(text)}</p></div>${action}</header>`;
}

function stats() {
  const count = status => state.jobs.filter(job => job.status === status).length;
  const total = state.jobs.length;
  const rejected = count("rejected");
  return {
    total, rejected,
    invited: count("interview_invited"),
    completed: count("interview_done"),
    offers: count("offer_received"),
    rejectionRate: total ? Math.round(rejected / total * 100) : 0,
  };
}

function statusOptions(selected = "", all = false) {
  return `${all ? '<option value="">All statuses</option>' : ""}${STATUSES.map(
    status => `<option value="${status}" ${status === selected ? "selected" : ""}>${label(status)}</option>`
  ).join("")}`;
}

function fileRows(files) {
  const rows = Object.entries(files || {}).flatMap(([category, names]) =>
    list(names).filter(Boolean).map(document => `<li><span class="pill">${label(category)}</span>${esc(document.original_filename)}</li>`)
  );
  return rows.length ? `<ul class="file-links">${rows.join("")}</ul>` : `<p class="muted">No files.</p>`;
}

async function loadJobs() {
  const result = await request("/jobs?include_errors=true");
  state.jobs = result.jobs;
  state.loadErrors = result.errors;
}

function loadErrorsHtml() {
  if (!state.loadErrors.length) return "";
  return `<section class="card error"><strong>${state.loadErrors.length} job record(s) failed to load.</strong><ul>${state.loadErrors.map(error => `<li><code>${esc(error.path)}</code> [${esc(error.code)}]: ${esc(error.message)}</li>`).join("")}</ul><p>Run <code>job doctor</code> for details.</p></section>`;
}

function renderDashboard() {
  const s = stats();
  const interviews = state.jobs.filter(job => job.status === "interview_invited");
  app.innerHTML = `
    ${pageHead("Dashboard", "Application status at a glance.", '<a class="button" href="#/new-job">Add job</a>')}
    ${loadErrorsHtml()}
    <section class="stats-grid">
      ${[
        ["Applications", s.total], ["Rejected", s.rejected], ["Interview invites", s.invited],
        ["Interviews done", s.completed], ["Offers", s.offers], ["Rejection rate", `${s.rejectionRate}%`],
      ].map(([name, value]) => `<article class="stat"><span>${esc(name)}</span><strong>${esc(value)}</strong></article>`).join("")}
    </section>
    <section class="card">
      <div class="section-head"><div><h2>Pending interviews</h2><p>Jobs waiting for interview.</p></div></div>
      <div class="compact-list">
        ${interviews.length ? interviews.map(job => `
          <a class="compact-row" href="#/jobs?job=${encodeURIComponent(job.id)}">
            <div><strong>${esc(job.company)}</strong><span>${esc(job.role)} · ${esc(job.location)}</span></div>
            <span class="pill">${label(job.status)}</span>
          </a>`).join("") : '<div class="empty">No pending interviews.</div>'}
      </div>
    </section>`;
}

function renderNewJob() {
  state.newFiles = [];
  app.innerHTML = `
    ${pageHead("Add New Job", "Store job details and application files.")}
    <form id="jobForm" class="card form">
      <div class="form-grid">
        <label>Company<input name="company" required></label>
        <label>Role<input name="role" required></label>
        <label>Location<input name="location"></label>
        <label>Date applied<input name="date_applied" type="date" value="${today}"></label>
        <label>Source<input name="source"></label>
        <label>Method<input name="application_method" value="company website"></label>
        <label class="span-2">Job URL<input name="job_url" type="url"></label>
        <label>Status<select name="status">${statusOptions("applied")}</select></label>
        <label>Next action<input name="next_action"></label>
        <label>Next action date<input name="next_action_date" type="date"></label>
      </div>
      <label>Job description<textarea name="job_description" rows="14" placeholder="Paste full job description"></textarea></label>
      <div class="upload-box">
        <label>Application files<input id="newFiles" type="file" multiple></label>
        <p>Choose category per file after selection.</p>
      </div>
      <div id="newFileQueue"></div>
      <label class="check"><input name="relevant_for_arbeitsagentur" type="checkbox" checked>Relevant for Arbeitsagentur</label>
      <div class="actions">
        <button type="submit" data-create>Create job</button>
        <button type="submit" class="secondary" data-create-cv>Create + generate CV</button>
      </div>
    </form>`;
}

function renderNewFileQueue() {
  const target = document.querySelector("#newFileQueue");
  if (!target) return;
  target.innerHTML = state.newFiles.length ? state.newFiles.map((item, index) => `
    <div class="queued-file"><span>${esc(item.file.name)}</span>
      <select data-new-file="${index}">${CATEGORIES.map(category =>
        `<option value="${category}" ${category === item.category ? "selected" : ""}>${label(category)}</option>`
      ).join("")}</select>
      <button type="button" class="text-button" data-remove-new="${index}">Remove</button>
    </div>`).join("") : "";
}

function jobListRows(jobs) {
  return jobs.map(job => `
    <button class="job-row ${state.detail?.id === job.id ? "active" : ""}" data-job="${esc(job.id)}">
      <span><strong>${esc(job.company || "Untitled")}</strong><small>${esc(job.role)} · ${esc(job.location)}</small></span>
      <span>${esc(displayDate(job.date_applied))}</span><span class="pill">${label(job.status)}</span>
    </button>`).join("");
}

function renderJobsPage() {
  const selected = routeInfo().query.get("job");
  app.innerHTML = `
    ${pageHead("All Jobs", "Search applications and inspect complete records.", '<a class="button" href="#/new-job">Add job</a>')}
    ${loadErrorsHtml()}
    <div class="jobs-layout">
      <section class="card jobs-column">
        <div class="filters"><input id="jobSearch" type="search" placeholder="Search jobs"><select id="jobStatus">${statusOptions("", true)}</select></div>
        <div id="jobRows" class="job-rows">${jobListRows(state.jobs)}</div>
      </section>
      <aside id="jobDetail" class="card detail-panel"><div class="empty">Select a job.</div></aside>
    </div>`;
  if (selected) selectJob(selected);
}

async function selectJob(id) {
  setStatus("Loading job…", "busy");
  try {
    const [detail, settings] = await Promise.all([
      request(`/jobs/${encodeURIComponent(id)}`),
      state.llm ? Promise.resolve(null) : request("/config"),
    ]);
    state.detail = detail;
    if (settings) state.llm = settings.llm;
    const target = document.querySelector("#jobDetail");
    if (!target) return;
    target.innerHTML = detailHtml(state.detail);
    document.querySelectorAll("[data-job]").forEach(row => row.classList.toggle("active", row.dataset.job === id));
    setStatus("Ready", "ok");
  } catch (error) {
    setStatus(error.message, "bad");
  }
}

function detailHtml(job) {
  const metadata = [
    ["Applied", displayDate(job.date_applied)], ["Status date", displayDate(job.status_date)], ["Location", job.location],
    ["Source", job.source], ["Method", job.application_method], ["Contact", job.contact_person],
    ["Email", job.contact_email], ["Next action", job.next_action], ["Next date", displayDate(job.next_action_date)],
  ].filter(([, value]) => value);
  const remote = state.llm?.endpoint_scope === "remote";
  const llmBlocked = state.llm?.endpoint_scope === "invalid" || (remote && !state.llm?.remote_consent_configured);
  const disclosure = remote
    ? `<p class="error">Remote AI: profile, job, and document content will leave this device via ${esc(state.llm.provider)} / ${esc(state.llm.model)}.${state.llm.remote_consent_configured ? " Confirmation is required for each request." : " Enable remote_disclosure_consent in config.yml first."}</p>`
    : state.llm?.endpoint_scope === "invalid" ? `<p class="error">AI endpoint configuration is invalid.</p>` : "";
  return `
    <div class="detail-title"><div><p class="eyebrow">Selected job</p><h2>${esc(job.company)}</h2><p>${esc(job.role)}</p></div><div class="detail-actions"><span class="pill">${label(job.status)}</span><button class="secondary" data-edit-job>Edit Job</button></div></div>
    <div class="meta-grid">${metadata.map(([key, value]) => `<div><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>
    ${job.job_url ? `<p><a href="${esc(job.job_url)}" target="_blank" rel="noreferrer">Open job posting</a></p>` : ""}
    <div class="inline-form"><select id="detailStatus">${statusOptions(job.status)}</select><button data-save-status>Save status</button></div>
    <details open><summary>Job description</summary><pre class="document">${esc(job.job_description)}</pre></details>
    <details><summary>Record notes / timeline</summary><pre class="document">${esc(job.body)}</pre></details>
    <details><summary>Skills</summary>
      <p><strong>Required:</strong> ${esc(list(job.required_skills).join(", ") || "—")}</p>
      <p><strong>Nice to have:</strong> ${esc(list(job.nice_to_have_skills).join(", ") || "—")}</p>
      <p><strong>Languages:</strong> ${esc(list(job.language_requirements).join(", ") || "—")}</p>
    </details>
    <details><summary>Files</summary>${fileRows(job.files)}
      <div class="attach-row"><input id="attachFiles" type="file" multiple><select id="attachCategory">${CATEGORIES.map(c => `<option value="${c}">${label(c)}</option>`).join("")}</select><button data-attach>Attach</button></div>
    </details>
    <details open><summary>Generated CV</summary>
      ${disclosure}
      <div class="actions"><button data-generate-cv ${llmBlocked ? "disabled" : ""}>Generate CV</button><button class="secondary" data-save-cv>Save draft</button><button class="secondary" data-generate-cv-pdf>Generate PDF</button></div>
      <textarea id="cvDraft" class="cv-editor" rows="22" placeholder="Generated CV appears here.">${esc(job.cv_draft)}</textarea>
    </details>`;
}

function editJobHtml(job) {
  return `<form id="editJobForm" class="form">
    <div class="detail-title"><div><p class="eyebrow">Edit job</p><h2>${esc(job.company)}</h2><p>Job ID remains unchanged.</p></div></div>
    <div class="form-grid edit-job-grid">
      <label>Company<input name="company" value="${esc(job.company)}" required></label>
      <label>Role<input name="role" value="${esc(job.role)}" required></label>
      <label>Location<input name="location" value="${esc(job.location)}"></label>
      <label>Date applied<input name="date_applied" type="date" value="${esc(job.date_applied)}"></label>
      <label>Status<select name="status">${statusOptions(job.status)}</select></label>
      <label>Status date<input name="status_date" type="date" value="${esc(job.status_date)}"></label>
      <label>Source<input name="source" value="${esc(job.source)}"></label>
      <label>Method<input name="application_method" value="${esc(job.application_method)}"></label>
      <label class="span-2">Job URL<input name="job_url" type="url" value="${esc(job.job_url)}"></label>
      <label>Contact person<input name="contact_person" value="${esc(job.contact_person)}"></label>
      <label>Contact email<input name="contact_email" type="email" value="${esc(job.contact_email)}"></label>
      <label>Next action<input name="next_action" value="${esc(job.next_action)}"></label>
      <label>Next action date<input name="next_action_date" type="date" value="${esc(job.next_action_date)}"></label>
      <label class="span-2">Required skills<input name="required_skills" value="${esc(list(job.required_skills).join(", "))}" placeholder="Python, SQL"></label>
      <label class="span-2">Nice-to-have skills<input name="nice_to_have_skills" value="${esc(list(job.nice_to_have_skills).join(", "))}"></label>
      <label class="span-2">Languages<input name="language_requirements" value="${esc(list(job.language_requirements).join(", "))}" placeholder="German, English"></label>
    </div>
    <label>Summary<textarea name="summary" rows="4">${esc(job.summary)}</textarea></label>
    <label>Job description<textarea name="job_description" rows="14">${esc(job.job_description)}</textarea></label>
    <label class="check"><input name="relevant_for_arbeitsagentur" type="checkbox" ${job.relevant_for_arbeitsagentur ? "checked" : ""}>Relevant for Arbeitsagentur</label>
    <div class="actions"><button type="submit">Save</button><button type="button" class="secondary" data-cancel-edit>Cancel</button></div>
  </form>`;
}

function filterJobs() {
  const search = document.querySelector("#jobSearch")?.value.toLowerCase() || "";
  const status = document.querySelector("#jobStatus")?.value || "";
  const jobs = state.jobs.filter(job =>
    (!status || job.status === status) &&
    (!search || [job.company, job.role, job.location, job.status].join(" ").toLowerCase().includes(search))
  );
  document.querySelector("#jobRows").innerHTML = jobListRows(jobs);
}

function renderReport() {
  const years = [...new Set(state.jobs.map(job => /^(\d{4})-/.exec(job.date_applied || "")?.[1]).filter(Boolean))]
    .sort((a, b) => Number(b) - Number(a));
  app.innerHTML = `
    ${pageHead("PDF Report", "Generate report and download server PDF.")}
    <section class="card">
      <div class="report-controls">
        <label>Month<select id="reportMonth"><option value="">All</option>${MONTHS.map((month, index) => `<option value="${String(index + 1).padStart(2, "0")}">${month}</option>`).join("")}</select></label>
        <label>Year<select id="reportYear"><option value="">All</option>${years.map(year => `<option value="${year}">${year}</option>`).join("")}</select></label>
        <button data-report-generate>Generate report</button><button class="secondary" data-download-report>Download PDF</button>
      </div>
      <div id="reportArea" class="empty">Generate report to preview it.</div>
    </section>`;
}

function reportQuery() {
  const query = new URLSearchParams();
  const year = document.querySelector("#reportYear")?.value;
  const month = document.querySelector("#reportMonth")?.value;
  if (year) query.set("year", year);
  if (month) query.set("month_number", month);
  return query.toString();
}

function reportHtml(report) {
  const data = report?.data || { rows: [], status_counts: [], total: 0, generated_at: "", applicant_name: "", month: "" };
  const total = Number(data.total) || 0;
  const statusBars = total ? list(data.status_counts).filter(([, rawCount]) => Number(rawCount) > 0).map(([status, rawCount]) => {
    const count = Number(rawCount) || 0;
    const percent = count / total * 100;
    const percentText = percent.toFixed(1);
    return `<div class="report-stat"><div class="report-stat-label"><span>${label(status)}:</span><strong>${esc(count)} (${percentText}%)</strong></div><div class="report-bar" role="img" aria-label="${esc(`${label(status)}: ${count} of ${total} applications, ${percentText} percent`)}"><span style="width:${percentText}%"></span></div></div>`;
  }).join("") : '<p class="muted">No applications in this period.</p>';
  const period = data.period_label || data.month || "All";
  return `<article id="reportPrint" class="print-sheet report">
    <h1>Job Application Report</h1><p>Generated: ${esc(displayDate(data.generated_at))}</p>
    ${data.applicant_name ? `<p>Applicant: ${esc(data.applicant_name)}</p>` : ""}
    <p>Period: ${esc(period)}</p>
    <h2>Summary</h2><p>Total applications: <strong>${esc(data.total)}</strong></p>
    <div class="report-stats">${statusBars}</div>
    <h2>Applications</h2>
    <div class="report-table-wrap"><table><thead><tr><th>Date</th><th>Company</th><th>Role</th><th>Location</th><th>Status</th></tr></thead>
    <tbody>${list(data.rows).map(row => `<tr><td>${esc(displayDate(row.date))}</td><td>${esc(row.company)}</td><td>${esc(row.role)}</td><td>${esc(row.location)}</td><td>${label(row.status)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No applications.</td></tr>'}</tbody></table></div>
  </article>`;
}

async function renderProfile() {
  if (!state.profile) state.profile = await request("/profile");
  const p = state.profile.personal || {};
  const legacyName = p.name && !p.given_name && !p.family_name
    ? `<p class="muted">Previous full name: ${esc(p.name)}. Enter given and family names before saving.</p>`
    : "";
  app.innerHTML = `
    ${pageHead("User Profile", "Facts used for tailored CV generation.")}
    <form id="profileForm" class="card form">
      ${legacyName}
      <div class="form-grid">
        <label>Given name<input name="given_name" value="${esc(p.given_name || "")}" required></label>
        <label>Family name<input name="family_name" value="${esc(p.family_name || "")}" required></label>
        <label>Email<input name="email" type="email" value="${esc(p.email || "")}" required></label>
        ${["phone", "city", "linkedin", "github", "website"].map(key =>
          `<label>${label(key)}<input name="${key}" value="${esc(p[key] || "")}"></label>`).join("")}
      </div>
      <label>Skills<input name="skills" value="${esc(list(state.profile.skills).join(", "))}" placeholder="Python, FastAPI, SQL"></label>
      <label>Skills and experience description<textarea name="experience" rows="14">${esc(state.profile.experience || "")}</textarea></label>
      <div class="actions"><button type="submit">Save profile</button></div>
    </form>
    <section class="card">
      <div class="section-head"><div><h2>CV files</h2><p>Stored for reference. Generation uses profile text.</p></div></div>
      <form id="profileCvForm" class="upload-line"><input name="files" type="file" accept=".pdf,.docx" multiple required><button>Upload</button></form>
      <ul class="file-links">${list(state.profile.cv_files).map(name => `<li>${esc(name)}</li>`).join("") || "<li>No CV files uploaded.</li>"}</ul>
    </section>`;
}

const desktopApi = () => window.pywebview?.api;

async function desktopCall(method, ...args) {
  const api = desktopApi();
  if (!api) throw new Error("Workspace management requires the desktop application");
  if (typeof api[method] !== "function") throw new Error("Desktop integration unavailable. Restart Job Tracker.");
  return api[method](...args);
}

const workspaceRecord = item => typeof item === "string" ? { path: item } : (item || {});

function workspaceListHtml(data) {
  const activeId = String(data?.active_id || "");
  const records = list(data?.workspaces).map(workspaceRecord);
  if (!records.length) return '<p class="empty">No remembered workspaces.</p>';
  return `<div class="workspace-list">${records.map(record => {
    const path = String(record.path || "");
    const id = String(record.id || "");
    const name = String(record.name || id || "Unnamed workspace");
    const isActive = id === activeId;
    const available = record.available !== false;
    return `<article class="workspace-row">
      <div><strong>${esc(name)}</strong>
        <span>${esc(record.applicant_name || "Unnamed profile")}</span>
        <span class="workspace-path">${esc(path)}</span>
        <small>${isActive ? "Active" : available ? "Available" : "Unavailable"}</small>
      </div>
      <div class="actions">
        <form class="inline-form" data-rename-workspace="${esc(id)}">
          <label>Workspace name<input name="workspace_name" value="${esc(record.name || "")}" placeholder="Folder name when blank"></label>
          <button type="submit" class="secondary">Rename</button>
        </form>
        <button type="button" class="secondary" data-activate-workspace="${esc(id)}" ${isActive || !available ? "disabled" : ""}>Switch</button>
        <button type="button" class="text-button" data-forget-workspace="${esc(id)}" ${isActive ? "disabled" : ""}>Forget</button>
      </div>
    </article>`;
  }).join("")}</div>`;
}

async function renderSettings() {
  if (!desktopApi()) {
    app.innerHTML = `${pageHead("Settings", "Workspace and storage location.")}
      <section class="card"><p>Workspace management requires the desktop application</p></section>`;
    return;
  }
  [state.workspaces, state.storage] = await Promise.all([
    desktopCall("get_workspaces"),
    desktopCall("get_storage_status"),
  ]);
  const active = state.workspaces?.active || "";
  const activeId = String(state.workspaces?.active_id || "");
  const activeRecord = list(state.workspaces?.workspaces).map(workspaceRecord).find(record => String(record.id || "") === activeId);
  const dataFolder = state.workspaces?.data_folder || "";
  app.innerHTML = `
    ${pageHead("Settings", "Workspace and storage location.")}
    <section class="card">
      <h2>Job Tracker data folder</h2>
      <p>Databases, uploaded files, profiles, and reports for all workspaces are stored below this folder.</p>
      <p class="workspace-path">${esc(dataFolder || "Direct workspace override")}</p>
    </section>
    <section class="card">
      <div class="section-head">
        <div><h2>Storage health</h2>
          <p>Read-only preview. Diagnostic exports include only these counts, versions, sizes, and status values.</p>
        </div>
        <button type="button" class="secondary" data-export-diagnostics>Export diagnostic summary</button>
      </div>
      <ul class="file-links">
        <li>Database integrity: ${esc(state.storage?.database_integrity || "unknown")}</li>
        <li>Foreign-key violations: ${esc(state.storage?.foreign_key_violations ?? "unknown")}</li>
        <li>Managed files: ${esc(state.storage?.managed_files?.healthy ?? 0)} healthy,
          ${esc(state.storage?.managed_files?.missing ?? 0)} missing,
          ${esc(state.storage?.managed_files?.corrupt ?? 0)} corrupt</li>
        <li>Workspace size: ${esc(formatBytes(state.storage?.workspace_size_bytes))};
          free disk: ${esc(formatBytes(state.storage?.free_space_bytes))}</li>
        <li>Latest backup: ${state.storage?.latest_backup_at
          ? `${esc(state.storage.latest_backup_at)} (${esc(state.storage.backup_age_days)} days old)`
          : "none"}</li>
      </ul>
    </section>
    <section class="card">
      <div class="section-head">
        <div><h2>Backup and export</h2>
          <p>Schema version ${esc(state.storage?.schema_version || "")}. Migration backups are retained separately from user exports.</p>
          <p class="workspace-path">${esc(state.storage?.backup_location || "")}</p>
        </div>
        <button type="button" class="secondary" data-export-workspace>Create verified export</button>
      </div>
      <p>${esc(state.storage?.recovery || "")}</p>
      <ul class="file-links">${list(state.storage?.migration_backups).map(backup =>
        `<li>Schema ${esc(backup.source_schema_version)} → ${esc(backup.target_schema_version)}
          · ${esc(backup.created_at)} (${esc(ageDays(backup.created_at))} days old)
          · ${esc(backup.size)} bytes
          · ${backup.verified ? "verified" : "unverified"}</li>`).join("") || "<li>No migration backup has been needed.</li>"}</ul>
    </section>
    <section class="card form">
      <div class="section-head"><div><h2>Current workspace</h2><strong>${esc(activeRecord?.name || "None")}</strong><p class="workspace-path">${esc(active || "None")}</p></div>
        <button type="button" class="secondary" data-import-workspace ${dataFolder ? "" : "disabled"}>Import existing workspace</button>
      </div>
      ${workspaceListHtml(state.workspaces)}
    </section>
    <form id="createWorkspaceForm" class="card form">
      <div><h2>Create workspace</h2><p class="muted">A managed workspace folder will be created automatically below the Job Tracker data folder.</p></div>
      <label>Workspace name (optional)<input name="workspace_name" placeholder="Folder name will be used when blank"></label>
      <div class="form-grid">
        <label>Given name<input name="given_name" required></label>
        <label>Family name<input name="family_name" required></label>
        <label>Email<input name="email" type="email" required></label>
        ${["phone", "city", "linkedin", "github", "website"].map(key =>
          `<label>${label(key)}<input name="${key}"></label>`).join("")}
      </div>
      <div class="actions"><button type="submit">Create workspace</button></div>
    </form>`;
}

async function navigate() {
  const { route } = routeInfo();
  const jobRoutes = new Set(["home", "new-job", "jobs", "report"]);
  document.querySelectorAll("[data-route]").forEach(link => link.classList.toggle("active", link.dataset.route === route));
  state.detail = null;
  try {
    if (jobRoutes.has(route) && !state.jobs.length) await loadJobs();
    if (route === "home") renderDashboard();
    else if (route === "new-job") renderNewJob();
    else if (route === "jobs") renderJobsPage();
    else if (route === "report") renderReport();
    else if (route === "profile") await renderProfile();
    else if (route === "settings") await renderSettings();
    else location.hash = "#/home";
    app.focus();
    if (state.notice) {
      setStatus(state.notice, "bad");
      state.notice = "";
    } else if (jobRoutes.has(route)) {
      setStatus(
        state.loadErrors.length ? `${state.jobs.length} jobs; ${state.loadErrors.length} failed to load` : `${state.jobs.length} jobs`,
        state.loadErrors.length ? "bad" : "ok",
      );
    } else {
      setStatus("Ready", "ok");
    }
  } catch (error) {
    app.innerHTML = `<div class="card error">${esc(error.message)}</div>`;
    setStatus(error.message, "bad");
  }
}

app.addEventListener("input", event => {
  if (event.target.matches("#jobSearch, #jobStatus")) filterJobs();
});

app.addEventListener("change", event => {
  if (event.target.id === "newFiles") {
    state.newFiles.push(...Array.from(event.target.files).map(file => ({ file, category: "attachments" })));
    renderNewFileQueue();
  }
  if (event.target.matches("[data-new-file]")) state.newFiles[Number(event.target.dataset.newFile)].category = event.target.value;
});

app.addEventListener("click", async event => {
  if (event.target.closest("[data-download-report]")) {
    try { await download(`/report/pdf${reportQuery() ? `?${reportQuery()}` : ""}`, "jobtracker-report.pdf"); setStatus("Report PDF downloaded", "ok"); }
    catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.closest("[data-export-workspace]")) {
    try {
      const result = await desktopCall("create_workspace_export");
      await renderSettings();
      setStatus(`${result.message}: ${result.path}`, "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.closest("[data-export-diagnostics]")) {
    try {
      const result = await desktopCall("export_diagnostics");
      setStatus(`${result.message}: ${result.path}`, "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.closest("[data-import-workspace]")) {
    try {
      const path = await desktopCall("choose_workspace_folder");
      if (path) await desktopCall("import_workspace", String(path));
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  const activateWorkspace = event.target.closest("[data-activate-workspace]");
  if (activateWorkspace) {
    try {
      await desktopCall("activate_workspace", activateWorkspace.dataset.activateWorkspace);
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  const forgetWorkspace = event.target.closest("[data-forget-workspace]");
  if (forgetWorkspace) {
    try {
      await desktopCall("forget_workspace", forgetWorkspace.dataset.forgetWorkspace);
      await renderSettings();
      setStatus("Workspace forgotten; data was not deleted", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  const jobRow = event.target.closest("[data-job]");
  if (jobRow) {
    history.replaceState(null, "", `#/jobs?job=${encodeURIComponent(jobRow.dataset.job)}`);
    await selectJob(jobRow.dataset.job);
    return;
  }
  const remove = event.target.closest("[data-remove-new]");
  if (remove) {
    state.newFiles.splice(Number(remove.dataset.removeNew), 1);
    renderNewFileQueue();
    return;
  }
  if (event.target.closest("[data-save-status]")) {
    const status = document.querySelector("#detailStatus").value;
    await request(`/jobs/${encodeURIComponent(state.detail.id)}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await loadJobs(); await selectJob(state.detail.id); return;
  }
  if (event.target.closest("[data-edit-job]")) {
    document.querySelector("#jobDetail").innerHTML = editJobHtml(state.detail);
    return;
  }
  if (event.target.closest("[data-cancel-edit]")) {
    document.querySelector("#jobDetail").innerHTML = detailHtml(state.detail);
    return;
  }
  if (event.target.closest("[data-attach]")) {
    const files = document.querySelector("#attachFiles").files;
    if (!files.length) return setStatus("Choose files first", "bad");
    const data = new FormData();
    Array.from(files).forEach(file => data.append("files", file, file.name));
    data.append("file_categories", JSON.stringify([document.querySelector("#attachCategory").value]));
    await request(`/jobs/${encodeURIComponent(state.detail.id)}/files`, { method: "POST", body: data });
    await selectJob(state.detail.id); return;
  }
  if (event.target.closest("[data-generate-cv]")) {
    const remote = state.llm?.endpoint_scope === "remote";
    const remoteConfirmed = remote && window.confirm(
      `Send profile, job, and document content to remote ${state.llm.provider} model ${state.llm.model}?`,
    );
    if (remote && !remoteConfirmed) return setStatus("Remote disclosure cancelled", "bad");
    setStatus("Generating CV...", "busy");
    try {
      const result = await request(`/jobs/${encodeURIComponent(state.detail.id)}/cv-draft`, {
        method: "POST",
        body: JSON.stringify({ remote_disclosure_confirmed: remoteConfirmed }),
      });
      document.querySelector("#cvDraft").value = result.content;
      setStatus("CV generated", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.closest("[data-save-cv]")) {
    const content = document.querySelector("#cvDraft").value;
    await request(`/jobs/${encodeURIComponent(state.detail.id)}/cv-draft`, { method: "PUT", body: JSON.stringify({ content }) });
    setStatus("CV saved", "ok"); return;
  }
  if (event.target.closest("[data-generate-cv-pdf]")) {
    setStatus("Generating PDF...", "busy");
    try {
      await download(`/jobs/${encodeURIComponent(state.detail.id)}/cv-pdf`, "generated_cv.pdf", {
        method: "POST",
        body: JSON.stringify({ content: document.querySelector("#cvDraft").value }),
      });
      setStatus("CV PDF downloaded", "ok");
    } catch (error) {
      setStatus(error.message, "bad");
    }
    return;
  }
  if (event.target.closest("[data-report-generate]")) {
    const query = reportQuery();
    setStatus("Generating report...", "busy");
    try {
      state.report = await request(`/report${query ? `?${query}` : ""}`, { method: "POST" });
      document.querySelector("#reportArea").className = "";
      document.querySelector("#reportArea").innerHTML = reportHtml(state.report);
      setStatus("Report generated", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
});

app.addEventListener("submit", async event => {
  event.preventDefault();
  if (event.target.id === "editJobForm") {
    const id = state.detail.id;
    const raw = new FormData(event.target);
    const payload = Object.fromEntries(raw.entries());
    ["required_skills", "nice_to_have_skills", "language_requirements"].forEach(key => { payload[key] = csvList(raw.get(key)); });
    payload.relevant_for_arbeitsagentur = raw.get("relevant_for_arbeitsagentur") === "on";
    if (payload.status !== state.detail.status && payload.status_date === state.detail.status_date) delete payload.status_date;
    setStatus("Saving job...", "busy");
    try {
      await request(`/jobs/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) });
      await loadJobs();
      await selectJob(id);
      filterJobs();
      setStatus("Job saved", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.id === "jobForm") {
    const generateCv = Boolean(event.submitter?.matches("[data-create-cv]"));
    const raw = new FormData(event.target);
    const payload = Object.fromEntries(raw.entries());
    payload.relevant_for_arbeitsagentur = raw.get("relevant_for_arbeitsagentur") === "on";
    const jobDescription = payload.job_description || "";
    delete payload.job_description;
    const data = new FormData();
    data.append("payload", JSON.stringify(payload));
    data.append("job_description", jobDescription);
    state.newFiles.forEach(item => data.append("files", item.file, item.file.name));
    data.append("file_categories", JSON.stringify(state.newFiles.map(item => ({ name: item.file.name, category: item.category }))));
    setStatus("Creating job...", "busy");
    try {
      const created = await request("/jobs", { method: "POST", body: data });
      if (generateCv) {
        setStatus("Generating CV...", "busy");
        try {
          await request(`/jobs/${encodeURIComponent(created.id)}/cv-draft`, { method: "POST" });
        } catch (error) {
          state.notice = error.message;
        }
      }
      await loadJobs();
      location.hash = `#/jobs?job=${encodeURIComponent(created.id)}`;
    } catch (error) { setStatus(error.message, "bad"); }
  }
  if (event.target.id === "profileForm") {
    const raw = new FormData(event.target);
    const personal = Object.fromEntries(["given_name", "family_name", "email", "phone", "city", "linkedin", "github", "website"].map(key => [key, raw.get(key) || ""]));
    const payload = { personal, skills: String(raw.get("skills") || "").split(",").map(x => x.trim()).filter(Boolean), experience: raw.get("experience") || "" };
    state.profile = await request("/profile", { method: "PUT", body: JSON.stringify(payload) });
    setStatus("Profile saved", "ok");
  }
  if (event.target.id === "createWorkspaceForm") {
    const raw = new FormData(event.target);
    const workspaceName = String(raw.get("workspace_name") || "");
    const profile = Object.fromEntries(["given_name", "family_name", "email", "phone", "city", "linkedin", "github", "website"].map(key => [key, raw.get(key) || ""]));
    try {
      await desktopCall("create_workspace", profile, workspaceName);
    } catch (error) { setStatus(error.message, "bad"); }
  }
  if (event.target.matches("[data-rename-workspace]")) {
    const raw = new FormData(event.target);
    try {
      await desktopCall(
        "rename_workspace",
        event.target.dataset.renameWorkspace,
        String(raw.get("workspace_name") || ""),
      );
      await renderSettings();
      setStatus("Workspace renamed", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
  }
  if (event.target.id === "profileCvForm") {
    const data = new FormData(event.target);
    const result = await request("/profile/cvs", { method: "POST", body: data });
    state.profile = result.profile;
    await renderProfile();
    setStatus("CV files uploaded", "ok");
  }
});

window.addEventListener("hashchange", navigate);
navigate();
