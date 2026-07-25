from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import os
from pathlib import Path
import socket
import sqlite3
import sys
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any, Sequence

import uvicorn

from jobtracker.database import SCHEMA_VERSION
from jobtracker.paths import (
    WorkspaceRegistry,
    WorkspaceResolution,
    load_workspace_registry,
    resolve_desktop_workspace,
    save_workspace_registry,
    validate_workspace_folder,
)
from jobtracker.repositories import build_application_service
from jobtracker.schemas import PersonalProfileSchema
from jobtracker.services import (
    load_profile,
    load_project_context,
    profile_display_name,
    save_profile,
)
from jobtracker.webapp import STATIC_DIR, create_app


HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 15.0


SETUP_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job Tracker setup</title>
  <style>
    :root { font: 16px system-ui, sans-serif; color: #182033; background: #f4f6fa; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; }
    main { width: min(720px, calc(100% - 2rem)); background: white; padding: 2rem; border-radius: 14px; box-shadow: 0 12px 35px #1b2b4b22; }
    h1 { margin-top: 0; } form, .grid { display: grid; gap: 1rem; }
    .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    label { display: grid; gap: .35rem; font-weight: 650; }
    input { box-sizing: border-box; width: 100%; padding: .7rem; border: 1px solid #cbd2df; border-radius: 8px; font: inherit; }
    .folder, .actions { display: flex; gap: .65rem; align-items: end; }
    .folder label { flex: 1; } button { padding: .72rem 1rem; border: 0; border-radius: 8px; background: #2857c5; color: white; font: inherit; font-weight: 700; cursor: pointer; }
    button.secondary { background: #e8edf8; color: #20345f; }
    #message { min-height: 1.4rem; color: #9b2531; }
    .muted { color: #68738a; overflow-wrap: anywhere; }
    @media (max-width: 620px) { .grid { grid-template-columns: 1fr; } .folder, .actions { align-items: stretch; flex-direction: column; } }
  </style>
</head>
<body>
<main>
  <h1 id="title">Set up Job Tracker</h1>
  <p id="intro" class="muted">Choose an empty local folder and create its applicant profile.</p>
  <form id="setupForm">
    <div class="folder">
      <label>Data folder<input id="folder" readonly required></label>
      <button id="browse" type="button" class="secondary">Choose folder</button>
    </div>
    <div class="grid">
      <label>Given name<input name="given_name" required></label>
      <label>Family name<input name="family_name" required></label>
      <label>Email<input name="email" type="email" required></label>
      <label>Phone<input name="phone"></label>
      <label>City<input name="city"></label>
      <label>LinkedIn<input name="linkedin"></label>
      <label>GitHub<input name="github"></label>
      <label>Website<input name="website"></label>
    </div>
    <p id="legacy" class="muted"></p>
    <p id="message" role="alert"></p>
    <div class="actions">
      <button id="submitButton" type="submit">Create workspace</button>
      <button id="openExisting" type="button" class="secondary">Open existing</button>
      <button id="cancel" type="button" class="secondary">Cancel</button>
    </div>
  </form>
</main>
<script>
  let setupState = {};
  const message = document.querySelector("#message");
  const setMessage = text => { message.textContent = text || ""; };
  const profile = () => Object.fromEntries(
    ["given_name", "family_name", "email", "phone", "city", "linkedin", "github", "website"]
      .map(name => [name, document.querySelector(`[name="${name}"]`).value])
  );
  window.addEventListener("pywebviewready", async () => {
    try {
      setupState = await window.pywebview.api.get_setup_state();
      const personal = setupState.personal || {};
      Object.entries(personal).forEach(([name, value]) => {
        const input = document.querySelector(`[name="${name}"]`);
        if (input) input.value = value || "";
      });
      if (setupState.path) document.querySelector("#folder").value = setupState.path;
      if (setupState.mode === "complete") {
        document.querySelector("#title").textContent = "Complete applicant profile";
        document.querySelector("#intro").textContent = "Confirm required profile information before this workspace opens.";
        document.querySelector("#folder").readOnly = true;
        document.querySelector("#browse").hidden = true;
        document.querySelector("#openExisting").hidden = true;
        document.querySelector("#submitButton").textContent = "Save and open";
      }
      if (setupState.legacy_name) document.querySelector("#legacy").textContent =
        `Previous full name: ${setupState.legacy_name}. Enter given and family names; no split was guessed.`;
      setMessage(setupState.error);
    } catch (error) { setMessage(error.message); }
  });
  document.querySelector("#browse").addEventListener("click", async () => {
    try {
      const path = await window.pywebview.api.choose_workspace_folder();
      if (path) document.querySelector("#folder").value = path;
    } catch (error) { setMessage(error.message); }
  });
  document.querySelector("#openExisting").addEventListener("click", async () => {
    try {
      const path = await window.pywebview.api.choose_workspace_folder();
      if (path) await window.pywebview.api.open_workspace(path);
    } catch (error) { setMessage(error.message); }
  });
  document.querySelector("#cancel").addEventListener("click", () => window.pywebview.api.cancel_setup());
  document.querySelector("#setupForm").addEventListener("submit", async event => {
    event.preventDefault(); setMessage("");
    try {
      if (setupState.mode === "complete") await window.pywebview.api.complete_workspace(profile());
      else await window.pywebview.api.create_workspace(document.querySelector("#folder").value, profile());
    } catch (error) { setMessage(error.message); }
  });
</script>
</body>
</html>
"""


def _initialize_application(
    data_dir: str | Path | None = None,
    *,
    enforce_workspace: bool = False,
) -> Any:
    context = load_project_context(
        data_dir=data_dir,
        enforce_workspace=enforce_workspace,
    )
    service = build_application_service(context)
    service.list_jobs()  # Open/upgrade SQLite before showing the window.
    if not (STATIC_DIR / "index.html").is_file():
        raise RuntimeError("Bundled web UI is missing")
    return create_app(service)


def _reserve_loopback_socket() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((HOST, 0))
    listener.listen()
    return listener, listener.getsockname()[1]


def _serve(server: uvicorn.Server, listener: socket.socket, errors: list[BaseException]) -> None:
    try:
        server.run(sockets=[listener])
    except BaseException as exc:  # Forward thread failures to the GUI thread.
        errors.append(exc)
    finally:
        listener.close()


def _wait_for_server(
    server: uvicorn.Server,
    thread: Thread,
    errors: list[BaseException],
) -> None:
    deadline = monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if errors:
            raise RuntimeError("Local server failed to start") from errors[0]
        if not thread.is_alive():
            raise RuntimeError("Local server stopped during startup")
        if monotonic() >= deadline:
            raise TimeoutError("Local server startup timed out")
        sleep(0.01)


@dataclass
class _ServerHandle:
    server: uvicorn.Server
    thread: Thread
    errors: list[BaseException]
    port: int


def _start_application(data_dir: Path) -> _ServerHandle:
    application = _initialize_application(data_dir, enforce_workspace=True)
    listener, port = _reserve_loopback_socket()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host=HOST,
            port=port,
            access_log=False,
            log_level="warning",
        )
    )
    errors: list[BaseException] = []
    thread = Thread(target=_serve, args=(server, listener, errors), daemon=True)
    thread.start()
    try:
        _wait_for_server(server, thread, errors)
    except Exception:
        server.should_exit = True
        thread.join(timeout=2)
        raise
    return _ServerHandle(server, thread, errors, port)


def _stop_application(handle: _ServerHandle | None) -> None:
    if handle is None:
        return
    handle.server.should_exit = True
    handle.thread.join(timeout=5)
    if handle.thread.is_alive():
        handle.server.force_exit = True
        handle.thread.join(timeout=2)
    if handle.thread.is_alive():
        raise RuntimeError("Local server did not stop")
    if handle.errors:
        raise RuntimeError("Local server failed") from handle.errors[0]


def _probe_workspace_database(data_dir: Path) -> None:
    database = data_dir / "jobtracker.sqlite3"
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"Workspace database cannot be opened: {database}") from exc
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported workspace database schema {version}; expected {SCHEMA_VERSION}"
            )
        application_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'applications'"
        ).fetchone()
        if application_table is None:
            raise ValueError("Workspace database is missing the applications table")
    finally:
        connection.close()


def _profile_state(data_dir: Path) -> tuple[bool, dict[str, Any]]:
    profile = load_profile(data_dir / "profile")
    personal = profile.get("personal", {})
    if not isinstance(personal, dict):
        return False, {}
    try:
        canonical = PersonalProfileSchema.model_validate(personal)
    except ValueError:
        return False, personal
    return True, canonical.model_dump()


class WorkspaceBridge:
    def __init__(
        self,
        webview: Any,
        resolution: WorkspaceResolution,
        handle: _ServerHandle | None = None,
    ) -> None:
        self.webview = webview
        self.window: Any | None = None
        self.resolution = resolution
        self.handle = handle
        self.active = resolution.data_dir if handle is not None else None
        self.pending: Path | None = None
        self.pending_personal: dict[str, Any] = {}
        self.lock = Lock()
        self.override = resolution.status in {"explicit", "environment"}

    def set_pending(self, path: Path, personal: dict[str, Any]) -> None:
        self.pending = path
        self.pending_personal = personal

    def _registry(self) -> WorkspaceRegistry:
        try:
            return load_workspace_registry() or WorkspaceRegistry()
        except ValueError:
            return WorkspaceRegistry()

    def _require_registry_control(self) -> None:
        if self.override:
            raise ValueError(
                "Workspace switching is disabled while JOBTRACKER_DATA_DIR is active"
            )

    def _save_active(self, path: Path) -> None:
        registry = self._registry()
        save_workspace_registry(
            WorkspaceRegistry(
                active=path,
                workspaces=(*registry.workspaces, path),
            )
        )

    def _switch(
        self,
        path: Path,
        *,
        personal: dict[str, Any] | None = None,
        create: bool = False,
    ) -> dict[str, str]:
        self._require_registry_control()
        with self.lock:
            validated = validate_workspace_folder(path, "new" if create else "existing")
            if not create:
                _probe_workspace_database(validated)
            new_handle = _start_application(validated)
            try:
                if personal is not None:
                    save_profile(
                        validated / "profile",
                        {"personal": personal, "skills": [], "experience": ""},
                    )
                self._save_active(validated)
                old_handle = self.handle
                self.handle = new_handle
                self.active = validated
                self.pending = None
                if self.window is None:
                    raise RuntimeError("Desktop window is unavailable")
                self.window.load_url(f"http://{HOST}:{new_handle.port}/")
            except Exception:
                _stop_application(new_handle)
                raise
            _stop_application(old_handle)
            return {"active": str(validated)}

    def get_setup_state(self) -> dict[str, Any]:
        personal = dict(self.pending_personal)
        return {
            "mode": "complete" if self.pending is not None else self.resolution.status,
            "path": str(self.pending) if self.pending is not None else "",
            "personal": {
                key: personal.get(key, "")
                for key in (
                    "given_name",
                    "family_name",
                    "email",
                    "phone",
                    "city",
                    "linkedin",
                    "github",
                    "website",
                )
            },
            "legacy_name": str(personal.get("name", "")).strip(),
            "error": self.resolution.error or "",
        }

    def get_workspaces(self) -> dict[str, Any]:
        registry = self._registry()
        active = self.active or registry.active
        paths = list(registry.workspaces)
        if active is not None:
            paths.append(active)
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in paths:
            canonical = path.resolve()
            key = os.path.normcase(str(canonical)).casefold()
            if key in seen:
                continue
            seen.add(key)
            available = canonical.is_dir() and (canonical / "jobtracker.sqlite3").is_file()
            applicant_name = canonical.name
            if available:
                try:
                    applicant_name = (
                        profile_display_name(load_profile(canonical / "profile"))
                        or canonical.name
                    )
                except (OSError, UnicodeError, ValueError):
                    pass
            records.append(
                {
                    "path": str(canonical),
                    "applicant_name": applicant_name,
                    "available": available,
                }
            )
        return {
            "active": str(active.resolve()) if active is not None else "",
            "workspaces": records,
        }

    def choose_workspace_folder(self) -> str:
        if self.window is None:
            raise RuntimeError("Desktop window is unavailable")
        selected = self.window.create_file_dialog(
            self.webview.FileDialog.FOLDER,
            allow_multiple=False,
        )
        return str(selected[0]) if selected else ""

    def create_workspace(self, path: str, profile: dict[str, Any]) -> dict[str, str]:
        if not path.strip():
            raise ValueError("Choose a data folder")
        personal = PersonalProfileSchema.model_validate(profile).model_dump()
        return self._switch(Path(path), personal=personal, create=True)

    def open_workspace(self, path: str) -> dict[str, Any]:
        self._require_registry_control()
        validated = validate_workspace_folder(path, "existing")
        _probe_workspace_database(validated)
        complete, personal = _profile_state(validated)
        if not complete:
            self.set_pending(validated, personal)
            if self.window is None:
                raise RuntimeError("Desktop window is unavailable")
            self.window.load_html(SETUP_HTML)
            return {"needs_profile": True, "path": str(validated)}
        return self._switch(validated)

    def complete_workspace(self, profile: dict[str, Any]) -> dict[str, str]:
        if self.pending is None:
            raise ValueError("No workspace is awaiting profile completion")
        personal = PersonalProfileSchema.model_validate(profile).model_dump()
        return self._switch(self.pending, personal=personal)

    def activate_workspace(self, path: str) -> dict[str, Any]:
        self._require_registry_control()
        selected = Path(path).expanduser().resolve()
        registry = self._registry()
        selected_key = os.path.normcase(str(selected)).casefold()
        registered = {
            os.path.normcase(str(candidate.resolve())).casefold()
            for candidate in registry.workspaces
        }
        if selected_key not in registered:
            raise ValueError("Workspace is not registered")
        return self.open_workspace(str(selected))

    def forget_workspace(self, path: str) -> dict[str, Any]:
        self._require_registry_control()
        selected = Path(path).expanduser().resolve()
        registry = self._registry()
        if registry.active is not None and os.path.normcase(
            str(registry.active)
        ).casefold() == os.path.normcase(str(selected)).casefold():
            raise ValueError("Active workspace cannot be forgotten")
        remaining = tuple(
            candidate
            for candidate in registry.workspaces
            if os.path.normcase(str(candidate.resolve())).casefold()
            != os.path.normcase(str(selected)).casefold()
        )
        saved = save_workspace_registry(
            WorkspaceRegistry(active=registry.active, workspaces=remaining)
        )
        return {
            "active": str(saved.active) if saved.active is not None else "",
            "workspaces": [str(candidate) for candidate in saved.workspaces],
        }

    def cancel_setup(self) -> None:
        if self.window is None:
            return
        if self.handle is not None and self.active is not None:
            self.pending = None
            self.pending_personal = {}
            self.window.load_url(f"http://{HOST}:{self.handle.port}/")
            return
        self.window.destroy()

    def stop(self) -> None:
        _stop_application(self.handle)
        self.handle = None


def run_desktop() -> None:
    resolution = resolve_desktop_workspace()
    handle: _ServerHandle | None = None
    pending: tuple[Path, dict[str, Any]] | None = None

    if resolution.data_dir is not None and resolution.status in {
        "explicit",
        "environment",
        "active",
        "adopt_default",
    }:
        if resolution.status in {"active", "adopt_default"}:
            validated = validate_workspace_folder(resolution.data_dir, "existing")
            _probe_workspace_database(validated)
            complete, personal = _profile_state(validated)
            if not complete:
                pending = (validated, personal)
            else:
                handle = _start_application(validated)
        else:
            handle = _start_application(resolution.data_dir)

    if resolution.status == "adopt_default" and handle is not None:
        assert resolution.registry is not None
        try:
            save_workspace_registry(resolution.registry)
        except Exception:
            _stop_application(handle)
            raise

    try:
        webview = import_module("webview")
    except Exception:
        _stop_application(handle)
        raise
    bridge = WorkspaceBridge(webview, resolution, handle)
    if pending is not None:
        bridge.set_pending(*pending)

    try:
        if handle is not None:
            window = webview.create_window(
                "Job Tracker",
                f"http://{HOST}:{handle.port}/",
                js_api=bridge,
                width=1280,
                height=800,
            )
        else:
            window = webview.create_window(
                "Job Tracker setup",
                html=SETUP_HTML,
                js_api=bridge,
                width=900,
                height=760,
            )
        bridge.window = window
        webview.start()
    finally:
        bridge.stop()


def smoke_test() -> None:
    resolution = resolve_desktop_workspace()
    if resolution.data_dir is None:
        raise RuntimeError("No workspace is configured; complete desktop setup first")
    _initialize_application(resolution.data_dir, enforce_workspace=True)
    if resolution.status == "adopt_default":
        assert resolution.registry is not None
        save_workspace_registry(resolution.registry)


def _show_startup_error(exc: Exception) -> None:
    message = f"Job Tracker could not start.\n\n{type(exc).__name__}: {exc}"
    try:
        from tkinter import messagebox

        messagebox.showerror("Job Tracker startup error", message)
    except Exception:
        pass
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args == ["--smoke-test"]:
            smoke_test()
        elif args:
            raise ValueError(f"Unknown argument: {args[0]}")
        else:
            run_desktop()
    except Exception as exc:
        _show_startup_error(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
