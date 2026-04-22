# codex-history / codex-helper

`codex-history` is a small local tool for managing Codex history stored under `~/.codex`.

`codex-history` 是一个本地小工具，用来管理保存在 `~/.codex` 下的 Codex 历史记录。

It is designed for one job: make old Codex conversations easier to browse, clean up, and resume.

它的目标很简单：让旧的 Codex 对话更容易查看、清理、恢复。

## Features / 功能

- Browse local Codex history in a web UI
- Search by title, prompt, reply, cwd, and provider
- Rename or delete sessions
- Bulk select and bulk delete sessions
- Filter and remove obvious junk sessions
- Hide subagent sessions by default
- Export a static HTML snapshot
- Pick a past session and run `codex resume <id>`
- Edit Codex API profiles in a local web UI
- Switch profiles with `cswitch`

- 在网页中浏览本地 Codex 历史
- 按标题、提问、回答、工作目录、provider 搜索
- 重命名或删除单条会话
- 批量选择、批量删除会话
- 筛选并清理明显的垃圾会话
- 默认隐藏 subagent 对话
- 导出静态 HTML 历史页
- 从历史中选择会话并执行 `codex resume <id>`
- 在网页中编辑 Codex API profiles
- 使用 `cswitch` 切换 profile

## Quick Install / 快速安装

### Option 1: `pipx` (recommended) / 方式一：`pipx`（推荐）

Install from a local checkout:

从本地源码目录安装：

```bash
pipx install /absolute/path/to/codex-helper
```

Reinstall after local changes:

本地改完后重新安装：

```bash
pipx reinstall /absolute/path/to/codex-helper
```

If `pipx` is not installed yet:

如果你还没有安装 `pipx`：

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

Restart your shell afterwards.

之后重开终端即可。

### Option 2: `install.sh` / 方式二：`install.sh`

Install directly from a local checkout:

直接从本地源码目录安装：

```bash
bash /absolute/path/to/codex-helper/install.sh
```

This writes:

它会安装这些文件：

- `~/.local/bin/codex-history`
- `~/.local/bin/codex-profiles`
- `~/.local/bin/cswitch`
- `~/.local/share/codex-history/codex_history.py`

## Remote Install / 远程安装

### `pipx` from a wheel

```bash
pipx install https://YOUR-DOMAIN/codex_history-0.1.0-py3-none-any.whl
```

### `curl | bash`

Host `install.sh` and `src/codex_history/cli.py`, then:

把 `install.sh` 和 `src/codex_history/cli.py` 发布出去后，可以这样安装：

```bash
curl -fsSL https://YOUR-DOMAIN/install.sh | \
  CODEX_HISTORY_CLI_URL=https://YOUR-DOMAIN/codex_history/cli.py bash
```

## Usage / 使用方法

### Start history web UI / 启动历史网页

```bash
codex-history --serve
```

or simply:

也可以直接：

```bash
codex-history
```

### Resume from history / 从历史恢复会话

Interactive picker:

交互式选择：

```bash
codex-history resume
```

Preview only:

只打印，不直接恢复：

```bash
codex-history resume --print-only --limit 20
```

Include subagent sessions:

包含 subagent：

```bash
codex-history resume --include-subagents
```

### Useful history flags / 常用历史参数

```bash
codex-history --no-open
codex-history --port 9876
codex-history --build
codex-history --reindex
```

Static export output:

静态导出默认输出位置：

```text
~/.codex/memories/shared_history/index.html
```

### Start profile editor / 启动 profile 编辑器

```bash
codex-profiles
```

```bash
codex-profiles --no-open
codex-profiles --port 8766
```

### Switch profiles / 切换 profiles

```bash
cswitch status
cswitch list
cswitch switch
cswitch set tokenflux
```

## Web UI Notes / 网页说明

The history page supports:

历史页支持：

- rename single session
- delete single session
- select visible sessions
- select junk sessions
- bulk delete selected sessions
- bulk delete junk sessions
- hide subagent sessions by default
- show a small `run codex resume <session_id>` hint inside each expanded session

- 单条重命名
- 单条删除
- 选择当前可见会话
- 选择垃圾会话
- 批量删除选中会话
- 批量删除垃圾会话
- 默认隐藏 subagent 会话
- 每条展开后显示一行小字提示：`run codex resume <session_id>`

Current junk-session heuristics:

当前垃圾会话判定规则：

- subagent session
- fewer than 3 user turns
- no proper `assistant:final_answer`
- older than 90 days

- subagent 会话
- 用户轮次少于 3
- 没有正常的 `assistant:final_answer`
- 超过 90 天

## Project Layout / 项目结构

- `src/codex_history/cli.py`: main application
- `install.sh`: lightweight installer for non-`pipx` installs
- `pyproject.toml`: Python package metadata and CLI entry points

- `src/codex_history/cli.py`：主程序
- `install.sh`：非 `pipx` 安装方式的轻量安装脚本
- `pyproject.toml`：Python 打包元数据和命令入口

## Build / 构建

Build a wheel locally:

本地构建 wheel：

```bash
python3 -m pip install --user build
cd /absolute/path/to/codex-helper
python3 -m build
```

Artifacts will be written to:

构建产物会输出到：

```text
dist/
```
