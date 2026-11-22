import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp.main import ROOT, mount_frontend


class FrontendDeploymentTests(unittest.TestCase):
    def test_missing_static_folder_still_serves_the_full_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            application = FastAPI()
            mount_frontend(application, Path(temporary) / "missing-static")
            with TestClient(application) as client:
                for route, name, media in (("/", "index.html", "text/html"),
                                          ("/app.js", "app.js", "application/javascript"),
                                          ("/styles.css", "styles.css", "text/css")):
                    with self.subTest(route=route):
                        response = client.get(route)
                        self.assertEqual(response.status_code, 200)
                        self.assertIn(media, response.headers["content-type"])
                        self.assertEqual(response.text, (ROOT / "static" / name).read_text(encoding="utf-8"))
                self.assertEqual(client.get("/api/nonexistent").status_code, 404)

    def test_partial_upload_uses_one_consistent_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "index.html").write_text("old partial upload", encoding="utf-8")
            application = FastAPI()
            mount_frontend(application, directory)
            with TestClient(application) as client:
                self.assertEqual(client.get("/").text, (ROOT / "static/index.html").read_text(encoding="utf-8"))
                self.assertEqual(client.get("/app.js").status_code, 200)

    def test_matching_static_folder_is_served_directly(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("index.html", "app.js", "styles.css"):
                (directory / name).write_text((ROOT / "static" / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            application = FastAPI()
            mount_frontend(application, directory)
            with TestClient(application) as client:
                self.assertEqual(client.get("/").text, (ROOT / "static/index.html").read_text(encoding="utf-8"))
                self.assertEqual(client.head("/app.js").status_code, 200)

    def test_old_complete_static_folder_cannot_override_current_design(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("index.html", "app.js", "styles.css"):
                (directory / name).write_text("old release: " + name, encoding="utf-8")
            application = FastAPI()
            mount_frontend(application, directory)
            with TestClient(application) as client:
                self.assertEqual(client.get("/").text, (ROOT / "static/index.html").read_text(encoding="utf-8"))
                self.assertEqual(client.get("/styles.css").text, (ROOT / "static/styles.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
