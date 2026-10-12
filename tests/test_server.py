import importlib.util
import json
import subprocess
import sys
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

    def test_cli_error_is_readable_json(self):
        result = subprocess.run([sys.executable, str(Path(server.__file__)), "unknown"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error"], "Unknown action")


    def test_profile_validation_and_revision(self):
        first = server.save_profile({"weather_location": "Example city", "repo_interval_hours": 12})
        second = server.save_profile({"temperature_unit": "C"})
        self.assertEqual(second["weather_location"], "Example city")
        self.assertNotEqual(first["revision"], second["revision"])
        for args in ({"skin_id": "skin.x);Exec(foo)"}, {"repo_interval_hours": 0}, {"temperature_unit": "X"}):
            with self.assertRaises(ValueError):
                server.save_profile(args)

    def test_capture_filters_secrets_and_packages_repositories(self):
        addondata = self.source / "userdata/addon_data"
        (addondata / "skin.fentastic").mkdir(parents=True)
        (addondata / "skin.fentastic/settings.xml").write_text('<settings><setting id="theme">dark</setting><setting id="api_key">private</setting></settings>')
        repo = self.source / "addons/repository.example"
        repo.mkdir(parents=True)
        (repo / "addon.xml").write_text('<addon id="repository.example" name="Example"><extension point="xbmc.addon.repository"/></addon>')
        meta = server.import_backup({"path": str(self.source), "label": "Source"})
        profile = server.capture_profile({"id": meta["id"]})
        self.assertEqual(profile["skin_settings"], {"theme": "dark"})
        self.assertEqual(profile["repositories"][0]["id"], "repository.example")
        self.assertTrue((server.clients_root() / profile["repositories"][0]["zip"]).exists())

    def test_playlist_classification_does_not_return_credentials(self):
        path = Path(self.tmp.name) / "playlist.m3u8"
        path.write_text("#EXTM3U\n#EXTINF:-1,Direct\nhttps://example.test/a?token=private\n#EXTINF:-1,Kodi\nplugin://addon/play\n#EXTINF:-1,DRM\n#KODIPROP:inputstream=inputstream.adaptive\nhttps://example.test/b\n#EXTINF:-1,Proxy\nhttp://127.0.0.1:9000/play\n")
        data = server.analyze_playlist({"path": str(path)})
        self.assertEqual(data, {"total": 4, "direct_candidates": 1, "kodi_dependent": 3, "other": 0})
        self.assertNotIn("private", json.dumps(data))

    def test_changing_skin_does_not_reuse_previous_skin_settings(self):
        initial = server.client_profile()
        initial["skin_settings"] = {"theme": "dark"}
        server.write_profile(initial)
        self.assertEqual(server.save_profile({"skin_id": "skin.estuary"})["skin_settings"], {})

    def test_status_returns_real_storage(self):
        self.assertGreater(server.status()["disk_total"], 0)

if __name__ == "__main__":
    unittest.main()
