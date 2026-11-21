import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from gamebanana_mcp.api import GameBanana
from gamebanana_mcp.downloads import _check_headers
from gamebanana_mcp.errors import GameBananaError
from gamebanana_mcp.server import create_server

MOD_ID = 639885
FILE_ID = 1626358
ARCHIVE_ID = 1582645
FILES_PATH = f"/apiv11/Mod/{MOD_ID}/Files"
PROFILE_PATH = f"/apiv11/Mod/{MOD_ID}/ProfilePage"
PAYLOAD = b"fake file contents\x00\xff\n"
MD5 = hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest()
CURRENT = {
    "_idRow": FILE_ID,
    "_sFile": "current.zip",
    "_nFilesize": len(PAYLOAD),
    "_sMd5Checksum": MD5.upper(),
    "_sDownloadUrl": f"https://gamebanana.com/dl/{FILE_ID}",
    "_bIsArchived": False,
    "_sVersion": "2.0",
    "_futureField": {"keep": True},
}
ARCHIVED = {
    "_idRow": ARCHIVE_ID,
    "_sFile": "\u5f52\u6863.zip",
    "_sDownloadUrl": f"https://gamebanana.com/dl/{ARCHIVE_ID}",
    "_bIsArchived": True,
}


class PartialBody(httpx.AsyncByteStream):
    def __init__(self, pause: asyncio.Event | None = None):
        self.pause = pause

    async def __aiter__(self):
        yield b"x" * (128 * 1024)
        if self.pause is not None:
            self.pause.set()
            await asyncio.Event().wait()
        raise httpx.ReadError("fixture socket closed")


class GameBananaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="gamebanana-unit-")))
        self.directory = root / "downloads"
        self.requests = []

    async def api(self, handler):
        def track(request):
            self.requests.append(request)
            self.assertNotIn("Authorization", request.headers)
            self.assertNotIn("Cookie", request.headers)
            return handler(request)

        http = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.MockTransport(track)))
        return GameBanana(http, self.directory)

    def with_files(self, responder, records=None):
        def handler(request):
            if request.url.path == FILES_PATH:
                return httpx.Response(200, json=[CURRENT, ARCHIVED] if records is None else records)
            return responder(request)

        return handler

    def assert_no_download(self):
        self.assertFalse(self.directory.exists() and any(self.directory.iterdir()))

    async def test_directory_and_search_use_distinct_filters_and_pages(self):
        page = {
            "_aMetadata": {"_bIsComplete": False, "_nPerpage": 15, "_nRecordCount": 24},
            "_aRecords": [{"_idRow": 33, "_sName": "demo", "untouched": {"list": [1, 2]}}],
            "future_page_field": True,
        }
        api = await self.api(lambda req: httpx.Response(200, json=page))
        self.assertEqual(await api.list_games(2, 5), page)
        self.assertEqual(await api.browse_mods(26199, 3, 5), page)
        self.assertEqual(await api.search_games("Baldi's Basics Plus", 3), page)
        self.assertEqual(await api.search_mods("map & test", 26199, 4), page)
        self.assertEqual(await api.browse_mods(None, 1, 5), page)
        self.assertEqual(await api.search_mods("map", None, 1), page)
        self.assertEqual(
            [(r.url.path, dict(r.url.params)) for r in self.requests],
            [
                ("/apiv11/Game/Index", {"_nPerpage": "5", "_nPage": "2"}),
                ("/apiv11/Mod/Index", {"_aFilters[Generic_Game]": "26199", "_nPerpage": "5", "_nPage": "3"}),
                ("/apiv11/Util/Search/Results", {"_sSearchString": "Baldi's Basics Plus", "_sModelName": "Game", "_nPage": "3"}),
                ("/apiv11/Util/Search/Results", {"_sSearchString": "map & test", "_sModelName": "Mod", "_idGameRow": "26199", "_nPage": "4"}),
                ("/apiv11/Mod/Index", {"_nPerpage": "5", "_nPage": "1"}),
                ("/apiv11/Util/Search/Results", {"_sSearchString": "map", "_sModelName": "Mod", "_nPage": "1"}),
            ],
        )

    async def test_last_page_and_empty_page_remain_raw(self):
        data = {"_aMetadata": {"_bIsComplete": True, "_nRecordCount": 0}, "_aRecords": []}
        api = await self.api(lambda req: httpx.Response(200, json=data))
        self.assertEqual(await api.search_mods("no matches", 26199, 2), data)

    async def test_profile_and_requirements_preserve_nested_source_fields(self):
        requirements = [["read the README", ""], ["required", "https://gamebanana.com/mods/8", "Installed", "BlueColor", "Required", "OrangeColor"]]
        profile = {
            "_idRow": MOD_ID,
            "_sText": '<p>Hello <a href="https://example.com">external link</a></p>',
            "_sProfileUrl": f"https://gamebanana.com/mods/{MOD_ID}",
            "_aPreviewMedia": {"_aImages": [{"_sFile": "large.jpg", "_sFile220": "small.jpg", "_wFile220": 220}]},
            "_aSubmitter": {"_idRow": 1, "_sName": "creator"},
            "_aCredits": [{"_sGroupName": "Contributors", "_aAuthors": [{"_sName": "artist"}]}],
            "_aRequirements": requirements,
            "_bAdvancedRequirementsExist": True,
        }
        api = await self.api(lambda req: httpx.Response(200, json=profile))
        self.assertEqual(await api.get_mod(MOD_ID), profile)
        self.assertEqual(await api.get_requirements(MOD_ID), {"_aRequirements": requirements, "_bAdvancedRequirementsExist": True})
        self.assertTrue(all(req.url.path == PROFILE_PATH for req in self.requests))

    async def test_missing_requirements_not_filled(self):
        api = await self.api(lambda req: httpx.Response(200, json={"_idRow": MOD_ID, "_bAdvancedRequirementsExist": False}))
        self.assertEqual(await api.get_requirements(MOD_ID), {"_bAdvancedRequirementsExist": False})

    async def test_explicit_empty_requirements_remain_empty(self):
        api = await self.api(lambda req: httpx.Response(200, json={"_idRow": MOD_ID, "_aRequirements": []}))
        self.assertEqual(await api.get_requirements(MOD_ID), {"_aRequirements": []})

    async def test_api_failures_are_not_empty_requirements(self):
        for status, code in [(403, "FORBIDDEN"), (404, "NO_SUCH_RECORD"), (429, "RATE_LIMITED"), (200, "INPUT_ERRORS")]:
            with self.subTest(status=status):
                api = await self.api(lambda req: httpx.Response(status, json={"_sErrorCode": code, "_sErrorMessage": "do not echo this"}))
                with self.assertRaisesRegex(GameBananaError, rf"HTTP {status}.*\[{code}\]") as failed:
                    await api.get_requirements(MOD_ID)
                self.assertNotIn("do not echo", str(failed.exception))

    async def test_malformed_responses_are_not_empty_records(self):
        api = await self.api(lambda req: httpx.Response(200, text="<html>oops</html>"))
        with self.assertRaisesRegex(GameBananaError, "usable JSON"):
            await api.list_games(1, 5)
        api = await self.api(lambda req: httpx.Response(200, json={"_unrecognized": []}))
        with self.assertRaisesRegex(GameBananaError, "invalid records page"):
            await api.list_games(1, 5)
        with self.assertRaisesRegex(GameBananaError, "invalid file list"):
            await api.list_files(MOD_ID)

    async def test_list_includes_current_and_archive_without_fetching_bytes(self):
        api = await self.api(self.with_files(lambda req: self.fail("unexpected content download")))
        self.assertEqual(await api.list_files(MOD_ID), [CURRENT, ARCHIVED])
        self.assertEqual(len(self.requests), 1)
        self.assert_no_download()

    async def test_get_file_returns_exact_record_including_archive(self):
        api = await self.api(self.with_files(lambda req: self.fail("metadata requested download bytes")))
        self.assertEqual(await api.get_file(MOD_ID, FILE_ID), CURRENT)
        self.assertEqual(await api.get_file(MOD_ID, ARCHIVE_ID), ARCHIVED)
        with self.assertRaisesRegex(GameBananaError, "did not match exactly one"):
            await api.get_file(MOD_ID, 99)
        self.assertTrue(all(req.url.path == FILES_PATH for req in self.requests))
        self.assert_no_download()

    async def test_redirected_download_verifies_and_keeps_exact_bytes(self):
        def responder(request):
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            if request.url.host == "gamebanana.com":
                self.assertEqual(request.url.path, f"/dl/{FILE_ID}")
                return httpx.Response(302, headers={"Location": "https://files.gamebanana.com/mods/download.zip?token=fixture"})
            if request.url.host == "files.gamebanana.com":
                return httpx.Response(302, headers={"Location": "https://filecache41.gamebanana.com/mods/download.zip?token=fixture"})
            self.assertEqual(request.url.host, "filecache41.gamebanana.com")
            self.assertEqual(request.url.params.get("token"), "fixture")
            return httpx.Response(200, content=PAYLOAD)

        api = await self.api(self.with_files(responder))
        result = await api.download_file(MOD_ID, FILE_ID)
        path = self.directory / CURRENT["_sFile"]
        self.assertEqual(result, {"file_id": FILE_ID, "path": str(path.resolve()), "bytes": len(PAYLOAD), "md5": MD5, "sha256": hashlib.sha256(PAYLOAD).hexdigest()})
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(list(self.directory.iterdir()), [path])

    async def test_archived_file_without_upstream_size_or_hash(self):
        api = await self.api(self.with_files(lambda req: httpx.Response(200, content=PAYLOAD)))
        result = await api.download_file(MOD_ID, ARCHIVE_ID)
        self.assertNotIn("md5", result)
        self.assertEqual(Path(result["path"]).name, ARCHIVED["_sFile"])
        self.assertEqual(result["bytes"], len(PAYLOAD))
        self.assertEqual(self.requests[-1].url.path, f"/dl/{ARCHIVE_ID}")

    async def test_file_must_be_a_member_and_record_must_have_url(self):
        api = await self.api(self.with_files(lambda req: self.fail("download before record validation")))
        with self.assertRaisesRegex(GameBananaError, "did not match exactly one"):
            await api.download_file(MOD_ID, FILE_ID + 1)
        api = await self.api(self.with_files(lambda req: self.fail("download without URL"), [{**CURRENT, "_sDownloadUrl": ""}]))
        with self.assertRaisesRegex(GameBananaError, "no download address"):
            await api.download_file(MOD_ID, FILE_ID)
        self.assert_no_download()

    async def test_expired_or_forbidden_download_never_exposes_url_or_writes_file(self):
        for status, message in [(403, "denied"), (410, "expired")]:
            with self.subTest(status=status):
                def responder(request):
                    if request.url.host == "gamebanana.com":
                        return httpx.Response(302, headers={"Location": "https://files.gamebanana.com/mods/old.zip?token=private"})
                    return httpx.Response(status, content=b"unavailable")

                api = await self.api(self.with_files(responder))
                with self.assertRaisesRegex(GameBananaError, f"{status}.*{message}") as failed:
                    await api.download_file(MOD_ID, FILE_ID)
                self.assertNotIn("token", str(failed.exception))
                self.assert_no_download()

    async def test_existing_file_is_not_overwritten_or_requested(self):
        self.directory.mkdir()
        original = self.directory / CURRENT["_sFile"]
        original.write_bytes(b"existing")
        api = await self.api(self.with_files(lambda req: self.fail("download despite local collision")))
        with self.assertRaisesRegex(GameBananaError, "already exists"):
            await api.download_file(MOD_ID, FILE_ID)
        self.assertEqual(original.read_bytes(), b"existing")
        self.assertEqual(list(self.directory.iterdir()), [original])

    async def test_collision_after_stream_opens_preserves_other_writer(self):
        original = self.directory / CURRENT["_sFile"]

        def responder(request):
            original.write_bytes(b"created by other writer")
            return httpx.Response(200, content=PAYLOAD)

        api = await self.api(self.with_files(responder))
        with self.assertRaisesRegex(GameBananaError, "already exists"):
            await api.download_file(MOD_ID, FILE_ID)
        self.assertEqual(original.read_bytes(), b"created by other writer")
        self.assertEqual(list(self.directory.iterdir()), [original])

    async def test_interrupted_stream_cleans_partial_file(self):
        api = await self.api(self.with_files(lambda req: httpx.Response(200, stream=PartialBody())))
        with self.assertRaisesRegex(GameBananaError, "interrupted"):
            await api.download_file(MOD_ID, ARCHIVE_ID)
        self.assert_no_download()

    async def test_cancelled_task_cleans_partial_file(self):
        started = asyncio.Event()
        api = await self.api(self.with_files(lambda req: httpx.Response(200, stream=PartialBody(started))))
        task = asyncio.create_task(api.download_file(MOD_ID, ARCHIVE_ID))
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(len(list(self.directory.glob("*.part"))), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_no_download()

    async def test_size_and_checksum_mismatches_never_publish(self):
        wrong_bytes = b"!" * len(PAYLOAD)
        for contents, message in [(b"short", "Content-Length"), (wrong_bytes, "MD5 did not match")]:
            with self.subTest(message=message):
                api = await self.api(self.with_files(lambda req: httpx.Response(200, content=contents)))
                with self.assertRaisesRegex(GameBananaError, message):
                    await api.download_file(MOD_ID, FILE_ID)
                self.assert_no_download()

    async def test_truncated_download_with_no_content_length(self):
        class Truncated(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"short"

        api = await self.api(self.with_files(lambda req: httpx.Response(200, stream=Truncated())))
        with self.assertRaisesRegex(GameBananaError, "size mismatch"):
            await api.download_file(MOD_ID, FILE_ID)
        self.assert_no_download()

    async def test_disallows_external_redirects_and_excessive_redirects(self):
        for location, message in [("https://example.com/file", "not on an allowed GameBanana"), (f"/dl/{FILE_ID}", "excessive redirect")]:
            with self.subTest(location=location):
                def responder(request):
                    self.assertEqual(request.url.host, "gamebanana.com")
                    return httpx.Response(302, headers={"Location": location})

                api = await self.api(self.with_files(responder))
                with self.assertRaisesRegex(GameBananaError, message):
                    await api.download_file(MOD_ID, FILE_ID)
                self.assert_no_download()

    async def test_disallows_path_traversal_and_windows_device_names(self):
        for name in ["../escape.zip", r"..\escape.zip", "x.zip:ads", "NUL .zip", "COM\u00b9.txt", "x.zip. ", "x\x00.zip"]:
            with self.subTest(name=name):
                api = await self.api(self.with_files(lambda req: self.fail("download with unsafe name"), [{**CURRENT, "_sFile": name}]))
                with self.assertRaisesRegex(GameBananaError, "safe filename"):
                    await api.download_file(MOD_ID, FILE_ID)
                self.assert_no_download()

    async def test_html_and_partial_content_are_not_downloads(self):
        api = await self.api(self.with_files(lambda req: httpx.Response(200, text="<html>log in</html>", headers={"Content-Type": "text/html; charset=utf-8"})))
        with self.assertRaisesRegex(GameBananaError, "HTML page"):
            await api.download_file(MOD_ID, ARCHIVE_ID)
        api = await self.api(self.with_files(lambda req: httpx.Response(206, content=b"bytes")))
        with self.assertRaisesRegex(GameBananaError, "206.*complete file"):
            await api.download_file(MOD_ID, ARCHIVE_ID)
        self.assert_no_download()

    async def test_rejects_invalid_content_length_and_encoded_body(self):
        with self.assertRaisesRegex(GameBananaError, "Content-Length"):
            _check_headers(httpx.Response(200, headers={"Content-Length": "abc"}), None)
        with self.assertRaisesRegex(GameBananaError, "content encoding"):
            _check_headers(httpx.Response(200, headers={"Content-Encoding": "gzip"}), None)

    async def test_tool_arguments_validate_before_network(self):
        server = create_server(self.directory)
        tools = await server.list_tools()
        by_name = {tool.name: tool.inputSchema for tool in tools}
        page = by_name["list_games"]["properties"]
        self.assertEqual(page["page"]["minimum"], 1)
        self.assertEqual(page["per_page"]["maximum"], 50)
        self.assertNotIn("per_page", by_name["search_mods"]["properties"])
        self.assertEqual(by_name["download_file"]["properties"]["file_id"]["minimum"], 1)
        self.assertEqual(by_name["search_mods"]["properties"]["query"]["minLength"], 1)
        for name, arguments, message in [
            ("list_games", {"page": 0}, "greater than or equal to 1"),
            ("list_games", {"per_page": 51}, "less than or equal to 50"),
            ("search_mods", {"query": "   "}, "at least 1 character"),
            ("download_file", {"mod_id": MOD_ID, "file_id": 0}, "greater than or equal to 1"),
        ]:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaisesRegex(ToolError, message):
                    await server.call_tool(name, arguments)
