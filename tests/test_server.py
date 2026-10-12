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
        self.assertNotIn("plugin://", result["kodi"])
        self.assertIn("WHIO", result["kodi"])
        self.assertNotIn("pluto-movie", result["kodi"])
        self.assertIn('url-tvg="http://192.168.1.119:8877/kodi.xml"', result["kodi"])
        self.assertIn("WHIO Dayton", result["kodi_epg"].decode())
        self.assertNotIn("Bulletin", result["kodi_epg"].decode())
        self.assertNotIn("private", json.dumps(result["sources"]))
        guide = result["epg"].decode()
        self.assertIn("WHIO Dayton", guide)
        self.assertIn("Bulletin", guide)
        self.assertIn("Nightly", guide)
        self.assertNotIn("Secret", guide)
        self.assertNotIn("Secret Show", guide)
        self.assertEqual(server.inventory()["attached"], [])

    def test_pluto_partner_links_become_master_playlists(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        entries = [{
            "name": "News", "source": "Pluto TV", "group": "Pluto TV", "logo": "", "lean": True,
            "tvg_id": "656538f8954b020008d389db",
            "url": "https://jmp2.uk/plu-656538f8954b020008d389db.m3u8",
        }, {
            "name": "Other", "source": "Samsung TV Plus", "group": "Samsung TV Plus", "logo": "",
            "lean": False, "tvg_id": "sam", "url": "https://jmp2.uk/stvp-US.m3u8",
        }]
        def fake_session():
            return {"stitcher": "https://stitcher.example.pluto.tv", "query": "appName=web&jwt=secret-token"}
        self.assertEqual(gather.rewrite_pluto(entries, fake_session), 1)
        self.assertTrue(entries[0]["url"].startswith("https://stitcher.example.pluto.tv/v2/stitch/hls/channel/656538f8954b020008d389db/master.m3u8?"))
        self.assertIn("masterJWTPassthrough=true", entries[0]["url"])
        self.assertIn("includeExtendedEvents=true", entries[0]["url"])
        self.assertNotIn("/v1/stitch/embed/", entries[0]["url"])
        self.assertNotIn("jmp2.uk", entries[0]["url"])
        self.assertEqual(entries[1]["url"], "https://jmp2.uk/stvp-US.m3u8")
        broken = [{"name": "Bad", "source": "Pluto TV", "url": "https://jmp2.uk/plu-not-a-channel.m3u8"}]
        def explode():
            raise AssertionError("session")
        self.assertEqual(gather.rewrite_pluto(broken, explode), 0)

    def test_pluto_apple_master_keeps_the_widescreen_rung(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        master = "\n".join((
            "#EXTM3U",
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",URI="https://cdn.example/subs.m3u8"',
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",DEFAULT=YES,URI="https://cdn.example/audio.m3u8"',
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English AD",URI="https://cdn.example/ad.m3u8",CHARACTERISTICS="public.accessibility.describes-video"',
            "#EXT-X-STREAM-INF:BANDWIDTH=640930,AUDIO=\"audio\"",
            "https://cdn.example/640930/playlist.m3u8",
            "#EXT-X-STREAM-INF:BANDWIDTH=1577180,AUDIO=\"audio\"",
            "https://cdn.example/1577180/playlist.m3u8",
            "#EXT-X-STREAM-INF:BANDWIDTH=1042180,AUDIO=\"audio\"",
            "https://cdn.example/1042180/playlist.m3u8",
            "#EXT-X-STREAM-INF:BANDWIDTH=3321280,AUDIO=\"audio\"",
            "https://cdn.example/3321280/playlist.m3u8",
        ))
        text = gather.apple_pluto_master(master, "https://stitcher.example/master.m3u8")
        self.assertIn("#EXT-X-VERSION:7", text)
        self.assertIn("FRAME-RATE=30.000", text)
        self.assertIn("RESOLUTION=1280x720", text)
        self.assertIn('CODECS="avc1.64001f,mp4a.40.2"', text)
        self.assertIn('CODECS="mp4a.40.2",URI="https://cdn.example/audio.m3u8"', text)
        self.assertIn("CLOSED-CAPTIONS=NONE", text)
        self.assertIn("https://cdn.example/3321280/playlist.m3u8", text)
        self.assertIn("https://cdn.example/audio.m3u8", text)
        self.assertNotIn("640930", text)
        self.assertNotIn("1577180", text)
        self.assertNotIn("1042180", text)
        self.assertNotIn("426x240", text)
        self.assertNotIn("subs", text)
        self.assertNotIn("describes-video", text)
        self.assertEqual(gather.apple_pluto_master("#EXTM3U\n", "https://stitcher.example/master.m3u8"), "")
        muxed = "\n".join((
            "#EXTM3U",
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Original",DEFAULT=YES,CHANNELS="2"',
            "#EXT-X-STREAM-INF:BANDWIDTH=640930,AUDIO=\"audio\"",
            "https://cdn.example/640930/playlist.m3u8",
            "#EXT-X-STREAM-INF:BANDWIDTH=3321280,AUDIO=\"audio\"",
            "https://cdn.example/3321280/playlist.m3u8",
        ))

        def measured(_url):
            raise AssertionError("known 16:9 rung must not be measured")

        widescreen = gather.apple_pluto_master(muxed, "https://stitcher.example/master.m3u8", measure=measured)
        self.assertIn("RESOLUTION=1280x720", widescreen)
        self.assertIn('CODECS="avc1.64001f,mp4a.40.2"', widescreen)
        self.assertIn("https://cdn.example/3321280/playlist.m3u8", widescreen)
        self.assertNotIn("TYPE=AUDIO", widescreen)
        self.assertNotIn("640930", widescreen)
        self.assertNotIn("426x240", widescreen)
        known = gather.apple_pluto_master(master, "https://stitcher.example/master.m3u8", measure=measured)
        self.assertIn("RESOLUTION=1280x720", known)
        self.assertIn("https://cdn.example/audio.m3u8", known)

    def test_pluto_channels_do_not_share_a_session(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        tokens = []
        gate = __import__("threading").Lock()

        def getter():
            with gate:
                token = "token-%d" % (len(tokens) + 1)
                tokens.append(token)
            return {"stitcher": "https://stitcher.example.pluto.tv", "query": "jwt=" + token}

        fetched = []

        def fetch(url):
            fetched.append(url)
            return b"\n".join((
                b"#EXTM3U",
                b"#EXT-X-STREAM-INF:BANDWIDTH=3321280",
                b"https://cdn.example/playlist.m3u8",
            ))

        entries = []
        for channel_id in ("656538f8954b020008d389db", "656538f8954b020008d389dc"):
            entries.append({
                "name": channel_id, "source": "Pluto TV", "group": "General", "logo": "",
                "tvg_id": channel_id,
                "url": "https://stitcher.example.pluto.tv/v2/stitch/hls/channel/%s/master.m3u8?jwt=shared" % channel_id,
            })
        self.assertEqual(gather.install_pluto_apple(entries, "http://192.168.1.120:8877", fetch=fetch, session_getter=getter), 2)
        self.assertEqual(sorted(tokens), ["token-1", "token-2"])
        self.assertEqual(len(fetched), 2)
        self.assertEqual(len({url.split("jwt=", 1)[1] for url in fetched}), 2)
        self.assertNotIn("jwt=shared", " ".join(fetched))
        self.assertTrue(all(url.startswith("http://192.168.1.120:8877/pluto/") for url in (entries[0]["url"], entries[1]["url"])))

    def test_kodi_playlist_keeps_tubi_ohio_and_xumo(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        entries = []
        for source, name, url in (
            ("Tubi", "Tubi Sports", "https://example.test/tubi"),
            ("Xumo", "Xumo Life", "https://example.test/xumo"),
            ("Ohio news", "WHIO", "https://example.test/whio"),
            ("Pluto TV", "Pluto Comedy", "https://example.test/pluto"),
            ("Plex", "Plex Movie", "https://example.test/plex"),
        ):
            entries.append({
                "name": name, "url": url, "tvg_id": source.lower().replace(" ", "-"),
                "logo": "", "group": "General", "source": source, "lean": source == "Ohio news",
            })
        guide = b"<tv></tv>"
        result = gather.finish_lineup(entries, [["#EXTINF:-1,Philo", "plugin://slyguy.philo/play"]],
                                      guide, "http://192.168.1.120:8877",
                                      probe=lambda url: {"bandwidth": 0, "height": 0})
        kodi = result["kodi"]
        self.assertIn("Tubi Sports", kodi)
        self.assertIn("Xumo Life", kodi)
        self.assertIn("WHIO", kodi)
        self.assertNotIn("Pluto Comedy", kodi)
        self.assertNotIn("Plex Movie", kodi)
        self.assertNotIn("plugin://", kodi)
        self.assertIn('url-tvg="http://192.168.1.120:8877/kodi.xml"', kodi)
        self.assertIn("Pluto Comedy", result["playlist"])
        self.assertIn('url-tvg="http://192.168.1.120:8877/epg.xml"', result["playlist"])

    def test_combined_guide_is_grouped_by_genre(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        plex = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="out" group-title="Sports & Outdoors",Outdoor Hour\n'
            "https://example.test/outdoor\n"
            '#EXTINF:-1 tvg-id="movie" group-title="United States",Friday Night Movie\n'
            "https://example.test/movie\n"
            '#EXTINF:-1 tvg-id="plain" group-title="United States",Something Else\n'
            "https://example.test/plain\n"
        )
        ohio = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="WHIOTV" group-title="Local",WHIO\n'
            "https://example.test/whio\n"
        )
        kids = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="kids" group-title="Kids",PBS Kids\n'
            "https://example.test/kids\n"
        )
        education = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="ark" group-title="Stations",Arkansas PBS\n'
            "https://example.test/arkansas\n"
        )
        direct, _plugins = gather.walk_entries(plex, "Plex")
        news, _plugins = gather.walk_entries(ohio, "Ohio news")
        kid_rows, _plugins = gather.walk_entries(kids, "PBS")
        edu_rows, _plugins = gather.walk_entries(education, "PBS")
        rows = direct + news + kid_rows + edu_rows
        by_name = {item["name"]: item for item in rows}
        self.assertEqual(by_name["Outdoor Hour"]["group"], "Sports")
        self.assertEqual(by_name["Outdoor Hour"]["source"], "Plex")
        self.assertEqual(by_name["Friday Night Movie"]["group"], "Movies")
        self.assertEqual(by_name["Something Else"]["group"], "General")
        self.assertEqual(by_name["WHIO"]["group"], "News")
        self.assertEqual(by_name["WHIO"]["source"], "Ohio news")
        self.assertTrue(by_name["WHIO"]["lean"])
        self.assertFalse(by_name["Something Else"]["lean"])
        self.assertEqual(by_name["PBS Kids"]["group"], "Kids")
        self.assertEqual(by_name["Arkansas PBS"]["group"], "Education")
        guide = (
            b'<tv><channel id="out"><display-name>Outdoor Hour</display-name></channel>'
            b'<programme channel="out" start="20261012000000 +0000" stop="20261012010000 +0000">'
            b"<title>Trail</title><category lang=\"en\">Sports</category></programme>"
            b'<channel id="movie"><display-name>Friday Night Movie</display-name></channel>'
            b"</tv>"
        )
        result = gather.finish_lineup(rows, [], guide, "http://192.168.1.120:8877",
                                      probe=lambda url: {"bandwidth": 0, "height": 0})
        playlist = result["playlist"]
        self.assertIn('group-title="Sports"', playlist)
        self.assertIn('group-title="Movies"', playlist)
        self.assertIn('group-title="General"', playlist)
        self.assertIn('group-title="News"', playlist)
        self.assertIn('group-title="Kids"', playlist)
        self.assertIn('group-title="Education"', playlist)
        self.assertNotIn('group-title="United States"', playlist)
        self.assertNotIn('group-title="Plex"', playlist)
        self.assertIn('group-title="House"', result["tuners"][0]["playlist"])
        self.assertNotIn('group-title="Sports"', result["tuners"][0]["playlist"])
        epg = result["epg"].decode()
        self.assertIn('<category lang="en">Sports</category>', epg)
        self.assertIn('<category lang="en">Movies</category>', epg)
        self.assertIn("<title>Trail</title>", epg)
        positions = [playlist.index(name) for name in (
            "WHIO", "Outdoor Hour", "Friday Night Movie", "PBS Kids", "Arkansas PBS", "Something Else")]
        self.assertEqual(positions, sorted(positions))

    def test_duplicate_keeps_the_clearer_stream_and_can_be_approved(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        def rows():
            return [
                {"name": "ABC News Live", "url": "https://example.test/low", "tvg_id": "low-id",
                 "logo": "", "group": "Xumo", "source": "Xumo", "lean": True},
                {"name": "ABC News Live HD", "url": "https://example.test/high", "tvg_id": "high-id",
                 "logo": "", "group": "Pluto TV", "source": "Pluto TV", "lean": True},
            ]
        def probe(url):
            return {"bandwidth": 3300000, "height": 1080} if url.endswith("high") else {"bandwidth": 800000, "height": 480}
        guide = (b'<tv><channel id="high-id"><display-name>ABC News Live</display-name></channel>'
                 b'<programme channel="high-id" start="20261012000000 +0000" stop="20261012010000 +0000">'
                 b'<title>Evening Report</title></programme></tv>')
        first = gather.finish_lineup(rows(), [], guide, "http://192.168.1.120:8877", probe=probe)
        self.assertIn("https://example.test/high", first["playlist"])
        self.assertIn("https://example.test/low", first["playlist"])
        self.assertIn("https://example.test/high", first["tuners"][0]["playlist"])
        self.assertNotIn("https://example.test/low", first["tuners"][0]["playlist"])
        self.assertEqual(len(first["tuners"]), 1)
        self.assertLessEqual(first["tuners"][0]["playlist"].count("#EXTINF"), 450)
        self.assertIn('group-title="House"', first["tuners"][0]["playlist"])
        text = json.dumps(first["lineup"])
        self.assertNotIn("example.test", text)
        self.assertNotIn("http", text)
        self.assertEqual(first["lineup"]["held"][0]["source"], "Xumo")
        self.assertIn("Evening Report", text)
        self.assertFalse(first["lineup"]["held"][0]["active"])
        gather.save_decision(first["lineup"]["held"][0]["key"], True)
        second = gather.finish_lineup(rows(), [], guide, "http://192.168.1.120:8877", probe=probe)
        self.assertIn("https://example.test/low", second["playlist"])
        self.assertIn("https://example.test/high", second["playlist"])
        self.assertTrue(second["lineup"]["held"][0]["active"])
        with self.assertRaises(ValueError):
            gather.save_decision("not-a-channel", True)

    def test_tuner_playlist_keeps_names_threadfin_would_split(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        entries = [{
            "name": "80's Sitcom Flashback",
            "url": "https://example.test/80's.m3u8",
            "tvg_id": "sitcom",
            "logo": 'https://example.test/logo"80.jpg',
            "group": "Pluto TV",
            "source": "Pluto TV",
            "lean": False,
        }]
        result = gather.finish_lineup(entries, [], b"<tv></tv>", "http://192.168.1.120:8877",
                                      probe=lambda url: {"bandwidth": 0, "height": 0})
        playlist = result["tuners"][0]["playlist"]
        self.assertNotIn("'", playlist)
        self.assertIn('group-title="House"', playlist)
        self.assertIn("80\u2019s Sitcom Flashback", playlist)
        self.assertIn("https://example.test/80%27s.m3u8", playlist)
        self.assertNotIn('logo"80', playlist)
        self.assertIn("80's Sitcom Flashback", result["playlist"])
        extinf = next(line for line in playlist.splitlines() if line.startswith("#EXTINF"))
        self.assertIn('tvg-name="80\u2019s Sitcom Flashback ', extinf)
        self.assertTrue(extinf.endswith(",80\u2019s Sitcom Flashback"))

    def test_tuner_playlist_keeps_channels_that_share_a_short_name(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        entries = []
        for suffix in ("a", "b"):
            entries.append({
                "name": "UFC",
                "url": "https://example.test/ufc-%s" % suffix,
                "tvg_id": "ufc-%s" % suffix,
                "logo": "",
                "group": "Plex",
                "source": "Plex",
                "lean": False,
            })
        result = gather.finish_lineup(entries, [], b"<tv></tv>", "http://192.168.1.120:8877",
                                      probe=lambda url: {"bandwidth": 0, "height": 0})
        playlist = result["tuners"][0]["playlist"]
        names = []
        for line in playlist.splitlines():
            if 'tvg-name="' not in line:
                continue
            names.append(line.split('tvg-name="', 1)[1].split('"', 1)[0])
            self.assertTrue(line.endswith(",UFC"))
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(result["playlist"].count(",UFC"), 2)
        self.assertNotIn("tvg-name=\"UFC ", result["playlist"])

    def test_same_station_name_copies_listings_and_skips_lookalikes(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        entries = [
            {"name": "BBC Earth (1080p)", "url": "https://example.test/earth", "tvg_id": "earth-copy",
             "logo": "", "group": "Fire TV", "source": "Fire TV", "lean": False},
            {"name": "ABC News Live 3 (720p)", "url": "https://example.test/abc", "tvg_id": "abc-copy",
             "logo": "", "group": "ABC News", "source": "ABC News", "lean": True},
            {"name": "News", "url": "https://example.test/news", "tvg_id": "news-copy",
             "logo": "", "group": "SOFAST", "source": "SOFAST", "lean": True},
            {"name": "AWE", "url": "https://example.test/awe", "tvg_id": "",
             "logo": "", "group": "Klowd", "source": "Klowd", "lean": False},
            {"name": "Boston 25", "url": "https://example.test/boston", "tvg_id": "boston-copy",
             "logo": "", "group": "Xumo", "source": "Xumo", "lean": True},
            {"name": "Fuel TV", "url": "https://example.test/fuel", "tvg_id": "fuel-copy",
             "logo": "", "group": "Klowd", "source": "Klowd", "lean": False},
            {"name": "Arkansas PBS", "url": "https://example.test/arkansas", "tvg_id": "PBS.us@KETS",
             "logo": "", "group": "PBS", "source": "PBS", "lean": False},
            {"name": "WMPT-DT2 (PBS Create)", "url": "https://example.test/create", "tvg_id": "Create.us@WMPT",
             "logo": "", "group": "PBS", "source": "PBS", "lean": False},
        ]
        house = (
            b'<tv><channel id="earth-donor"><display-name>BBC Earth</display-name></channel>'
            b'<programme channel="earth-donor" start="20261012000000 +0000" stop="20261012100000 +0000">'
            b'<title>Planet</title></programme></tv>'
        )
        donor = (
            b'<tv><channel id="abc"><display-name>ABC News Live</display-name></channel>'
            b'<programme channel="abc" start="20261012020000 +0000" stop="20261012030000 +0000">'
            b'<title>World News</title></programme>'
            b'<programme channel="abc" start="20220101000000 +0000" stop="20220101010000 +0000">'
            b'<title>Ancient</title></programme>'
            b'<channel id="abc-short"><display-name>ABC News Live</display-name></channel>'
            b'<programme channel="abc-short" start="20261012020000 +0000" stop="20261012030000 +0000">'
            b'<title>Brief</title></programme>'
            b'<channel id="kets"><display-name>PBS (KETS) Arkansas HD.us</display-name></channel>'
            b'<programme channel="kets" start="20261012040000 +0000" stop="20261012050000 +0000">'
            b'<title>NewsHour</title></programme>'
            b'<channel id="wmpt"><display-name>PBS (WMPT) Annapolis, MD HD.us</display-name></channel>'
            b'<programme channel="wmpt" start="20261012040000 +0000" stop="20261012050000 +0000">'
            b'<title>Annapolis</title></programme>'
            b'<channel id="awe-plus"><display-name>AWE Plus</display-name></channel>'
            b'<programme channel="awe-plus" start="20261012020000 +0000" stop="20261012030000 +0000">'
            b'<title>Wild Roads</title></programme>'
            b'<channel id="boston"><display-name>Boston</display-name></channel>'
            b'<programme channel="boston" start="20261012020000 +0000" stop="20261012030000 +0000">'
            b'<title>Wrong City</title></programme>'
            b'<channel id="tie-a"><display-name>Fuel TV</display-name></channel>'
            b'<programme channel="tie-a" start="20261012020000 +0000" stop="20261012030000 +0000">'
            b'<title>Left</title></programme>'
            b'<channel id="tie-b"><display-name>Fuel TV HD</display-name></channel>'
            b'<programme channel="tie-b" start="20261012020000 +0000" stop="20261012030000 +0000">'
            b'<title>Right</title></programme></tv>'
        )
        result = gather.finish_lineup(entries, [], house, "http://192.168.1.120:8877",
                                      probe=lambda url: {"bandwidth": 0, "height": 0},
                                      name_guides=[donor])
        guide = result["epg"].decode()
        self.assertIn('channel="earth-copy"', guide)
        self.assertEqual(guide.count(">Planet<"), 2)
        self.assertEqual(guide.count(">World News<"), 1)
        self.assertIn('channel="abc-copy"', guide)
        self.assertNotIn('programme channel="news-copy"', guide)
        self.assertNotIn('programme channel="boston-copy"', guide)
        self.assertNotIn('programme channel="fuel-copy"', guide)
        self.assertNotIn("Wild Roads", guide)
        self.assertNotIn("Wrong City", guide)
        self.assertNotIn(">Left<", guide)
        self.assertNotIn(">Right<", guide)
        self.assertIn('channel="PBS.us@KETS"', guide)
        self.assertIn("NewsHour", guide)
        self.assertNotIn("Ancient", guide)
        self.assertNotIn("Brief", guide)
        self.assertNotIn("Annapolis", guide)
        again, copied = gather.copy_listings_by_name(result["epg_source"], entries, [donor])
        self.assertEqual(copied, 0)
        self.assertEqual(again.count(b"Planet"), result["epg_source"].count(b"Planet"))
        seen = []

        def fetch(url, limit):
            seen.append(url)
            raise ValueError("missing")

        with patch.object(gather, "EPG_LIMIT", 20 * 1024 * 1024):
            self.assertEqual(gather.extra_name_guides(fetch), [])
        self.assertEqual(seen, [])
        with patch.object(gather, "EPG_LIMIT", 64 * 1024 * 1024):
            self.assertEqual(gather.extra_name_guides(fetch), [])
        self.assertEqual(len(seen), len(gather.NAME_GUIDE_SOURCES))

    def test_every_active_channel_is_packed_under_the_plex_limit(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
        import gather
        entries = []
        for index in range(500):
            entries.append({
                "name": "Station %03d" % index, "url": "https://example.test/station/%03d" % index,
                "tvg_id": "station-%03d" % index, "logo": "", "group": "Pack", "source": "Plex", "lean": False,
            })
        result = gather.finish_lineup(entries, [], b"<tv></tv>", "http://192.168.1.120:8877",
                                      probe=lambda url: {"bandwidth": 0, "height": 0})
        self.assertEqual(result["active"], 500)
        self.assertEqual(result["inactive"], 0)
        self.assertGreaterEqual(len(result["tuners"]), 2)
        covered = []
        for tuner in result["tuners"]:
            self.assertLessEqual(tuner["playlist"].count("#EXTINF"), 450)
            self.assertGreater(tuner["playlist"].count("#EXTINF"), 0)
            covered.append(tuner["playlist"])
            self.assertIn("<channel ", tuner["guide"].decode())
        blob = "\n".join(covered)
        for index in range(500):
            self.assertIn("https://example.test/station/%03d" % index, blob)
        self.assertNotIn("example.test", json.dumps(result["lineup"]))

    def test_portal_lineup_strips_stream_addresses(self):
        cleaned = server.scrub_public({
            "name": "News", "url": "https://example.test/a?token=private",
            "programmes": [{"title": "Evening"}],
        })
        self.assertEqual(cleaned["programmes"][0]["title"], "Evening")
        self.assertNotIn("url", cleaned)
        self.assertNotIn("example.test", json.dumps(cleaned))
        self.assertNotIn("private", json.dumps(cleaned))

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
