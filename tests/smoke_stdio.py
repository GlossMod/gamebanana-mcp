"""Opt-in live smoke test: use the installed CLI and public GameBanana API."""

import asyncio
import hashlib
import json
import os
import sysconfig
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

GAME_ID = 26199
MOD_ID = 639885
FILE_ID = 1626358


async def smoke():
    entry = Path(sysconfig.get_path("scripts")) / ("gamebanana-mcp.exe" if os.name == "nt" else "gamebanana-mcp")
    assert entry.is_file(), f"Expected installed console entry point: {entry}"
    with tempfile.TemporaryDirectory(prefix="gamebanana-mcp-live-") as directory:
        parameters = StdioServerParameters(command=str(entry), env={**os.environ, "GAMEBANANA_DOWNLOAD_DIR": directory})
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            assert names == ["browse_mods", "download_file", "get_file", "get_mod", "get_requirements", "list_files", "list_games", "search_games", "search_mods"]

            async def call(tool, **arguments):
                response = await session.call_tool(tool, arguments)
                assert not response.isError, f"{tool} failed: {response.content}"
                if response.structuredContent is not None:
                    return response.structuredContent
                # Compatibility with SDK 1.x releases supporting unstructured text output.
                return json.loads(response.content[0].text)

            catalog = await call("list_games", page=1, per_page=2)
            assert catalog["_aRecords"] and len(catalog["_aRecords"]) == 2
            games = await call("search_games", query="Baldi's Basics Plus")
            game = next(row for row in games["_aRecords"] if row["_idRow"] == GAME_ID)
            browse = [await call("browse_mods", game_id=game["_idRow"], page=page, per_page=20) for page in (1, 2)]
            assert not browse[0]["_aMetadata"]["_bIsComplete"]
            assert all(result["_aRecords"] and all(row["_aGame"]["_idRow"] == GAME_ID for row in result["_aRecords"]) for result in browse)
            page_ids = [[row["_idRow"] for row in result["_aRecords"]] for result in browse]
            assert page_ids[0] != page_ids[1]
            search = await call("search_mods", query="99 notebooks map", game_id=game["_idRow"])
            assert search["_aRecords"] and all(row["_aGame"]["_idRow"] == GAME_ID for row in search["_aRecords"])
            matched = next(row for row in search["_aRecords"] if row["_idRow"] == MOD_ID)
            profile = await call("get_mod", mod_id=matched["_idRow"])
            assert profile["_sProfileUrl"] == f"https://gamebanana.com/mods/{MOD_ID}"
            assert profile["_sText"] and profile["_aCredits"] and profile["_aSubmitter"]
            preview_keys = sorted(profile["_aPreviewMedia"]["_aImages"][0].keys())
            assert {"_sFile", "_sFile220"} <= set(preview_keys)
            requirements = await call("get_requirements", mod_id=MOD_ID)
            assert requirements["_aRequirements"] == profile["_aRequirements"]
            linkless = next(row for row in requirements["_aRequirements"] if row[0] and not row[1])

            rows = (await call("list_files", mod_id=MOD_ID))["files"]
            archives = [row for row in rows if row.get("_bIsArchived")]
            archived_ids = [row["_idRow"] for row in archives]
            assert archived_ids and set(archived_ids) == {row["_idRow"] for row in profile["_aArchivedFiles"]}
            assert (await call("get_file", mod_id=MOD_ID, file_id=archived_ids[0])) == archives[0]
            selected = await call("get_file", mod_id=MOD_ID, file_id=FILE_ID)
            assert selected == next(row for row in rows if row["_idRow"] == FILE_ID)
            assert not selected["_bIsArchived"] and 0 < selected["_nFilesize"] < 10 * 1024 * 1024
            assert not list(Path(directory).iterdir()), "Listing files unexpectedly wrote to disk"
            result = await call("download_file", mod_id=MOD_ID, file_id=selected["_idRow"])
            file = Path(result["path"])
            assert file.parent == Path(directory).resolve() and file.name == selected["_sFile"]
            with file.open("rb") as stream:
                actual_md5 = hashlib.file_digest(stream, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()
            with file.open("rb") as stream:
                actual_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            assert file.stat().st_size == result["bytes"] == selected["_nFilesize"]
            assert actual_md5 == result["md5"] == selected["_sMd5Checksum"].lower()
            assert actual_sha256 == result["sha256"]
            assert list(file.parent.iterdir()) == [file]
            evidence = {
                "server": initialized.serverInfo.model_dump(),
                "protocol": initialized.protocolVersion,
                "tools": names,
                "game": {"id": GAME_ID, "name": game["_sName"]},
                "browse_page_ids": page_ids,
                "search_page_metadata": search["_aMetadata"],
                "profile": {"id": MOD_ID, "homepage": profile["_sProfileUrl"], "preview_keys": preview_keys},
                "linkless_requirement": linkless,
                "file_count": len(rows),
                "archive_ids": archived_ids,
                "archive_ids_match_profile": True,
                "verified_metadata_file_ids": [FILE_ID, archived_ids[0]],
                "verified_download": result,
            }
    assert not file.exists() and not Path(directory).exists()
    evidence["temp_directory_cleaned"] = True
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    asyncio.run(smoke())
