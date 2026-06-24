# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
poetry install

# Run a single scan pass
poetry run yad2-watcher run

# Run tests
poetry run pytest tests/ -v

# Run a single test file
poetry run pytest tests/test_fetcher.py -v

# Lint
poetry run ruff check src/ tests/

# Type-check
poetry run pyrefly check

# Autoformat / fix lint
poetry run ruff check --fix src/ tests/
```

## Architecture

The app is a Python CLI tool that polls Yad2 for new rental listings and sends Telegram alerts. Data flow is linear:

```
fetcher.py → watcher.py → notifier.py (Telegram)
                       → journal.py  (Markdown files)
                       → store.py    (SQLite dedup)
```

**`fetcher.py`** — No headless browser. Parses the `__NEXT_DATA__` JSON embedded in Yad2's Next.js HTML. Uses specific browser-mimicking headers to avoid ShieldSquare CAPTCHA (bare `requests.get` gets blocked). Also calls `gw.yad2.co.il/realestate-item/{token}/customer` for phone numbers.

**`store.py`** — SQLite at `~/.yad2_watcher/seen.db`. Tracks `seen_tokens`, `price_history`, and `run_log`. The key logic in `watcher.py` calls `get_price_history(token)` — returns `None` for brand-new listings, or a list for price-changed ones. Alerts fire for both cases.

**`watcher.py`** — `Watcher.run_once()` iterates neighborhoods, fetches listings, finds new/price-changed ones, fetches phone, sends photo alert (falls back to text if no image), journals, and marks seen. Critically, `mark_seen()` and `journal.append()` are called in a `finally` block so a Telegram failure never causes re-alerting.

**`notifier.py`** — Calls Telegram Bot API directly (`sendPhoto` with caption, falling back to `sendMessage`). Broadcasts to all `chat_ids` in config. Caption is capped at 1024 chars (Telegram limit).

**`journal.py`** — Appends Markdown entries to `~/.yad2_watcher/journal/journal_YYYY-MM.md`. Uses `#neighborhood/<name>` Obsidian tags on headings for cross-file filtering. `NullJournal` is swapped in when journaling is disabled.

**`cli.py`** — Click group. `DEFAULT_CONFIG` is resolved relative to the package root (not CWD), so `poetry run yad2-watcher` always finds `config.yaml` at the repo root. `YAD2_BOT_TOKEN` is loaded from `.env` and injected into config — never read from `config.yaml`.

## Secrets and config

- `YAD2_BOT_TOKEN` must be in `.env` at the project root (never in `config.yaml`)
- `config.yaml` holds neighborhoods, chat IDs, search filters, and watcher settings
- The SQLite DB and journal both live under `~/.yad2_watcher/` by default (configurable in `config.yaml`)

## macOS launchd

`com.yad2.watcher.plist` schedules `yad2-watcher run` at the interval configured in `config.yaml`. Install to `~/Library/LaunchAgents/` and bootstrap with `launchctl`. Logs go to `logs/watcher.log` (rotating, capped by `max_log_size_mb`).

## Tests

All 90+ tests are fully mocked — no network calls, no real DB (uses `tmp_path` fixtures). `tests/conftest.py` has shared fixtures including a sample `__NEXT_DATA__` blob. Tests use `pytest-mock` (`mocker` fixture) throughout.
