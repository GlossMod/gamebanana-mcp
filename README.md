# GameBanana MCP 

<div align="center">

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/) [![MCP 1.x](https://img.shields.io/badge/MCP-1.x-green.svg)](https://modelcontextprotocol.io/)

**匿名搜索 GameBanana 作品，查看作者署名、前置和归档文件。**

</div>

---

## ✨ 项目简介

这个非官方本地 stdio 服务读取 GameBanana 的公开游戏和 Mod。项目详情保留站点原始 HTML、各尺寸预览图、分组署名与前置说明；文件工具可以查询或下载当前及可访问的归档文件。

## ⚡ 快速开始

### 前置条件

- Python 3.11 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。
- 一个支持 stdio 的 MCP 客户端。公开查询和下载使用匿名访问，无需设置 API Key 或 Cookie。

### 安装与更新

在 `gamebanana-mcp` 仓库根目录执行：

```sh
uv sync --python 3.11
```

更新仓库源码后，重新执行上面的命令并重启客户端中的服务。若使用已有的 Python 虚拟环境，也可以用 `python -m pip install --upgrade .` 安装或更新，启动命令为 `gamebanana-mcp`。

### 配置客户端

将以下条目加入使用 `mcpServers` 格式的客户端，把 `--directory` 后的示例路径替换为本仓库的绝对路径。使用 VS Code 的 `.vscode/mcp.json` 时，顶层键使用 `servers`。

```json
{
  "mcpServers": {
    "gamebanana-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "E:/GitHub/mcp/gamebanana-mcp", "run", "--no-sync", "gamebanana-mcp"]
    }
  }
}
```

### 第一次调用

试试「在 *Baldi’s Basics Plus* 中搜索 `99 notebooks map`，列出作者注明的前置和可下载版本」。客户端可以先调用 `search_games` 取得游戏 `_idRow`，再将它作为 `game_id` 传给 `search_mods`。

## 🧰 工具列表

| 工具               | 主要参数                           | 用途                                                                                                                                        |
| ------------------ | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_games`       | 可选 `page`、`per_page`            | 分页浏览游戏目录。                                                                                                                          |
| `search_games`     | `query`；可选 `page`               | 按关键词查游戏，取得后续筛选用的 `_idRow`。                                                                                                 |
| `browse_mods`      | 可选 `game_id`、`page`、`per_page` | 浏览 Mod 目录，或只浏览所选游戏。                                                                                                           |
| `search_mods`      | `query`；可选 `game_id`、`page`    | 在所有游戏或指定游戏内按关键词搜索 Mod。                                                                                                    |
| `get_mod`          | `mod_id`                           | 返回完整 `ProfilePage`。项目主页见 `_sProfileUrl`，说明见 `_sText`，提交者、署名和图集分别见 `_aSubmitter`、`_aCredits`、`_aPreviewMedia`。 |
| `get_requirements` | `mod_id`                           | 读取 `_aRequirements` 与 `_bAdvancedRequirementsExist`，保留纯文字、空链接及作者填写的附加状态。                                            |
| `list_files`       | `mod_id`                           | 返回 `files` 列表，记录保留文件名、大小、可用校验值和下载地址；`_bIsArchived` 标记归档。                                                    |
| `get_file`         | `mod_id`、`file_id`                | 按文件 `_idRow` 取出一条完整元数据，包括可访问的归档记录。                                                                                  |
| `download_file`    | `mod_id`、`file_id`                | 重新查询项目文件列表后下载所选记录，返回本地路径、大小和摘要。                                                                              |

**分页。** `page` 从 1 开始，目录工具接受 `per_page`（1–50）。关键词搜索的每页大小由站点决定。检查 `_aMetadata._bIsComplete` 判断是否还有下一页。

**本地下载。** 默认目录为 `~/Downloads/GameBanana`，可在客户端的 `env` 中设置 `GAMEBANANA_DOWNLOAD_DIR` 为其他绝对路径。文件按块写入临时文件，核对可用的大小和站点 MD5 后保存。已有同名文件会保留，成功结果含 `path`、`bytes`、`sha256` 和已验证的 `md5`（如有）。

## 测试

```sh
uv run python -m unittest discover -s tests -v
uv run python tests/smoke_stdio.py
```

第一条运行离线测试。第二条经真实 stdio 客户端浏览、搜索并读取当前及归档文件元数据，随后匿名下载约 4.5 MB 的样本，在临时目录回读校验。

## 构建与发布到 PyPI

本地可运行 `uv build`，在 `dist/` 检查 wheel 和源码包。

在 PyPI 为 `gamebanana-mcp` 配置 GitHub Actions Trusted Publisher：仓库为 `GlossMod/gamebanana-mcp`（Owner：`GlossMod`，Repository：`gamebanana-mcp`），Workflow：`publish.yml`，Environment：`pypi`。

在 GitHub 发布带版本标签的 Release（例如 `v0.1.1`）后，`.github/workflows/publish.yml` 会使用标签版本构建并检查 wheel 和源码包，通过后自动发布到 PyPI。版本只在 CI 中替换，不会回写仓库。

参考：[GameBanana](https://gamebanana.com/) · [项目详情响应示例](https://gamebanana.com/apiv11/Mod/639885/ProfilePage)
