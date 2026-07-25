from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from unittest.mock import Mock, patch

from jobtracker import desktop
from jobtracker.paths import (
    DATA_DIR_ENV,
    WorkspaceRegistry,
    WorkspaceResolution,
    load_workspace_registry,
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
        fake_server: _FakeServer | None = None

        def make_server(config: object) -> _FakeServer:
            nonlocal fake_server
            fake_server = _FakeServer(config)
            return fake_server

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
            log_level="warning",
        )
        fake_webview.create_window.assert_called_once_with(
            "Job Tracker",
            f"http://127.0.0.1:{fake_server.bound_port}/",
            js_api=unittest.mock.ANY,
            width=1280,
            height=800,
        )
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
            js_api=unittest.mock.ANY,
            width=900,
            height=760,
        )
        fake_webview.start.assert_called_once_with()

    def test_incomplete_existing_profile_shows_setup_without_guessing_name(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            desktop._initialize_application(root, enforce_workspace=True)
            profile_dir = root / "profile"
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

            call = fake_webview.create_window.call_args
            bridge = call.kwargs["js_api"]
            self.assertEqual(call.args[0], "Job Tracker setup")
            self.assertEqual(bridge.get_setup_state()["legacy_name"], "Legacy Person")
            self.assertEqual(bridge.get_setup_state()["personal"]["given_name"], "")

    def test_bridge_creates_workspace_then_restart_uses_registered_path(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_path = root / "control" / "workspaces.yml"
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
                    "jobtracker.paths.workspace_registry_file",
                    return_value=registry_path,
                ),
                patch.object(desktop, "_start_application", side_effect=start),
                patch.object(desktop, "_stop_application"),
            ):
                result = bridge.create_workspace(
                    str(workspace),
                    {
                        "given_name": "Jane",
                        "family_name": "Example",
                        "email": "jane@example.com",
                    },
                )
                registry = load_workspace_registry(registry_path)
                resolution = desktop.resolve_desktop_workspace()

            self.assertEqual(result["active"], str(workspace.resolve()))
            self.assertEqual(registry.active, workspace.resolve())
            self.assertEqual(resolution.status, "active")
            self.assertEqual(
                load_profile(workspace / "profile")["personal"]["given_name"],
                "Jane",
            )
            fake_window.load_url.assert_called_once_with("http://127.0.0.1:43210/")

    def test_failed_switch_preserves_old_active_and_forget_never_deletes_data(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old"
            new = root / "new"
            desktop._initialize_application(old, enforce_workspace=True)
            desktop._initialize_application(new, enforce_workspace=True)
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
            registry_path = root / "control" / "workspaces.yml"
            save_workspace_registry(
                WorkspaceRegistry(active=old, workspaces=(old, new)),
                registry_path,
            )
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution(
                    "active",
                    old,
                    WorkspaceRegistry(active=old, workspaces=(old, new)),
                ),
                Mock(),
            )
            bridge.window = Mock()

            with (
                patch(
                    "jobtracker.paths.workspace_registry_file",
                    return_value=registry_path,
                ),
                patch.object(
                    desktop,
                    "_start_application",
                    side_effect=RuntimeError("injected startup failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    bridge.activate_workspace(str(new))
                self.assertEqual(load_workspace_registry(registry_path).active, old.resolve())

                bridge.forget_workspace(str(new))
                remaining = load_workspace_registry(registry_path)

            self.assertNotIn(new.resolve(), remaining.workspaces)
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
