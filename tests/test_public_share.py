"""公网入口回归：真实转发地址识别和保活/重连分支。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from public_share import EXPECTED, refresh_connection, tunnel_url


class PublicShareTests(unittest.TestCase):
    def test_ignore_management_and_help_urls(self):
        self.assertIsNone(tunnel_url("Visit https://admin.localhost.run/ or https://localhost.run/docs/"))
        self.assertEqual(tunnel_url("demo.app.lhr.life tunneled with tls termination, https://demo.app.lhr.life\n"), "https://demo.app.lhr.life")

    def test_healthy_connection_is_visited_without_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "public-url.txt").write_text("https://demo.app.lhr.life", encoding="utf-8")
            with patch("public_share.health", return_value=EXPECTED) as probe, patch("public_share.publish") as reconnect:
                self.assertEqual(refresh_connection(data), "https://demo.app.lhr.life")
                probe.assert_called_once_with("https://demo.app.lhr.life", timeout=12)
                reconnect.assert_not_called()

    def test_disconnected_tunnel_reconnects_without_another_monitor(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("public_share.publish", return_value="https://new.app.lhr.life") as reconnect:
                self.assertEqual(refresh_connection(Path(folder)), "https://new.app.lhr.life")
                reconnect.assert_called_once_with(open_browser=False, monitor=False)


if __name__ == "__main__":
    unittest.main()
