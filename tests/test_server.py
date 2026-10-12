import gzip
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

    def test_publish_hosts_direct_streams_and_hides_plugin_urls_from_status(self):
        folder = Path(self.tmp.name) / "merge"
        folder.mkdir()
        (folder / "playlist.m3u8").write_text("#EXTM3U\n#EXTINF:-1,Direct\nhttps://example.test/a?token=private\n#EXTINF:-1,Kodi\nplugin://addon/play\n")
        (folder / "epg.xml").write_text("<tv></tv>")
        result = server.publish_playlist({"path": str(folder / "playlist.m3u8")})
        apple = (server.public_root() / "playlist.m3u8").read_text()
        self.assertIn("https://example.test/a?token=private", apple)
        self.assertNotIn("plugin://", apple)
        self.assertIn('url-tvg="', apple)
        self.assertEqual((server.public_root() / "epg.xml").read_text(), "<tv></tv>")
        self.assertEqual(result["direct_candidates"], 1)
        self.assertNotIn("private", json.dumps(result))
        self.assertTrue(result["apple_playlist"].endswith("/playlist.m3u8"))

    def test_house_list_keeps_ohio_news_and_leaves_tokens_out_of_status(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        files = {}
        for source in gather.SOURCES:
            files[source["m3u"]] = "#EXTM3U\n"
        ohio = next(source for source in gather.SOURCES if source["name"] == "Ohio news")
        pluto = next(source for source in gather.SOURCES if source["name"] == "Pluto TV")
        files[ohio["m3u"]] = "#EXTM3U\n#EXTINF:-1 tvg-id=\"CBS.us@WHIOTV\" group-title=\"General\",CBS WHIO-TV (1080p)\nhttps://cdn.example.test/whio/playlist.m3u8\n"
        files[ohio["epg"]] = "<tv><channel id=\"WHIO Dayton\"><display-name>WHIO Dayton News</display-name></channel><channel id=\"Other\"><display-name>A and E</display-name></channel><programme channel=\"WHIO Dayton\" start=\"20261012000000 +0000\" stop=\"20261012010000 +0000\"><title>News</title></programme><programme channel=\"Other\" start=\"20261012000000 +0000\" stop=\"20261012010000 +0000\"><title>Secret</title></programme></tv>"
        files[pluto["m3u"]] = "#EXTM3U\n#EXTINF:-1 tvg-id=\"news1\",CBS News 24/7\nhttps://example.test/news\n#EXTINF:-1 tvg-id=\"movie1\",Pluto Movie\nhttps://example.test/pluto-movie?token=private\n#EXTINF:-1,Nope\nplugin://addon/play\n"
        files[pluto["epg"]] = "<tv><channel id=\"news1\"><display-name>CBS News 24/7</display-name></channel><programme channel=\"news1\" start=\"20261012000000 +0000\" stop=\"20261012010000 +0000\"><title>Bulletin</title></programme></tv>"
        def fetch(url, limit):
            if url not in files:
                raise ValueError("missing")
            return files[url].encode()
        merge = "#EXTM3U\n#EXTINF:-1 tvg-id=\"philo\",Philo\nplugin://slyguy.philo/play\n#EXTINF:-1,Direct merge\nhttps://example.test/merge\n#EXTINF:-1 tvg-id=\"localnews\",Local News\nhttps://example.test/merge-news\n"
        guide_path = Path(self.tmp.name) / "merge-epg.xml"
        guide_path.write_bytes(gzip.compress(
            b"<tv><channel id=\"localnews\"><display-name>Local News</display-name></channel>"
            b"<programme channel=\"localnews\" start=\"20261012000000 +0000\" stop=\"20261012010000 +0000\"><title>Nightly</title></programme>"
            b"<channel id=\"philo\"><display-name>Philo</display-name></channel>"
            b"<programme channel=\"philo\" start=\"20261012000000 +0000\" stop=\"20261012010000 +0000\"><title>Secret Show</title></programme></tv>"))
        result = gather.build_house(merge, guide_path, "http://192.168.1.119:8877", fetch)
        self.assertIn("WHIO", result["tvh"])
        self.assertIn("CBS News", result["tvh"])
        self.assertIn("https://example.test/merge-news", result["tvh"])
        self.assertNotIn("https://example.test/merge\n", result["tvh"])
        self.assertNotIn("pluto-movie", result["tvh"])
        self.assertIn("https://example.test/merge\n", result["playlist"])
        self.assertIn("pluto-movie?token=private", result["playlist"])
        self.assertNotIn("plugin://", result["playlist"])
        self.assertIn("plugin://slyguy.philo/play", result["kodi"])
        self.assertNotIn("private", json.dumps(result["sources"]))
        guide = result["epg"].decode()
        self.assertIn("WHIO Dayton", guide)
        self.assertIn("Bulletin", guide)
        self.assertIn("Nightly", guide)
        self.assertNotIn("Secret", guide)
        self.assertNotIn("Secret Show", guide)
        self.assertEqual(server.inventory()["attached"], [])

    def test_changing_skin_does_not_reuse_previous_skin_settings(self):
        initial = server.client_profile()
        initial["skin_settings"] = {"theme": "dark"}
        server.write_profile(initial)
        self.assertEqual(server.save_profile({"skin_id": "skin.estuary"})["skin_settings"], {})

    def test_status_returns_real_storage(self):
        self.assertGreater(server.status()["disk_total"], 0)

    def test_file_manager_stays_inside_allowed_folders(self):
        folder = self.home / "clips"
        folder.mkdir()
        (folder / "note.txt").write_text("hello")
        listed = server.list_files({"path": str(self.home)})
        self.assertIn("clips", [item["name"] for item in listed["entries"]])
        self.assertEqual(server.read_file_preview({"path": str(folder), "name": "note.txt"})["text"], "hello")
        created = server.make_directory({"path": str(folder), "name": "extra"})
        self.assertIn("extra", [item["name"] for item in created["entries"]])
        renamed = server.rename_path({"path": str(folder), "name": "note.txt", "to": "done.txt"})
        self.assertIn("done.txt", [item["name"] for item in renamed["entries"]])
        uploaded = server.write_uploaded_file({"path": str(folder), "name": "clip.txt"}, b"clip")
        self.assertIn("clip.txt", [item["name"] for item in uploaded["entries"]])
        removed = server.delete_path({"path": str(folder), "name": "done.txt"})
        self.assertNotIn("done.txt", [item["name"] for item in removed["entries"]])
        outside = Path("C:/Windows") if sys.platform.startswith("win") else Path("/etc")
        with self.assertRaises(ValueError):
            server.list_files({"path": str(outside)})
        with self.assertRaises(ValueError):
            server.delete_path({"path": str(self.home), "name": "."})
        with self.assertRaises(ValueError):
            server.delete_path({"path": str(server.root().parent), "name": server.root().name})
        secret = server.root() / "clients"
        secret.mkdir(exist_ok=True)
        (secret / "library.xml").write_text("<settings/>")
        with self.assertRaises(ValueError):
            server.read_file_preview({"path": str(secret), "name": "library.xml"})
        with self.assertRaises(ValueError):
            server.write_uploaded_file({"path": str(folder), "name": "big.bin"}, b"x" * (8 * 1024 * 1024 + 1))

if __name__ == "__main__":
    unittest.main()
