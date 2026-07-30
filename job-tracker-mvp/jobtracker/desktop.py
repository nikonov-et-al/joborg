from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any, Callable, Sequence

import uvicorn

from jobtracker import __version__
from jobtracker.backups import (
    assert_database_healthy,
    create_workspace_export,
    list_migration_backups,
)
from jobtracker.database import MIN_SUPPORTED_SCHEMA_VERSION, SCHEMA_VERSION
from jobtracker.paths import (
    WorkspaceCatalog,
    WorkspaceRecord,
    WorkspaceResolution,
    WorkspaceSettings,
    load_workspace_catalog,
    resolve_desktop_workspace,
    save_workspace_catalog,
    save_workspace_settings,
    validate_workspaces_root,
    validate_workspace_folder,
    workspace_catalog_file,
    workspace_path,
    workspace_settings_file,
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
from jobtracker.workspace_storage import (
    create_managed_workspace,
    discard_managed_workspace,
    finalize_layout_migration,
    import_managed_workspace,
    layout_migration_phase,
    mark_layout_migration_activated,
    prepare_same_folder_adoption,
    preserve_legacy_registry,
    restore_legacy_registry,
)


HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 15.0
WORKSPACE_API_METHODS = (
    "get_setup_state",
    "get_workspaces",
    "choose_workspace_folder",
    "configure_data_folder",
    "recover_data_folder",
    "create_workspace",
    "import_workspace",
    "activate_workspace",
    "forget_workspace",
    "rename_workspace",
    "get_storage_status",
    "create_workspace_export",
    "cancel_setup",
)
_FOLDER_PICKER_ERROR = "Folder picker could not be opened. Restart Job Tracker."


def _workspace_display_name(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > 100 or any(character in normalized for character in "\r\n"):
        raise ValueError("Workspace name must be a single line of at most 100 characters")
    return normalized


def _run_folder_dialog(window: Any, callback: Callable[[], Any]) -> Any:
    if os.name != "nt":
        return callback()

    native = getattr(window, "native", None)
    if native is None:
        raise RuntimeError(_FOLDER_PICKER_ERROR)

    try:
        if not native.InvokeRequired:
            return callback()
        # pywebview runs JavaScript APIs on MTA threads; WinForms dialogs need STA.
        system = import_module("System")
        return native.Invoke(system.Func[system.Object](callback))
    except Exception as error:
        raise RuntimeError(_FOLDER_PICKER_ERROR) from error


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
    #message { min-height: 1.4rem; color: #9b2531; white-space: pre-wrap; }
    .notice { padding: .9rem; border-radius: 8px; background: #eef3ff; overflow-wrap: anywhere; }
    [hidden] { display: none !important; }
    .muted { color: #68738a; overflow-wrap: anywhere; }
    @media (max-width: 620px) { .grid { grid-template-columns: 1fr; } .folder, .actions { align-items: stretch; flex-direction: column; } }
  </style>
</head>
<body>
<main>
  <h1 id="title">Choose the Job Tracker data folder</h1>
  <p id="intro" class="muted">Databases, uploaded files, profiles, and reports for all workspaces are stored below this folder.</p>
  <p id="migration" class="notice" hidden></p>
  <p id="message" role="alert"></p>
  <form id="setupForm">
    <div class="folder">
      <label>Job Tracker data folder<input id="folder" readonly required></label>
      <button id="browse" type="button" class="secondary" disabled>Choose folder</button>
    </div>
    <p id="folderHint" class="muted">Choose an empty local folder that is not synchronized by OneDrive.</p>
    <label id="workspaceNameField">Workspace name (optional)<input name="workspace_name" maxlength="100" placeholder="For example: Main search"></label>
    <p id="workspaceNameHint" class="muted">This name is shown in Job Tracker. It does not rename the workspace folder.</p>
    <div id="profileFields" class="grid">
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
    <div class="actions">
      <button id="submitButton" type="submit" disabled>Create first workspace</button>
      <button id="cancel" type="button" class="secondary" disabled>Cancel</button>
    </div>
  </form>
</main>
<script>
  let setupState = {};
  let bridgeInitialized = false;
  let bridgeRetryCount = 0;
  let bridgeRetryTimer = null;
  const message = document.querySelector("#message");
  const bridgeError = "Desktop integration unavailable. Restart Job Tracker.";
  const bridgeRetryLimit = 40;
  const bridgeRetryDelay = 100;
  const bridgeMethods = [
    "get_setup_state", "choose_workspace_folder", "configure_data_folder",
    "create_workspace", "recover_data_folder", "cancel_setup",
  ];
  const setMessage = text => { message.textContent = text || ""; };
  const hasDesktopMethod = method =>
    typeof window.pywebview?.api?.[method] === "function";
  const setActionAvailability = () => {
    const canChoose = hasDesktopMethod("choose_workspace_folder");
    document.querySelector("#browse").disabled = !canChoose;
    document.querySelector("#cancel").disabled = !hasDesktopMethod("cancel_setup");
    const action = setupState.mode === "recovery" ? "recover_data_folder"
      : setupState.data_folder ? "create_workspace" : "configure_data_folder";
    document.querySelector("#submitButton").disabled = !hasDesktopMethod(action);
  };
  const desktopCall = async (method, ...args) => {
    const fn = window.pywebview?.api?.[method];
    if (typeof fn !== "function") throw new Error(bridgeError);
    return fn(...args);
  };
  const profile = () => Object.fromEntries(
    ["given_name", "family_name", "email", "phone", "city", "linkedin", "github", "website"]
      .map(name => [name, document.querySelector(`[name="${name}"]`).value])
  );
  const applySetupState = state => {
    setupState = state || {};
    const personal = setupState.personal || {};
    Object.entries(personal).forEach(([name, value]) => {
      const input = document.querySelector(`[name="${name}"]`);
      if (input) input.value = value || "";
    });
    document.querySelector("#folder").value =
      setupState.data_folder || setupState.suggested_data_folder || "";
    if (setupState.mode === "migrate") {
      const source = setupState.legacy_workspace || "the previous data folder";
      document.querySelector("#migration").hidden = false;
      document.querySelector("#migration").textContent =
        `Your existing workspace at ${source} will be copied and verified. The original will be retained for recovery.`;
      if (setupState.same_folder_adoption) {
        document.querySelector("#folderHint").textContent =
          "Use this folder to organize the existing workspace below workspaces/. A verified recovery copy will be retained.";
        document.querySelector("#submitButton").textContent =
          "Organize existing workspace here";
      } else {
        document.querySelector("#submitButton").textContent =
          "Migrate and open workspace";
      }
    } else if (setupState.mode === "recovery") {
      document.querySelector("#title").textContent = "Locate the Job Tracker data folder";
      document.querySelector("#intro").textContent =
        "The configured data folder or workspace catalog is unavailable. Choose an existing managed data folder.";
      document.querySelector("#folderHint").textContent =
        "The selected folder must contain workspaces.yml.";
      document.querySelector("#profileFields").hidden = true;
      document.querySelectorAll("#profileFields input").forEach(input => input.required = false);
      document.querySelector("#workspaceNameField").hidden = true;
      document.querySelector("#workspaceNameHint").hidden = true;
      document.querySelector("#submitButton").textContent = "Use this data folder";
    } else if (setupState.data_folder) {
      document.querySelector("#title").textContent = "Create the first workspace";
      document.querySelector("#intro").textContent =
        `All workspace data will be stored below ${setupState.data_folder}.`;
      document.querySelector("#browse").hidden = true;
    }
    if (setupState.legacy_name) document.querySelector("#legacy").textContent =
      `Previous full name: ${setupState.legacy_name}. Enter given and family names; no split was guessed.`;
    setMessage(setupState.error || "");
  };
  async function initializeDesktopBridge() {
    setActionAvailability();
    const api = window.pywebview?.api;
    if (!api || !bridgeMethods.some(hasDesktopMethod)) {
      return false;
    }
    if (bridgeInitialized) return true;
    bridgeInitialized = true;
    if (bridgeRetryTimer !== null) clearTimeout(bridgeRetryTimer);
    try {
      if (hasDesktopMethod("get_setup_state")) {
        applySetupState(await desktopCall("get_setup_state"));
      } else {
        setMessage(bridgeError);
      }
    } catch (error) {
      setMessage(error.message || bridgeError);
    }
    setActionAvailability();
    return true;
  }
  async function retryDesktopBridge() {
    if (await initializeDesktopBridge()) return;
    bridgeRetryCount += 1;
    if (bridgeRetryCount >= bridgeRetryLimit) {
      setMessage(bridgeError);
      return;
    }
    bridgeRetryTimer = setTimeout(retryDesktopBridge, bridgeRetryDelay);
  }
  window.addEventListener("pywebviewready", initializeDesktopBridge);
  document.querySelector("#browse").addEventListener("click", async () => {
    try {
      const path = await desktopCall("choose_workspace_folder");
      if (path) document.querySelector("#folder").value = path;
    } catch (error) { setMessage(error.message || bridgeError); }
  });
  document.querySelector("#cancel").addEventListener("click", async () => {
    try { await desktopCall("cancel_setup"); }
    catch (error) { setMessage(error.message || bridgeError); }
  });
  document.querySelector("#setupForm").addEventListener("submit", async event => {
    event.preventDefault(); setMessage("");
    try {
      const folder = document.querySelector("#folder").value;
      const workspaceName =
        document.querySelector('[name="workspace_name"]')?.value || "";
      if (setupState.mode === "recovery") await desktopCall("recover_data_folder", folder);
      else if (setupState.data_folder) await desktopCall("create_workspace", profile(), workspaceName);
      else await desktopCall("configure_data_folder", folder, profile(), workspaceName);
    } catch (error) { setMessage(error.message || bridgeError); }
  });
  retryDesktopBridge();
</script>
</body>
</html>
"""


def _initialize_application(
    data_dir: str | Path | None = None,
    *,
    enforce_workspace: bool = False,
    api_token: str | None = None,
) -> Any:
    context = load_project_context(
        data_dir=data_dir,
        enforce_workspace=enforce_workspace,
    )
    service = build_application_service(context)
    service.list_jobs()  # Open/upgrade SQLite before showing the window.
    if not (STATIC_DIR / "index.html").is_file():
        raise RuntimeError("Bundled web UI is missing")
    return create_app(service, api_token=api_token)


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
    api_token: str


def _start_application(data_dir: Path) -> _ServerHandle:
    api_token = secrets.token_urlsafe(32)
    application = _initialize_application(data_dir, enforce_workspace=True, api_token=api_token)
    listener, port = _reserve_loopback_socket()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host=HOST,
            port=port,
            access_log=False,
            log_config=None,
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
    return _ServerHandle(server, thread, errors, port, api_token)


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
        if not MIN_SUPPORTED_SCHEMA_VERSION <= version <= SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported workspace database schema {version}; "
                f"supported range is {MIN_SUPPORTED_SCHEMA_VERSION}-{SCHEMA_VERSION}"
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
        self.root = resolution.workspaces_root
        self.catalog = resolution.catalog
        self.lock = Lock()
        self.override = resolution.status in {"explicit", "environment"}

    def _require_registry_control(self) -> None:
        if self.override:
            raise ValueError(
                "Workspace switching is disabled while JOBTRACKER_DATA_DIR is active"
            )

    def _current_catalog(self) -> WorkspaceCatalog:
        if self.root is None:
            return WorkspaceCatalog()
        loaded = load_workspace_catalog(self.root)
        return loaded or self.catalog or WorkspaceCatalog()

    def _switch(
        self,
        record: WorkspaceRecord,
        path: Path,
        *,
        catalog: WorkspaceCatalog,
    ) -> dict[str, str]:
        self._require_registry_control()
        with self.lock:
            validated = validate_workspace_folder(path, "existing")
            _probe_workspace_database(validated)
            previous_catalog = self._current_catalog()
            new_handle = _start_application(validated)
            try:
                assert self.root is not None
                saved = save_workspace_catalog(
                    self.root,
                    WorkspaceCatalog(
                        active_id=record.id,
                        workspaces=catalog.workspaces,
                    ),
                )
                old_handle = self.handle
                self.handle = new_handle
                self.active = validated
                self.catalog = saved
                if self.window is None:
                    raise RuntimeError("Desktop window is unavailable")
                self.window.load_url(f"http://{HOST}:{new_handle.port}/")
            except Exception:
                _stop_application(new_handle)
                if self.root is not None:
                    save_workspace_catalog(self.root, previous_catalog)
                raise
            _stop_application(old_handle)
            return {
                "active": str(validated),
                "active_id": record.id,
            }

    def _register_created_workspace(
        self,
        root: Path,
        record: WorkspaceRecord,
        path: Path,
        *,
        save_locator: bool,
    ) -> dict[str, str]:
        previous_root = self.root
        previous_catalog = self.catalog
        catalog_file_existed = workspace_catalog_file(root).exists()
        catalog = (
            load_workspace_catalog(root)
            or previous_catalog
            or WorkspaceCatalog()
        )
        updated = WorkspaceCatalog(
            active_id=record.id,
            workspaces=(*catalog.workspaces, record),
        )
        self.root = root
        self.catalog = catalog
        try:
            if save_locator:
                save_workspace_settings(WorkspaceSettings(workspaces_root=root))
            result = self._switch(record, path, catalog=updated)
            return result
        except Exception:
            self.root = previous_root
            self.catalog = previous_catalog
            try:
                if save_locator:
                    workspace_settings_file().unlink(missing_ok=True)
                if catalog_file_existed:
                    save_workspace_catalog(root, catalog)
                else:
                    workspace_catalog_file(root).unlink(missing_ok=True)
            finally:
                discard_managed_workspace(root, path)
            raise

    def _register_adopted_workspace(
        self,
        root: Path,
        record: WorkspaceRecord,
        path: Path,
    ) -> dict[str, str]:
        previous_root = self.root
        previous_catalog = self.catalog
        previous_settings = self.resolution.settings
        with self.lock:
            validated = validate_workspace_folder(path, "existing")
            _probe_workspace_database(validated)
            new_handle = _start_application(validated)
            try:
                preserve_legacy_registry(root)
                saved = save_workspace_catalog(
                    root,
                    WorkspaceCatalog(
                        active_id=record.id,
                        workspaces=(record,),
                    ),
                )
                save_workspace_settings(WorkspaceSettings(workspaces_root=root))
                if self.window is None:
                    raise RuntimeError("Desktop window is unavailable")
                self.window.load_url(f"http://{HOST}:{new_handle.port}/")
            except Exception:
                _stop_application(new_handle)
                restore_legacy_registry(root)
                if previous_settings is None:
                    workspace_settings_file().unlink(missing_ok=True)
                else:
                    save_workspace_settings(previous_settings)
                self.root = previous_root
                self.catalog = previous_catalog
                raise

            old_handle = self.handle
            self.handle = new_handle
            self.active = validated
            self.root = root
            self.catalog = saved
            _stop_application(old_handle)

        warning = ""
        snapshot = ""
        try:
            mark_layout_migration_activated(root)
            recovery_path = finalize_layout_migration(root)
            snapshot = str(recovery_path or "")
        except Exception as exc:
            warning = (
                "Workspace activated, but archiving the original layout is incomplete: "
                f"{exc}. It will be retried at startup."
            )
        return {
            "active": str(validated),
            "active_id": record.id,
            "recovery_snapshot": snapshot,
            "warning": warning,
        }

    def get_setup_state(self) -> dict[str, Any]:
        source = self.resolution.legacy_source
        personal: dict[str, Any] = {}
        if source is not None:
            try:
                loaded = load_profile(source / "profile").get("personal", {})
                if isinstance(loaded, dict):
                    personal = loaded
            except (OSError, UnicodeError, ValueError):
                personal = {}
        return {
            "mode": self.resolution.status,
            "data_folder": str(self.root) if self.root is not None else "",
            "suggested_data_folder": (
                str(source)
                if self.resolution.status == "migrate"
                and source is not None
                and source.resolve() == workspace_settings_file().parent.resolve()
                else ""
            ),
            "same_folder_adoption": (
                self.resolution.status == "migrate"
                and source is not None
                and source.resolve() == workspace_settings_file().parent.resolve()
            ),
            "legacy_workspace": str(source) if source is not None else "",
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
        if self.override:
            return {
                "data_folder": "",
                "active": str(self.active or ""),
                "active_id": "",
                "workspaces": [],
                "override": True,
            }
        catalog = self._current_catalog()
        records: list[dict[str, Any]] = []
        if self.root is None:
            return {
                "data_folder": "",
                "active": "",
                "active_id": "",
                "workspaces": [],
            }
        for record in catalog.workspaces:
            canonical = workspace_path(self.root, record)
            available = canonical.is_dir() and (canonical / "jobtracker.sqlite3").is_file()
            applicant_name = record.name
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
                    "id": record.id,
                    "name": record.name,
                    "folder": record.folder.as_posix(),
                    "path": str(canonical),
                    "applicant_name": applicant_name,
                    "available": available,
                }
            )
        return {
            "data_folder": str(self.root),
            "active": str(self.active.resolve()) if self.active is not None else "",
            "active_id": catalog.active_id or "",
            "workspaces": records,
        }

    def get_storage_status(self) -> dict[str, Any]:
        if self.active is None:
            raise ValueError("No active workspace")
        database = self.active / "jobtracker.sqlite3"
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        try:
            assert_database_healthy(connection)
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
        finally:
            connection.close()
        return {
            "schema_version": schema_version,
            "backup_location": str(self.active / "backups"),
            "migration_backups": [
                backup.public_dict() for backup in list_migration_backups(database)
            ],
            "recovery": (
                "Close Job Tracker before restore. Preserve the failed database, "
                "copy jobtracker.sqlite3 from a verified migration backup into the "
                "workspace, then restart."
            ),
        }

    def create_workspace_export(self) -> dict[str, str]:
        if self.active is None:
            raise ValueError("No active workspace")
        with self.lock:
            export = create_workspace_export(self.active, app_version=__version__)
        return {
            "path": str(export),
            "message": "Verified workspace export created",
        }

    def choose_workspace_folder(self) -> str:
        if self.window is None:
            raise RuntimeError("Desktop window is unavailable")
        selected = _run_folder_dialog(
            self.window,
            lambda: self.window.create_file_dialog(
                self.webview.FileDialog.FOLDER,
                allow_multiple=False,
            ),
        )
        if isinstance(selected, (tuple, list)):
            return str(selected[0]) if selected else ""
        return str(selected) if selected else ""

    def configure_data_folder(
        self,
        path: str,
        profile: dict[str, Any],
        workspace_name: str = "",
    ) -> dict[str, str]:
        self._require_registry_control()
        if self.root is not None:
            raise ValueError("Job Tracker data folder is already configured")
        if not path.strip():
            raise ValueError("Choose a Job Tracker data folder")
        personal = PersonalProfileSchema.model_validate(profile).model_dump()
        display_name = _workspace_display_name(workspace_name)
        folder_name_source = " ".join(
            value.strip()
            for value in (personal["given_name"], personal["family_name"])
            if value.strip()
        )
        legacy_source = self.resolution.legacy_source
        same_folder_adoption = (
            legacy_source is not None
            and Path(path).expanduser().resolve() == legacy_source.resolve()
        )
        if same_folder_adoption:
            root = validate_workspace_folder(path, "existing")
            record, workspace = prepare_same_folder_adoption(
                root,
                folder_name_source,
                display_name,
            )
            existing = load_profile(workspace / "profile")
            save_profile(
                workspace / "profile",
                {
                    "personal": personal,
                    "skills": existing.get("skills", []),
                    "experience": existing.get("experience", ""),
                },
            )
            return self._register_adopted_workspace(root, record, workspace)

        root = validate_workspaces_root(path, "new")
        if legacy_source is not None:
            record, workspace = import_managed_workspace(
                root,
                legacy_source,
                folder_name_source,
                display_name,
            )
            try:
                existing = load_profile(workspace / "profile")
                save_profile(
                    workspace / "profile",
                    {
                        "personal": personal,
                        "skills": existing.get("skills", []),
                        "experience": existing.get("experience", ""),
                    },
                )
            except Exception:
                discard_managed_workspace(root, workspace)
                raise
        else:
            record, workspace = create_managed_workspace(
                root,
                folder_name_source,
                personal,
                display_name,
            )
        return self._register_created_workspace(
            root,
            record,
            workspace,
            save_locator=True,
        )

    def recover_data_folder(self, path: str) -> dict[str, str]:
        self._require_registry_control()
        root = validate_workspaces_root(path, "existing")
        catalog = load_workspace_catalog(root)
        assert catalog is not None
        if catalog.active_id is None:
            raise ValueError("The selected data folder has no active workspace")
        record = next(
            item for item in catalog.workspaces if item.id == catalog.active_id
        )
        workspace = workspace_path(root, record)
        validate_workspace_folder(workspace, "existing")
        _probe_workspace_database(workspace)
        previous_root = self.root
        previous_catalog = self.catalog
        self.root = root
        self.catalog = catalog
        try:
            save_workspace_settings(WorkspaceSettings(workspaces_root=root))
            return self._switch(record, workspace, catalog=catalog)
        except Exception:
            self.root = previous_root
            self.catalog = previous_catalog
            if self.resolution.settings is not None:
                save_workspace_settings(self.resolution.settings)
            else:
                workspace_settings_file().unlink(missing_ok=True)
            raise

    def create_workspace(
        self,
        profile: dict[str, Any],
        workspace_name: str = "",
    ) -> dict[str, str]:
        self._require_registry_control()
        if self.root is None:
            raise ValueError("Choose the Job Tracker data folder first")
        personal = PersonalProfileSchema.model_validate(profile).model_dump()
        display_name = _workspace_display_name(workspace_name)
        folder_name_source = " ".join(
            value.strip()
            for value in (personal["given_name"], personal["family_name"])
            if value.strip()
        )
        record, workspace = create_managed_workspace(
            self.root,
            folder_name_source,
            personal,
            display_name,
        )
        return self._register_created_workspace(
            self.root,
            record,
            workspace,
            save_locator=False,
        )

    def import_workspace(self, path: str) -> dict[str, str]:
        self._require_registry_control()
        if self.root is None:
            raise ValueError("Choose the Job Tracker data folder first")
        source = validate_workspace_folder(path, "existing")
        _probe_workspace_database(source)
        catalog = self._current_catalog()
        registered = next(
            (
                record
                for record in catalog.workspaces
                if workspace_path(self.root, record) == source
            ),
            None,
        )
        if registered is not None:
            return self._switch(registered, source, catalog=catalog)
        complete, _personal = _profile_state(source)
        if not complete:
            raise ValueError(
                "The selected workspace needs a complete applicant profile before import"
            )
        name = profile_display_name(load_profile(source / "profile")) or source.name
        record, workspace = import_managed_workspace(
            self.root,
            source,
            name,
            name,
        )
        return self._register_created_workspace(
            self.root,
            record,
            workspace,
            save_locator=False,
        )

    def activate_workspace(self, workspace_id: str) -> dict[str, Any]:
        self._require_registry_control()
        if self.root is None:
            raise ValueError("Job Tracker data folder is not configured")
        catalog = self._current_catalog()
        selected = next(
            (record for record in catalog.workspaces if record.id == workspace_id),
            None,
        )
        if selected is None:
            raise ValueError("Workspace is not registered")
        return self._switch(
            selected,
            workspace_path(self.root, selected),
            catalog=catalog,
        )

    def forget_workspace(self, workspace_id: str) -> dict[str, Any]:
        self._require_registry_control()
        if self.root is None:
            raise ValueError("Job Tracker data folder is not configured")
        catalog = self._current_catalog()
        if catalog.active_id == workspace_id:
            raise ValueError("Active workspace cannot be forgotten")
        if not any(record.id == workspace_id for record in catalog.workspaces):
            raise ValueError("Workspace is not registered")
        saved = save_workspace_catalog(
            self.root,
            WorkspaceCatalog(
                active_id=catalog.active_id,
                workspaces=tuple(
                    record
                    for record in catalog.workspaces
                    if record.id != workspace_id
                ),
            ),
        )
        self.catalog = saved
        return {
            "active_id": saved.active_id or "",
            "workspaces": [record.id for record in saved.workspaces],
        }

    def rename_workspace(
        self,
        workspace_id: str,
        workspace_name: str,
    ) -> dict[str, str]:
        self._require_registry_control()
        if self.root is None:
            raise ValueError("Job Tracker data folder is not configured")
        catalog = self._current_catalog()
        selected = next(
            (record for record in catalog.workspaces if record.id == workspace_id),
            None,
        )
        if selected is None:
            raise ValueError("Workspace is not registered")
        normalized_name = _workspace_display_name(workspace_name) or selected.folder.name
        updated = tuple(
            WorkspaceRecord(record.id, normalized_name, record.folder)
            if record.id == workspace_id
            else record
            for record in catalog.workspaces
        )
        saved = save_workspace_catalog(
            self.root,
            WorkspaceCatalog(
                active_id=catalog.active_id,
                workspaces=updated,
            ),
        )
        self.catalog = saved
        return {
            "id": selected.id,
            "name": normalized_name,
        }

    def cancel_setup(self) -> None:
        if self.window is None:
            return
        if self.handle is not None and self.active is not None:
            self.window.load_url(f"http://{HOST}:{self.handle.port}/")
            return
        self.window.destroy()

    def stop(self) -> None:
        _stop_application(self.handle)
        self.handle = None


def _expose_workspace_api(window: Any, bridge: WorkspaceBridge) -> None:
    window.expose(*(getattr(bridge, name) for name in WORKSPACE_API_METHODS))


def run_desktop() -> None:
    resolution = resolve_desktop_workspace()
    handle: _ServerHandle | None = None

    if resolution.data_dir is not None and resolution.status in {
        "explicit",
        "environment",
        "active",
    }:
        if resolution.status == "active":
            validated = validate_workspace_folder(resolution.data_dir, "existing")
            _probe_workspace_database(validated)
            complete, _personal = _profile_state(validated)
            if not complete:
                resolution = WorkspaceResolution(
                    "recovery",
                    validated,
                    error=(
                        "Active workspace requires a complete applicant profile: "
                        f"{validated}"
                    ),
                    workspaces_root=resolution.workspaces_root,
                    settings=resolution.settings,
                    catalog=resolution.catalog,
                )
            else:
                handle = _start_application(validated)
        else:
            handle = _start_application(resolution.data_dir)

    if (
        handle is not None
        and resolution.status == "active"
        and resolution.workspaces_root is not None
    ):
        try:
            root = resolution.workspaces_root
            if resolution.settings is None:
                save_workspace_settings(WorkspaceSettings(workspaces_root=root))
            phase = layout_migration_phase(root)
            if phase == "prepared":
                mark_layout_migration_activated(root)
                phase = "activated"
            if phase == "activated":
                finalize_layout_migration(root)
        except Exception as exc:
            if sys.stderr is not None:
                print(
                    f"Job Tracker layout migration recovery remains pending: {exc}",
                    file=sys.stderr,
                )

    try:
        webview = import_module("webview")
    except Exception:
        _stop_application(handle)
        raise
    bridge = WorkspaceBridge(webview, resolution, handle)

    try:
        if handle is not None:
            token = handle.api_token if isinstance(getattr(handle, "api_token", ""), str) else ""
            window = webview.create_window(
                "Job Tracker",
                f"http://{HOST}:{handle.port}/" + (f"?token={token}" if token else ""),
                width=1280,
                height=800,
            )
        else:
            window = webview.create_window(
                "Job Tracker setup",
                html=SETUP_HTML,
                width=900,
                height=760,
            )
        bridge.window = window
        _expose_workspace_api(window, bridge)
        webview.start()
    finally:
        bridge.stop()


def smoke_test() -> None:
    resolution = resolve_desktop_workspace()
    if resolution.data_dir is None:
        raise RuntimeError("No workspace is configured; complete desktop setup first")
    _initialize_application(resolution.data_dir, enforce_workspace=True)


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
