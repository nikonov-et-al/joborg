from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from jobtracker import cli, desktop


class _StoppedThread:
    def is_alive(self) -> bool:
        return False


class CliWebSecurityTests(unittest.TestCase):
    def test_rejects_non_loopback_and_reload(self) -> None:
        runner = CliRunner()
        remote = runner.invoke(cli.app, ["web", "--host", "0.0.0.0"])
        reload = runner.invoke(cli.app, ["web", "--reload"])
        self.assertNotEqual(remote.exit_code, 0)
        self.assertIn("Only 127.0.0.1", remote.output)
        self.assertNotEqual(reload.exit_code, 0)
        self.assertIn("cannot preserve", reload.output)

    def test_opens_tokenized_page_without_printing_token(self) -> None:
        handle = SimpleNamespace(
            api_token="process-secret",
            port=8123,
            thread=_StoppedThread(),
            errors=[],
        )
        with (
            patch.object(desktop, "_start_application", return_value=handle) as start,
            patch.object(desktop, "_stop_application") as stop,
            patch.object(cli.webbrowser, "open", return_value=True) as browser,
        ):
            result = CliRunner().invoke(cli.app, ["web", "--port", "8123"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("process-secret", result.output)
        browser.assert_called_once_with(
            "http://127.0.0.1:8123/?token=process-secret"
        )
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["port"], 8123)
        self.assertFalse(start.call_args.kwargs["diagnostic"])
        stop.assert_called_once_with(handle)

    def test_diagnostic_mode_uses_disposable_data_and_docs(self) -> None:
        handle = SimpleNamespace(
            api_token="process-secret",
            port=8124,
            thread=_StoppedThread(),
            errors=[],
        )
        with (
            patch.object(desktop, "_start_application", return_value=handle) as start,
            patch.object(desktop, "_stop_application"),
            patch.object(cli.webbrowser, "open", return_value=True) as browser,
        ):
            result = CliRunner().invoke(
                cli.app,
                ["web", "--port", "8124", "--diagnostic"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(start.call_args.kwargs["diagnostic"])
        browser.assert_called_once_with("http://127.0.0.1:8124/docs")


if __name__ == "__main__":
    unittest.main()
