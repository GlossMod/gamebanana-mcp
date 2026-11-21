"""MCP stdio entry point and tool definitions."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator

import httpx
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, StringConstraints

from .api import USER_AGENT, GameBanana, JsonObject

Page = Annotated[int, Field(ge=1, description="One-based GameBanana page number.")]
PerPage = Annotated[int, Field(ge=1, le=50, description="Page size for directory listing (1–50).")]
GameId = Annotated[int, Field(ge=1, description="GameBanana game _idRow.")]
ModId = Annotated[int, Field(ge=1, description="GameBanana Mod _idRow (e.g. from /mods/639885).")]
FileId = Annotated[int, Field(ge=1, description="File _idRow returned by list_files for this Mod.")]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def create_server(download_dir: Path | None = None) -> FastMCP:
    if download_dir is None:
        configured = os.environ.get("GAMEBANANA_DOWNLOAD_DIR")
        download_dir = Path(configured).expanduser() if configured else Path.home() / "Downloads" / "GameBanana"
    download_dir = download_dir.absolute()

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[GameBanana]:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60, connect=15),
        ) as http:
            yield GameBanana(http, download_dir)

    server = FastMCP(
        "gamebanana-mcp",
        instructions="Browse or search public GameBanana games and Mods. Get file IDs with list_files, then explicitly request a local download.",
        lifespan=lifespan,
        log_level="WARNING",  # Do not log HTTPX request URLs (redirects may include tokens).
    )

    def api(ctx: Context) -> GameBanana:
        return ctx.request_context.lifespan_context

    @server.tool()
    async def list_games(ctx: Context, page: Page = 1, per_page: PerPage = 20) -> JsonObject:
        """List GameBanana games from the Game directory. Use search_games for keywords."""
        return await api(ctx).list_games(page, per_page)

    @server.tool()
    async def search_games(ctx: Context, query: Query, page: Page = 1) -> JsonObject:
        """Find GameBanana games by keyword; use a result's _idRow for Mod filtering. Page size is set by GameBanana."""
        return await api(ctx).search_games(query, page)

    @server.tool()
    async def browse_mods(
        ctx: Context, game_id: GameId | None = None, page: Page = 1, per_page: PerPage = 20
    ) -> JsonObject:
        """Browse the Mod directory, optionally for one game. This is not a keyword search."""
        return await api(ctx).browse_mods(game_id, page, per_page)

    @server.tool()
    async def search_mods(
        ctx: Context, query: Query, game_id: GameId | None = None, page: Page = 1
    ) -> JsonObject:
        """Search Mods by keyword, optionally within one game. Page size is set by GameBanana."""
        return await api(ctx).search_mods(query, game_id, page)

    @server.tool()
    async def get_mod(ctx: Context, mod_id: ModId) -> JsonObject:
        """Fetch the full raw ProfilePage object, including homepage, HTML, previews, credits and requirements if provided."""
        return await api(ctx).get_mod(mod_id)

    @server.tool()
    async def get_requirements(ctx: Context, mod_id: ModId) -> JsonObject:
        """Read author's raw requirement rows, including entries with no link and any statuses. Missing source fields are omitted."""
        return await api(ctx).get_requirements(mod_id)

    @server.tool()
    async def list_files(ctx: Context, mod_id: ModId) -> JsonObject:
        """List raw file records (current and available archived) for this Mod. Does not download files."""
        return {"files": await api(ctx).list_files(mod_id)}

    @server.tool()
    async def get_file(ctx: Context, mod_id: ModId, file_id: FileId) -> JsonObject:
        """Return one raw file record by its ID within a Mod, including archived records. Does not download files."""
        return await api(ctx).get_file(mod_id, file_id)

    @server.tool()
    async def download_file(ctx: Context, mod_id: ModId, file_id: FileId) -> JsonObject:
        """Download the selected file to the configured local directory. Verify available size and MD5; never overwrite an existing file."""
        return await api(ctx).download_file(mod_id, file_id)

    return server


def main() -> None:
    create_server().run(transport="stdio")
