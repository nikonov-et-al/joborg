from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jobtracker import desktop
from jobtracker.paths import (
    DATA_DIR_ENV,
    WorkspaceCatalog,
    WorkspaceRecord,
    WorkspaceRegistry,
    WorkspaceResolution,
    WorkspaceSettings,
    load_workspace_catalog,
    load_workspace_registry,
    load_workspace_settings,
    save_workspace_catalog,
    save_workspace_registry,
)
from jobtracker.services import load_profile, save_profile


class _FakeServer:
    def __init__(self, config: object, *, fail: bool = False) -> None:
        self.config = config
        self.fail = fail
        self.started = False
        self.should_exit = False
        self.force_exit = False
        self.bound_host = ""
        self.bound_port = 0

    def run(self, sockets: list[object]) -> None:
        listener = sockets[0]
        self.bound_host, self.bound_port = listener.getsockname()
        if self.fail:
            raise OSError("injected server failure")
        self.started = True
        while not self.should_exit and not self.force_exit:
            sleep(0.001)


class _FakeFunc:
    def __class_getitem__(cls, item: object):
        return lambda callback: callback


class DesktopTests(unittest.TestCase):
    def test_loopback_socket_uses_os_assigned_port(self) -> None:
        listener, port = desktop._reserve_loopback_socket()
        try:
            self.assertEqual(listener.getsockname()[0], "127.0.0.1")
            self.assertGreater(port, 0)
        finally:
            listener.close()

    def test_window_close_stops_loopback_server(self) -> None:
        fake_webview = Mock()
        fake_window = Mock()
        events: list[str] = []
        fake_server: _FakeServer | None = None

        def make_server(config: object) -> _FakeServer:
            nonlocal fake_server
            fake_server = _FakeServer(config)
            return fake_server

        def create_window(*args: object, **kwargs: object) -> Mock:
            events.append("create")
            return fake_window

        fake_webview.create_window.side_effect = create_window
        fake_window.expose.side_effect = lambda *functions: events.append("expose")
        fake_webview.start.side_effect = lambda: events.append("start")

        with (
            patch.object(
                desktop,
                "resolve_desktop_workspace",
                return_value=WorkspaceResolution("explicit", Path("C:/test-workspace")),
            ),
            patch.object(desktop, "_initialize_application", return_value=object()),
            patch.object(desktop, "import_module", return_value=fake_webview),
            patch.object(desktop.uvicorn, "Config", return_value=object()) as config,
            patch.object(desktop.uvicorn, "Server", side_effect=make_server),
        ):
            desktop.run_desktop()

        self.assertIsNotNone(fake_server)
        assert fake_server is not None
        self.assertEqual(fake_server.bound_host, "127.0.0.1")
        self.assertGreater(fake_server.bound_port, 0)
        self.assertTrue(fake_server.should_exit)
        config.assert_called_once_with(
            unittest.mock.ANY,
            host="127.0.0.1",
            port=fake_server.bound_port,
            access_log=False,
            log_config=None,
            log_level="warning",
        )
        window_call = fake_webview.create_window.call_args
        self.assertEqual(window_call.args[0], "Job Tracker")
        self.assertRegex(
            window_call.args[1],
            rf"^http://127\.0\.0\.1:{fake_server.bound_port}/\?token=[A-Za-z0-9_-]+$",
        )
        self.assertEqual(window_call.kwargs, {"width": 1280, "height": 800})
        self.assertEqual(
            tuple(function.__name__ for function in fake_window.expose.call_args.args),
            desktop.WORKSPACE_API_METHODS,
        )
        self.assertEqual(events, ["create", "expose", "start"])
        fake_webview.start.assert_called_once_with()

    def test_server_start_failure_is_visible_to_caller(self) -> None:
        fake_webview = Mock()
        fake_server = _FakeServer(object(), fail=True)
        with (
            patch.object(
                desktop,
                "resolve_desktop_workspace",
                return_value=WorkspaceResolution("explicit", Path("C:/test-workspace")),
            ),
            patch.object(desktop, "_initialize_application", return_value=object()),
            patch.object(desktop, "import_module", return_value=fake_webview),
            patch.object(desktop.uvicorn, "Config", return_value=object()),
            patch.object(desktop.uvicorn, "Server", return_value=fake_server),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed to start"):
                desktop.run_desktop()
        fake_webview.start.assert_not_called()
        self.assertTrue(fake_server.should_exit)

    def test_window_creation_failure_stops_started_server(self) -> None:
        handle = Mock()
        fake_webview = Mock()
        fake_webview.create_window.side_effect = RuntimeError("window failed")
        with (
            patch.object(
                desktop,
                "resolve_desktop_workspace",
                return_value=WorkspaceResolution("explicit", Path("C:/test-workspace")),
            ),
            patch.object(desktop, "_start_application", return_value=handle),
            patch.object(desktop, "_stop_application") as stop,
            patch.object(desktop, "import_module", return_value=fake_webview),
        ):
            with self.assertRaisesRegex(RuntimeError, "window failed"):
                desktop.run_desktop()

        stop.assert_called_once_with(handle)

    def test_first_run_shows_setup_without_initializing_storage(self) -> None:
        fake_webview = Mock()
        fake_window = Mock()
        fake_webview.create_window.return_value = fake_window
        with (
            patch.object(
                desktop,
                "resolve_desktop_workspace",
                return_value=WorkspaceResolution("setup", None),
            ),
            patch.object(desktop, "_initialize_application") as initialize,
            patch.object(desktop, "import_module", return_value=fake_webview),
        ):
            desktop.run_desktop()

        initialize.assert_not_called()
        fake_webview.create_window.assert_called_once_with(
            "Job Tracker setup",
            html=desktop.SETUP_HTML,
            width=900,
            height=760,
        )
        self.assertEqual(
            tuple(function.__name__ for function in fake_window.expose.call_args.args),
            desktop.WORKSPACE_API_METHODS,
        )
        fake_webview.start.assert_called_once_with()

    def test_incomplete_existing_profile_shows_setup_without_guessing_name(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            desktop._initialize_application(root, enforce_workspace=True)
            profile_dir = root / "profile"
            profile_dir.mkdir()
            (profile_dir / "personal_info.yml").write_text(
                "name: Legacy Person\nemail: legacy@example.com\n",
                encoding="utf-8",
            )
            fake_webview = Mock()
            fake_window = Mock()
            fake_webview.create_window.return_value = fake_window
            with (
                patch.object(
                    desktop,
                    "resolve_desktop_workspace",
                    return_value=WorkspaceResolution("active", root),
                ),
                patch.object(desktop, "import_module", return_value=fake_webview),
            ):
                desktop.run_desktop()

            window_call = fake_webview.create_window.call_args
            bridge = fake_window.expose.call_args.args[0].__self__
            self.assertEqual(window_call.args[0], "Job Tracker setup")
            self.assertEqual(bridge.get_setup_state()["mode"], "recovery")
            self.assertIn("complete applicant profile", bridge.get_setup_state()["error"])

    def test_real_window_exposes_only_workspace_allowlist(self) -> None:
        from webview.window import Window

        window = Window("test-window", "Test", None)
        bridge = desktop.WorkspaceBridge(
            Mock(),
            WorkspaceResolution("setup", None),
        )

        desktop._expose_workspace_api(window, bridge)

        self.assertEqual(tuple(window._functions), desktop.WORKSPACE_API_METHODS)
        self.assertNotIn("stop", window._functions)
        self.assertNotIn("set_pending", window._functions)
        for function in window._functions.values():
            self.assertIs(function.__self__, bridge)

    def test_open_reports_folder_opens_active_workspace_reports_directory(self) -> None:
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution("active", workspace),
                handle=Mock(),
            )
            reports_dir = workspace / "reports"
            with patch.object(desktop.os, "startfile", create=True) as open_folder:
                result = bridge.open_reports_folder()

            self.assertEqual(result, str(reports_dir))
            self.assertTrue(reports_dir.is_dir())
            open_folder.assert_called_once_with(str(reports_dir))

    def test_setup_bridge_runtime_behavior(self) -> None:
        node = shutil.which("node")
        if node is None and os.name == "nt":
            installed = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs/node.exe"
            node = str(installed) if installed.is_file() else None
        if node is None:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run(
            [node, str(Path(__file__).with_name("setup_bridge_test.js"))],
            input=desktop.SETUP_HTML,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_folder_dialog_returns_selection_or_empty_without_writing(self) -> None:
        fake_webview = Mock()
        fake_webview.FileDialog.FOLDER = 20
        window = Mock()
        window.native.InvokeRequired = True
        window.native.Invoke.side_effect = lambda callback: callback()
        bridge = desktop.WorkspaceBridge(
            fake_webview,
            WorkspaceResolution("setup", None),
        )
        bridge.window = window

        with TemporaryDirectory() as temp:
            root = Path(temp)
            selected = root / "selected"
            selected.mkdir()
            window.create_file_dialog.side_effect = [
                (str(selected),),
                [str(selected)],
                str(selected),
                None,
            ]

            with patch.object(
                desktop,
                "import_module",
                return_value=SimpleNamespace(Func=_FakeFunc, Object=object),
            ):
                self.assertEqual(bridge.choose_workspace_folder(), str(selected))
                self.assertEqual(bridge.choose_workspace_folder(), str(selected))
                self.assertEqual(bridge.choose_workspace_folder(), str(selected))
                self.assertEqual(bridge.choose_workspace_folder(), "")
            self.assertEqual(list(selected.iterdir()), [])

        self.assertEqual(window.native.Invoke.call_count, 4)
        self.assertEqual(window.create_file_dialog.call_count, 4)
        for call in window.create_file_dialog.call_args_list:
            self.assertEqual(call.args, (fake_webview.FileDialog.FOLDER,))
            self.assertEqual(call.kwargs, {"allow_multiple": False})

    def test_folder_dialog_runs_directly_on_ui_thread(self) -> None:
        window = Mock()
        window.native.InvokeRequired = False
        window.create_file_dialog.return_value = ("C:/workspace",)
        bridge = desktop.WorkspaceBridge(
            Mock(FileDialog=SimpleNamespace(FOLDER=20)),
            WorkspaceResolution("setup", None),
        )
        bridge.window = window

        with patch.object(desktop, "import_module") as import_system:
            self.assertEqual(bridge.choose_workspace_folder(), "C:/workspace")

        window.native.Invoke.assert_not_called()
        import_system.assert_not_called()

    def test_folder_dialog_runs_directly_outside_windows(self) -> None:
        window = Mock()
        window.create_file_dialog.return_value = ("C:/workspace",)
        bridge = desktop.WorkspaceBridge(
            Mock(FileDialog=SimpleNamespace(FOLDER=20)),
            WorkspaceResolution("setup", None),
        )
        bridge.window = window

        with (
            patch.object(desktop.os, "name", "posix"),
            patch.object(desktop, "import_module") as import_system,
        ):
            self.assertEqual(bridge.choose_workspace_folder(), "C:/workspace")

        window.native.Invoke.assert_not_called()
        import_system.assert_not_called()

    def test_folder_dialog_missing_native_window_is_actionable(self) -> None:
        window = Mock(native=None)
        bridge = desktop.WorkspaceBridge(
            Mock(FileDialog=SimpleNamespace(FOLDER=20)),
            WorkspaceResolution("setup", None),
        )
        bridge.window = window

        with self.assertRaisesRegex(RuntimeError, desktop._FOLDER_PICKER_ERROR):
            bridge.choose_workspace_folder()

        window.create_file_dialog.assert_not_called()

    def test_folder_dialog_invocation_failure_is_actionable(self) -> None:
        window = Mock()
        window.native.InvokeRequired = True
        window.native.Invoke.side_effect = OSError("injected invoke failure")
        bridge = desktop.WorkspaceBridge(
            Mock(FileDialog=SimpleNamespace(FOLDER=20)),
            WorkspaceResolution("setup", None),
        )
        bridge.window = window

        with (
            patch.object(
                desktop,
                "import_module",
                return_value=SimpleNamespace(Func=_FakeFunc, Object=object),
            ),
            self.assertRaisesRegex(RuntimeError, desktop._FOLDER_PICKER_ERROR),
        ):
            bridge.choose_workspace_folder()

        window.create_file_dialog.assert_not_called()

    def test_bridge_creates_workspace_then_restart_uses_registered_path(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            data_root.mkdir()
            settings_path = root / "control" / "settings.yml"
            fake_webview = Mock()
            fake_webview.FileDialog.FOLDER = 20
            fake_window = Mock()
            handle = Mock()
            handle.port = 43210
            bridge = desktop.WorkspaceBridge(
                fake_webview,
                WorkspaceResolution("setup", None),
            )
            bridge.window = fake_window

            def start(path: Path):
                desktop._initialize_application(path, enforce_workspace=True)
                return handle

            with (
                patch(
                    "jobtracker.paths.workspace_settings_file",
                    return_value=settings_path,
                ),
                patch.object(desktop, "_start_application", side_effect=start),
                patch.object(desktop, "_stop_application"),
            ):
                result = bridge.configure_data_folder(
                    str(data_root),
                    {
                        "given_name": "Jane",
                        "family_name": "Example",
                        "email": "jane@example.com",
                    },
                )
                catalog = load_workspace_catalog(data_root)
                resolution = desktop.resolve_desktop_workspace()

            assert catalog is not None
            workspace = data_root / catalog.workspaces[0].folder
            self.assertEqual(result["active"], str(workspace.resolve()))
            self.assertEqual(catalog.active_id, result["active_id"])
            self.assertEqual(resolution.status, "active")
            self.assertEqual(
                load_profile(workspace / "profile")["personal"]["given_name"],
                "Jane",
            )
            fake_window.load_url.assert_called_once_with("http://127.0.0.1:43210/")

    def test_legacy_workspace_is_copied_verified_and_source_is_retained(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "legacy"
            data_root = root / "managed"
            data_root.mkdir()
            desktop._initialize_application(source, enforce_workspace=True)
            save_profile(
                source / "profile",
                {
                    "personal": {
                        "given_name": "Legacy",
                        "family_name": "Person",
                        "email": "legacy@example.com",
                    },
                    "skills": ["Python"],
                    "experience": "Experience",
                },
            )
            sentinel = source / "keep.txt"
            sentinel.write_text("source remains", encoding="utf-8")
            settings_path = root / "control" / "settings.yml"
            handle = Mock(port=43210)
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution(
                    "migrate",
                    None,
                    legacy_source=source,
                ),
            )
            bridge.window = Mock()

            with (
                patch(
                    "jobtracker.paths.workspace_settings_file",
                    return_value=settings_path,
                ),
                patch.object(desktop, "_start_application", return_value=handle),
                patch.object(desktop, "_stop_application"),
            ):
                result = bridge.configure_data_folder(
                    str(data_root),
                    {
                        "given_name": "Legacy",
                        "family_name": "Person",
                        "email": "legacy@example.com",
                    },
                )

            catalog = load_workspace_catalog(data_root)
            settings = load_workspace_settings(settings_path)
            assert catalog is not None
            assert settings is not None
            imported = data_root / catalog.workspaces[0].folder
            self.assertEqual(result["active"], str(imported.resolve()))
            self.assertTrue((imported / "jobtracker.sqlite3").is_file())
            self.assertEqual(
                (imported / "keep.txt").read_text(encoding="utf-8"),
                "source remains",
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "source remains")
            self.assertEqual(settings.workspaces_root, data_root.resolve())

    def test_same_folder_legacy_workspace_is_adopted_and_archived_without_deletion(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "jobtracker"
            desktop._initialize_application(root, enforce_workspace=True)
            save_profile(
                root / "profile",
                {
                    "personal": {
                        "given_name": "Legacy",
                        "family_name": "Person",
                        "email": "legacy@example.com",
                    },
                    "skills": ["Python"],
                    "experience": "Experience",
                },
            )
            (root / "jobs").mkdir()
            sentinel = root / "keep.txt"
            sentinel.write_text("source remains recoverable", encoding="utf-8")
            old_backup = root / "backups" / "legacy-migration-existing"
            old_backup.mkdir(parents=True)
            (old_backup / "manifest.yml").write_text("kept", encoding="utf-8")
            save_workspace_registry(
                WorkspaceRegistry(active=root, workspaces=(root,)),
                root / "workspaces.yml",
            )
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution(
                    "migrate",
                    None,
                    legacy_source=root,
                ),
            )
            bridge.window = Mock()
            handle = Mock(port=43210)

            with (
                patch("jobtracker.paths.default_data_dir", return_value=root),
                patch.object(desktop, "_start_application", return_value=handle),
                patch.object(desktop, "_stop_application"),
            ):
                result = bridge.configure_data_folder(
                    str(root),
                    {
                        "given_name": "Legacy",
                        "family_name": "Person",
                        "email": "legacy@example.com",
                    },
                    "Germany search",
                )

            catalog = load_workspace_catalog(root)
            assert catalog is not None
            workspace = root / catalog.workspaces[0].folder
            snapshots = list((root / "backups").glob("layout-migration-*"))
            self.assertEqual(result["active"], str(workspace.resolve()))
            self.assertEqual(result["warning"], "")
            self.assertEqual(catalog.workspaces[0].name, "Germany search")
            self.assertRegex(workspace.name, r"^legacy-person--[0-9a-f]{8}$")
            self.assertEqual(
                (workspace / "keep.txt").read_text(encoding="utf-8"),
                "source remains recoverable",
            )
            self.assertFalse((root / "jobtracker.sqlite3").exists())
            self.assertFalse(sentinel.exists())
            self.assertTrue((root / "workspaces.v1.yml").is_file())
            self.assertEqual(
                (old_backup / "manifest.yml").read_text(encoding="utf-8"),
                "kept",
            )
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(
                (snapshots[0] / "keep.txt").read_text(encoding="utf-8"),
                "source remains recoverable",
            )
            self.assertTrue((snapshots[0] / "jobtracker.sqlite3").is_file())
            self.assertTrue((snapshots[0] / "jobs").is_dir())
            self.assertTrue((snapshots[0] / "layout-migration.yml").is_file())

    def test_workspace_name_is_optional_and_can_be_renamed_without_moving_folder(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspaces" / "jane-example--12345678"
            desktop._initialize_application(workspace, enforce_workspace=True)
            record = WorkspaceRecord(
                "12345678123456781234567812345678",
                workspace.name,
                workspace.relative_to(root),
            )
            catalog = save_workspace_catalog(
                root,
                WorkspaceCatalog(active_id=record.id, workspaces=(record,)),
            )
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution(
                    "active",
                    workspace,
                    workspaces_root=root,
                    catalog=catalog,
                ),
            )

            renamed = bridge.rename_workspace(record.id, "Main search")
            after_rename = load_workspace_catalog(root)
            reset = bridge.rename_workspace(record.id, "  ")
            after_reset = load_workspace_catalog(root)

            assert after_rename is not None
            assert after_reset is not None
            self.assertEqual(renamed["name"], "Main search")
            self.assertEqual(after_rename.workspaces[0].name, "Main search")
            self.assertEqual(reset["name"], workspace.name)
            self.assertEqual(after_reset.workspaces[0].name, workspace.name)
            self.assertTrue(workspace.is_dir())

    def test_same_folder_locator_failure_restores_legacy_registry_and_direct_data(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "jobtracker"
            desktop._initialize_application(root, enforce_workspace=True)
            save_profile(
                root / "profile",
                {
                    "personal": {
                        "given_name": "Legacy",
                        "family_name": "Person",
                        "email": "legacy@example.com",
                    },
                    "skills": [],
                    "experience": "",
                },
            )
            save_workspace_registry(
                WorkspaceRegistry(active=root, workspaces=(root,)),
                root / "workspaces.yml",
            )
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution("migrate", None, legacy_source=root),
            )
            bridge.window = Mock()

            with (
                patch("jobtracker.paths.default_data_dir", return_value=root),
                patch.object(
                    desktop,
                    "save_workspace_settings",
                    side_effect=OSError("injected settings failure"),
                ),
                patch.object(
                    desktop,
                    "_start_application",
                    return_value=Mock(port=43210),
                ),
                patch.object(desktop, "_stop_application"),
                self.assertRaisesRegex(OSError, "injected settings failure"),
            ):
                bridge.configure_data_folder(
                    str(root),
                    {
                        "given_name": "Legacy",
                        "family_name": "Person",
                        "email": "legacy@example.com",
                    },
                )

            registry = load_workspace_registry(root / "workspaces.yml")
            assert registry is not None
            self.assertEqual(registry.active, root.resolve())
            self.assertTrue((root / "jobtracker.sqlite3").is_file())
            self.assertTrue((root / "profile").is_dir())
            self.assertFalse((root / "settings.yml").exists())
            self.assertTrue((root / ".layout-migration.yml").is_file())
            self.assertTrue(any((root / "workspaces").iterdir()))

    def test_locator_failure_does_not_register_partial_workspace(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "managed"
            data_root.mkdir()
            settings_path = root / "control" / "settings.yml"
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution("setup", None),
            )
            bridge.window = Mock()

            with (
                patch.object(
                    desktop,
                    "save_workspace_settings",
                    side_effect=OSError("injected settings failure"),
                ),
                patch.object(
                    desktop,
                    "workspace_settings_file",
                    return_value=settings_path,
                ),
                self.assertRaisesRegex(OSError, "injected settings failure"),
            ):
                bridge.configure_data_folder(
                    str(data_root),
                    {
                        "given_name": "Jane",
                        "family_name": "Example",
                        "email": "jane@example.com",
                    },
                )

            self.assertFalse(settings_path.exists())
            self.assertFalse((data_root / "workspaces.yml").exists())
            self.assertEqual(list((data_root / "workspaces").iterdir()), [])

    def test_failed_switch_preserves_old_active_and_forget_never_deletes_data(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            old = data_root / "workspaces" / "old--11111111"
            new = data_root / "workspaces" / "new--22222222"
            desktop._initialize_application(old, enforce_workspace=True)
            desktop._initialize_application(new, enforce_workspace=True)
            save_profile(
                old / "profile",
                {
                    "personal": {
                        "given_name": "Old",
                        "family_name": "Person",
                        "email": "old@example.com",
                    },
                    "skills": [],
                    "experience": "",
                },
            )
            save_profile(
                new / "profile",
                {
                    "personal": {
                        "given_name": "New",
                        "family_name": "Person",
                        "email": "new@example.com",
                    },
                    "skills": [],
                    "experience": "",
                },
            )
            sentinel = new / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            old_record = WorkspaceRecord(
                "11111111111111111111111111111111",
                "Old Person",
                old.relative_to(data_root),
            )
            new_record = WorkspaceRecord(
                "22222222222222222222222222222222",
                "New Person",
                new.relative_to(data_root),
            )
            catalog = save_workspace_catalog(
                data_root,
                WorkspaceCatalog(
                    active_id=old_record.id,
                    workspaces=(old_record, new_record),
                ),
            )
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution(
                    "active",
                    old,
                    workspaces_root=data_root,
                    settings=WorkspaceSettings(workspaces_root=data_root),
                    catalog=catalog,
                ),
                Mock(),
            )
            bridge.window = Mock()

            with patch.object(
                desktop,
                "_start_application",
                side_effect=RuntimeError("injected startup failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    bridge.activate_workspace(new_record.id)
                self.assertEqual(
                    load_workspace_catalog(data_root).active_id,
                    old_record.id,
                )

            bridge.forget_workspace(new_record.id)
            remaining = load_workspace_catalog(data_root)

            assert remaining is not None
            self.assertNotIn(
                new_record.id,
                [record.id for record in remaining.workspaces],
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_smoke_test_initializes_external_data_and_preserves_it(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {DATA_DIR_ENV: str(root)}):
                desktop.smoke_test()
                sentinel = root / "keep.txt"
                sentinel.write_text("user data", encoding="utf-8")
                desktop.smoke_test()

            self.assertTrue((root / "jobtracker.sqlite3").is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data")

    def test_main_reports_startup_failure(self) -> None:
        error = RuntimeError("broken schema")
        with (
            patch.object(desktop, "run_desktop", side_effect=error),
            patch.object(desktop, "_show_startup_error") as show_error,
        ):
            self.assertEqual(desktop.main([]), 1)
        show_error.assert_called_once_with(error)


if __name__ == "__main__":
    unittest.main()
