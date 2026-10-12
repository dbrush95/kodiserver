import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("server", Path(__file__).parents[1] / "server/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.mock = patch.object(Path, "home", return_value=self.home)
        self.mock.start()
        self.source = Path(self.tmp.name) / "202610120100"
        self.source.mkdir()
        (self.source / "xbmcbackup.val").write_text("{}")
        (self.source / "userdata").mkdir()
        (self.source / "userdata/settings.xml").write_text("<settings/>")

    def tearDown(self):
        self.mock.stop()
        self.tmp.cleanup()

    def test_directory_import_preserves_original_and_multiple_versions(self):
        one = server.import_backup({"path": str(self.source), "label": "Shield"})
        two = server.import_backup({"path": str(self.source), "label": "Shield newer"})
        self.assertNotEqual(one["id"], two["id"])
        data = server.inventory()
        self.assertEqual(len(data["items"]), 2)
        target = Path(data["items"][0]["path"])
        self.assertEqual((target / "userdata/settings.xml").read_text(), "<settings/>")
        self.assertTrue((self.source / "userdata/settings.xml").exists())
        self.assertEqual(server.root().stat().st_mode & 0o777, 0o700)

    def test_zip_is_copied_without_extracting_paths(self):
        archive = Path(self.tmp.name) / "backup.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("xbmcbackup.val", "{}")
            z.writestr("../../escape", "unsafe")
        result = server.import_backup({"path": str(archive), "label": "ZIP"})
        self.assertTrue(result["archive"])
        self.assertEqual(Path(server.inventory()["items"][0]["path"]).read_bytes(), archive.read_bytes())
        self.assertFalse((server.root() / "escape").exists())

    def test_rejects_non_backup_and_relative_path(self):
        with self.assertRaises(ValueError):
            server.import_backup({"path": "relative", "label": "No"})
        (self.source / "xbmcbackup.val").unlink()
        with self.assertRaises(ValueError):
            server.import_backup({"path": str(self.source), "label": "No"})

    def test_rejects_recursive_import(self):
        with self.assertRaises(ValueError):
            server.import_backup({"path": str(self.home), "label": "No"})

    def test_rejects_links(self):
        (self.source / "linked").symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(ValueError):
            server.import_backup({"path": str(self.source), "label": "No"})

    def test_failed_copy_leaves_no_partial_backup(self):
        with patch.object(server.shutil, "copy2", side_effect=OSError("disk problem")):
            with self.assertRaises(OSError):
                server.import_backup({"path": str(self.source), "label": "No"})
        self.assertEqual(list(server.backup_root().iterdir()), [])

    def test_settings_validate_and_persist(self):
        result = server.save_settings({"plex_url": "http://192.168.1.50:32400/web/"})
        self.assertEqual(result["plex_url"], "http://192.168.1.50:32400")
        self.assertEqual(server.settings(), result)
        for url in ["javascript:alert(1)", "http://user:pass@host", "http://host?X-Plex-Token=secret",
                    "http://host/#secret", "file:///etc/passwd", "http://host:99999", "http://host/other"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                server.normalize_url(url)

    def test_status_returns_real_storage(self):
        self.assertGreater(server.status()["disk_total"], 0)

if __name__ == "__main__":
    unittest.main()
