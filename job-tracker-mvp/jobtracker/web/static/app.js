const API = "/api";
const STATUSES = [
  "draft", "applied", "confirmation_received", "interview_invited",
  "interview_done", "task_received", "offer_received", "rejected",
  "withdrawn", "no_response", "closed",
];
const CATEGORIES = ["attachments", "cv", "motivation_letter", "job_description", "evidence", "screenshots"];
const CLOSED = new Set(["rejected", "withdrawn", "no_response", "closed"]);
const state = { jobs: [], detail: null, newFiles: [], profile: null, report: null, notice: "" };
const app = document.querySelector("#app");
const apiStatus = document.querySelector("#apiStatus");
const today = new Date().toISOString().slice(0, 10);

const esc = (value = "") => String(value)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const label = (value = "") => String(value).replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
const list = value => Array.isArray(value) ? value : [];

function setStatus(text, tone = "") {
  apiStatus.textContent = text;
  apiStatus.dataset.tone = tone;
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: {
      Accept: "application/json",
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    let message = await response.text();
    try { message = JSON.parse(message).detail || message; } catch {}
    throw new Error(message || `${response.status} ${response.statusText}`);
  }
  return response.json();
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
    list(names).filter(Boolean).map(name => `<li><span class="pill">${label(category)}</span>${esc(name)}</li>`)
  );
  return rows.length ? `<ul class="file-links">${rows.join("")}</ul>` : `<p class="muted">No files.</p>`;
}

async function loadJobs() {
  state.jobs = await request("/jobs");
}

function renderDashboard() {
  const s = stats();
  const interviews = state.jobs.filter(job => job.status === "interview_invited");
  app.innerHTML = `
    ${pageHead("Dashboard", "Application status at a glance.", '<a class="button" href="#/new-job">Add job</a>')}
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
      <span>${esc(job.date_applied)}</span><span class="pill">${label(job.status)}</span>
    </button>`).join("");
}

function renderJobsPage() {
  const selected = routeInfo().query.get("job");
  app.innerHTML = `
    ${pageHead("All Jobs", "Search applications and inspect complete records.", '<a class="button" href="#/new-job">Add job</a>')}
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
    state.detail = await request(`/jobs/${encodeURIComponent(id)}`);
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
    ["Applied", job.date_applied], ["Status date", job.status_date], ["Location", job.location],
    ["Source", job.source], ["Method", job.application_method], ["Contact", job.contact_person],
    ["Email", job.contact_email], ["Next action", job.next_action], ["Next date", job.next_action_date],
  ].filter(([, value]) => value);
  return `
    <div class="detail-title"><div><p class="eyebrow">Selected job</p><h2>${esc(job.company)}</h2><p>${esc(job.role)}</p></div><span class="pill">${label(job.status)}</span></div>
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
      <div class="actions"><button data-generate-cv>Generate CV</button><button class="secondary" data-save-cv>Save draft</button><button class="secondary" data-print-cv>Print / PDF</button></div>
      <textarea id="cvDraft" class="cv-editor" rows="22" placeholder="Generated CV appears here.">${esc(job.cv_draft)}</textarea>
      <article id="cvPrint" class="print-sheet"><pre class="document">${esc(job.cv_draft)}</pre></article>
    </details>`;
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
  app.innerHTML = `
    ${pageHead("PDF Report", "Generate a printable application report.")}
    <section class="card">
      <div class="report-controls"><label>Month<input id="reportMonth" type="month"></label><button data-report-generate>Generate report</button><button class="secondary" data-print-report disabled>Print / Save PDF</button></div>
      <div id="reportArea" class="empty">Generate report to preview it.</div>
    </section>`;
}

function reportHtml(month = "") {
  const jobs = month ? state.jobs.filter(job => job.date_applied?.startsWith(month)) : state.jobs;
  const counts = jobs.reduce((result, job) => ({ ...result, [job.status]: (result[job.status] || 0) + 1 }), {});
  return `<article id="reportPrint" class="print-sheet report">
    <h1>Job Application Report</h1><p>Generated: ${new Date().toLocaleString()}</p>
    ${month ? `<p>Month: ${esc(month)}</p>` : ""}
    <h2>Summary</h2><p>Total applications: <strong>${jobs.length}</strong></p>
    <div class="report-stats">${Object.entries(counts).map(([status, count]) => `<span>${label(status)}: <strong>${count}</strong></span>`).join("")}</div>
    <h2>Applications</h2>
    <table><thead><tr><th>Date</th><th>Company</th><th>Role</th><th>Location</th><th>Status</th></tr></thead>
    <tbody>${jobs.map(job => `<tr><td>${esc(job.date_applied)}</td><td>${esc(job.company)}</td><td>${esc(job.role)}</td><td>${esc(job.location)}</td><td>${label(job.status)}</td></tr>`).join("")}</tbody></table>
  </article>`;
}

async function renderProfile() {
  if (!state.profile) state.profile = await request("/profile");
  const p = state.profile.personal || {};
  app.innerHTML = `
    ${pageHead("User Profile", "Facts used for tailored CV generation.")}
    <form id="profileForm" class="card form">
      <div class="form-grid">
        ${["name", "email", "phone", "city", "linkedin", "github", "website"].map(key =>
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

function printElement(element) {
  document.querySelectorAll(".print-target").forEach(item => item.classList.remove("print-target"));
  element?.classList.add("print-target");
  window.print();
}

async function navigate() {
  const { route } = routeInfo();
  document.querySelectorAll("[data-route]").forEach(link => link.classList.toggle("active", link.dataset.route === route));
  state.detail = null;
  try {
    if (!state.jobs.length) await loadJobs();
    if (route === "home") renderDashboard();
    else if (route === "new-job") renderNewJob();
    else if (route === "jobs") renderJobsPage();
    else if (route === "report") renderReport();
    else if (route === "profile") await renderProfile();
    else location.hash = "#/home";
    app.focus();
    if (state.notice) {
      setStatus(state.notice, "bad");
      state.notice = "";
    } else {
      setStatus(`${state.jobs.length} jobs`, "ok");
    }
  } catch (error) {
    app.innerHTML = `<div class="card error">${esc(error.message)}</div>`;
    setStatus(error.message, "bad");
  }
}

app.addEventListener("input", event => {
  if (event.target.matches("#jobSearch, #jobStatus")) filterJobs();
  if (event.target.matches("#cvDraft")) {
    const preview = document.querySelector("#cvPrint pre");
    if (preview) preview.textContent = event.target.value;
  }
});

app.addEventListener("change", event => {
  if (event.target.id === "newFiles") {
    state.newFiles.push(...Array.from(event.target.files).map(file => ({ file, category: "attachments" })));
    renderNewFileQueue();
  }
  if (event.target.matches("[data-new-file]")) state.newFiles[Number(event.target.dataset.newFile)].category = event.target.value;
});

app.addEventListener("click", async event => {
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
    setStatus("Generating CV…", "busy");
    try {
      const result = await request(`/jobs/${encodeURIComponent(state.detail.id)}/cv-draft`, { method: "POST" });
      document.querySelector("#cvDraft").value = result.content;
      document.querySelector("#cvPrint pre").textContent = result.content;
      setStatus("CV generated", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.closest("[data-save-cv]")) {
    const content = document.querySelector("#cvDraft").value;
    await request(`/jobs/${encodeURIComponent(state.detail.id)}/cv-draft`, { method: "PUT", body: JSON.stringify({ content }) });
    setStatus("CV saved", "ok"); return;
  }
  if (event.target.closest("[data-print-cv]")) return printElement(document.querySelector("#cvPrint"));
  if (event.target.closest("[data-report-generate]")) {
    const month = document.querySelector("#reportMonth").value;
    setStatus("Generating report…", "busy");
    try {
      state.report = await request(`/report${month ? `?month=${encodeURIComponent(month)}` : ""}`, { method: "POST" });
      document.querySelector("#reportArea").className = "";
      document.querySelector("#reportArea").innerHTML = reportHtml(month);
      document.querySelector("[data-print-report]").disabled = false;
      setStatus("Report generated", "ok");
    } catch (error) { setStatus(error.message, "bad"); }
    return;
  }
  if (event.target.closest("[data-print-report]")) printElement(document.querySelector("#reportPrint"));
});

app.addEventListener("submit", async event => {
  event.preventDefault();
  if (event.target.id === "jobForm") {
    const generateCv = Boolean(event.submitter?.matches("[data-create-cv]"));
    const raw = new FormData(event.target);
    const payload = Object.fromEntries(raw.entries());
    payload.relevant_for_arbeitsagentur = raw.get("relevant_for_arbeitsagentur") === "on";
    const data = new FormData();
    data.append("payload", JSON.stringify(payload));
    data.append("job_description", payload.job_description || "");
    state.newFiles.forEach(item => data.append("files", item.file, item.file.name));
    data.append("file_categories", JSON.stringify(state.newFiles.map(item => ({ name: item.file.name, category: item.category }))));
    setStatus("Creating job…", "busy");
    try {
      const created = await request("/jobs", { method: "POST", body: data });
      if (generateCv) {
        setStatus("Generating CV…", "busy");
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
    const personal = Object.fromEntries(["name", "email", "phone", "city", "linkedin", "github", "website"].map(key => [key, raw.get(key) || ""]));
    const payload = { personal, skills: String(raw.get("skills") || "").split(",").map(x => x.trim()).filter(Boolean), experience: raw.get("experience") || "" };
    state.profile = await request("/profile", { method: "PUT", body: JSON.stringify(payload) });
    setStatus("Profile saved", "ok");
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
window.addEventListener("afterprint", () => document.querySelectorAll(".print-target").forEach(item => item.classList.remove("print-target")));
navigate();
