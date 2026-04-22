# codex-history

`codex-history` is a small local tool for browsing, searching, renaming, and deleting Codex session history stored under `~/.codex`.

It provides:

- a local web UI for viewing session history
- full-text search over titles and transcript excerpts
- in-place record deletion without page jump
- in-place title renaming
- a static HTML export mode
- a local web UI for editing Codex API profiles (endpoints + keys)
- a `cswitch` command for switching profiles

## What Is `pipx`

`pipx` is a tool for installing Python command-line applications into isolated virtual environments while still exposing the final command globally on your `PATH`.

That means:

- the tool does not pollute your main Python environment
- upgrades and uninstalls are cleaner
- the installed command is still as simple as `codex-history`

If you do not already have `pipx`, install it with:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

Then restart your shell.

## Project Layout

- `src/codex_history/cli.py`: main application
- `install.sh`: simple install script for non-`pipx` installs
- `pyproject.toml`: package metadata and CLI entry point

## Local Install

### Option 1: `pipx`

Install directly from the local checkout:

```bash
pipx install /Users/liiilhhh/Downloads/codex-history
```

Upgrade after local changes:

```bash
pipx reinstall /Users/liiilhhh/Downloads/codex-history
```

### Option 2: install script

Install from the local checkout without `pipx`:

```bash
bash /Users/liiilhhh/Downloads/codex-history/install.sh
```

This copies the tool to `~/.local/share/codex-history/` and writes a wrapper to `~/.local/bin/codex-history`.

It also installs:

- `~/.local/bin/cswitch`
- `~/.local/bin/codex-profiles`

## Usage

Start the local history UI:

```bash
codex-history
```

Start the local profile editor UI:

```bash
codex-profiles
```

Switch Codex profiles (endpoint + key) using `~/.codex/cswitch_profiles.json`:

```bash
cswitch status
cswitch list
cswitch switch
cswitch set tokenflux
```

Useful flags:

```bash
codex-history --no-open
codex-history --port 9876
codex-history --build
codex-history --reindex
codex-history resume
codex-history resume --print-only --limit 20
```

For `codex-profiles`:

```bash
codex-profiles --no-open
codex-profiles --port 8766
```

Static export goes to:

```text
~/.codex/memories/shared_history/index.html
```

## Build A Wheel

Install build backend once:

```bash
python3 -m pip install --user build
```

Build source and wheel distributions:

```bash
cd /Users/liiilhhh/Downloads/codex-history
python3 -m build
```

The artifacts will appear under:

```text
dist/
```

## Remote Install After You Upload It

Once you host this project, you can support both remote install methods:

### `pipx` from a wheel

```bash
pipx install https://YOUR-DOMAIN/codex-history-0.1.0-py3-none-any.whl
```

### `curl | bash`

Host `install.sh` somewhere reachable, then:

```bash
curl -fsSL https://YOUR-DOMAIN/install.sh | bash
```

For that remote mode, `install.sh` expects the environment variable `CODEX_HISTORY_CLI_URL` to point to a raw downloadable `cli.py` URL unless you customize the script with your own default URL.

Example:

```bash
curl -fsSL https://YOUR-DOMAIN/install.sh | \
  CODEX_HISTORY_CLI_URL=https://YOUR-DOMAIN/codex_history/cli.py bash
```
