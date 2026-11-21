"""Read GameBanana API v11 without transforming its records."""

import re
from pathlib import Path
from typing import Any

import httpx

from .downloads import save_file
from .errors import GameBananaError

API = "https://gamebanana.com/apiv11"
USER_AGENT = "gamebanana-mcp/0.1.0"
JsonObject = dict[str, Any]


class GameBanana:
    def __init__(self, http: httpx.AsyncClient, download_dir: Path):
        self.http = http
        self.download_dir = download_dir

    async def _get(self, endpoint: str, params: dict[str, str | int] | None = None) -> Any:
        try:
            response = await self.http.get(
                f"{API}/{endpoint}",
                params=params,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.RequestError:
            raise GameBananaError(f"GameBanana {endpoint} request failed or timed out.") from None
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            data = None
        # Use only stable error codes; upstream messages can echo untrusted input.
        code = data.get("_sErrorCode") if isinstance(data, dict) else None
        if response.status_code != 200 or code is not None:
            reason = {
                400: "the API rejected the request",
                401: "anonymous access is not authorized",
                403: "access is forbidden or restricted by GameBanana",
                404: "the requested record or route was not found",
                429: "GameBanana rate limited the request; try again later",
            }.get(response.status_code, "the API did not return a successful result")
            suffix = f" [{code}]" if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code) else ""
            raise GameBananaError(f"GameBanana {endpoint}: HTTP {response.status_code}, {reason}{suffix}.")
        if data is None:
            raise GameBananaError(f"GameBanana {endpoint} did not return usable JSON.")
        return data

    async def _page(self, endpoint: str, params: dict[str, str | int]) -> JsonObject:
        data = await self._get(endpoint, params)
        if not isinstance(data, dict) or not _is_record_list(data.get("_aRecords")):
            raise GameBananaError(f"GameBanana {endpoint} returned an invalid records page.")
        return data

    async def list_games(self, page: int, per_page: int) -> JsonObject:
        return await self._page("Game/Index", {"_nPage": page, "_nPerpage": per_page})

    async def browse_mods(self, game_id: int | None, page: int, per_page: int) -> JsonObject:
        params: dict[str, str | int] = {"_nPage": page, "_nPerpage": per_page}
        if game_id is not None:
            params["_aFilters[Generic_Game]"] = game_id
        return await self._page("Mod/Index", params)

    async def search_games(self, query: str, page: int) -> JsonObject:
        return await self._search("Game", query, page)

    async def search_mods(self, query: str, game_id: int | None, page: int) -> JsonObject:
        return await self._search("Mod", query, page, game_id)

    async def _search(self, model: str, query: str, page: int, game_id: int | None = None) -> JsonObject:
        params: dict[str, str | int] = {
            "_sSearchString": query,
            "_sModelName": model,
            "_nPage": page,
        }
        if game_id is not None:
            params["_idGameRow"] = game_id
        return await self._page("Util/Search/Results", params)

    async def get_mod(self, mod_id: int) -> JsonObject:
        data = await self._get(f"Mod/{mod_id}/ProfilePage")
        if not isinstance(data, dict) or data.get("_idRow") != mod_id:
            raise GameBananaError(f"GameBanana returned no matching ProfilePage for Mod {mod_id}.")
        return data

    async def get_requirements(self, mod_id: int) -> JsonObject:
        data = await self.get_mod(mod_id)
        return {key: data[key] for key in ("_aRequirements", "_bAdvancedRequirementsExist") if key in data}

    async def list_files(self, mod_id: int) -> list[JsonObject]:
        data = await self._get(f"Mod/{mod_id}/Files")
        if not _is_record_list(data):
            raise GameBananaError(f"GameBanana returned an invalid file list for Mod {mod_id}.")
        return data

    async def get_file(self, mod_id: int, file_id: int) -> JsonObject:
        rows = await self.list_files(mod_id)
        matches = [row for row in rows if row.get("_idRow") == file_id]
        if len(matches) != 1:
            raise GameBananaError(f"File {file_id} did not match exactly one file of Mod {mod_id}.")
        return matches[0]

    async def download_file(self, mod_id: int, file_id: int) -> JsonObject:
        record = await self.get_file(mod_id, file_id)
        return await save_file(self.http, self.download_dir, record)


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(row, dict) for row in value)
