import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import start


class AppOnlyStartupTests(unittest.TestCase):
    def test_default_matches_railway_domain_port(self):
        self.assertEqual(start.server_port({}), 8080)

    def test_railway_port_is_respected(self):
        self.assertEqual(start.server_port({"PORT": "9000"}), 9000)

    def test_invalid_ports_are_rejected(self):
        for port in ("", "abc", "0", "65536", "-1"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                start.server_port({"PORT": port})

    def test_starts_one_web_server_on_public_interface(self):
        run = Mock()
        modules = {"uvicorn": SimpleNamespace(run=run), "dotenv": SimpleNamespace(load_dotenv=Mock())}
        with patch.dict(sys.modules, modules), patch.dict(start.os.environ, {"PORT": "8080"}):
            self.assertEqual(start.main(), 0)
        run.assert_called_once_with("webapp.main:app", host="0.0.0.0", port=8080,
                                    workers=1, app_dir=str(start.ROOT), timeout_graceful_shutdown=10)


if __name__ == "__main__":
    unittest.main()
