# Yad2 Apartment Watcher

Automatically monitors Yad2 for new apartment listings and sends Telegram alerts.

## How it works

- Polls configured neighborhood search URLs every 30 minutes
- Extracts listings from Yad2's server-rendered `__NEXT_DATA__` JSON (no headless browser needed)
- Tracks seen listing tokens in a local SQLite DB to avoid duplicate alerts
- Sends formatted Telegram messages to one or more chats with listing details and a direct link

## Setup

### 1. Install dependencies

```bash
cd /Users/ilia/projects/yad2
/opt/homebrew/bin/poetry install
```

### 2. Configure secrets

Create a `.env` file in the project root (never commit this):

```bash
echo "YAD2_BOT_TOKEN=<your-bot-token>" > .env
```

The bot token is loaded automatically from `.env` at startup — do not put it in `config.yaml`.

### 3. Get your Telegram chat IDs

Send any message to [@yad2_jlm_bot](https://t.me/yad2_jlm_bot), then run:

```bash
/opt/homebrew/bin/poetry run yad2-watcher get-chat-id
```

Copy the `chat_id` values and add them to `config.yaml`:

```yaml
telegram:
  chat_ids:
    - "111111111"
    - "222222222"   # add as many recipients as you like
```

### 4. Test the connection

```bash
/opt/homebrew/bin/poetry run yad2-watcher test-notify
```

You should receive a test message in every configured chat.

### 5. Run once manually

```bash
/opt/homebrew/bin/poetry run yad2-watcher run
```

### 6. Enable automatic polling (macOS launchd)

```bash
# Create logs directory
mkdir -p logs

# Install the launchd plist
cp com.yad2.watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.yad2.watcher.plist
```

The watcher will now run every 30 minutes automatically, even after reboots (as long as you're logged in).

To stop it:
```bash
launchctl unload ~/Library/LaunchAgents/com.yad2.watcher.plist
```

To check status:
```bash
launchctl list | grep yad2
```

To view logs:
```bash
tail -f logs/watcher.log
```

### 7. Check run statistics

```bash
/opt/homebrew/bin/poetry run yad2-watcher status
```

## CLI Reference

```
yad2-watcher run              # Single scan pass
yad2-watcher watch            # Continuous loop (for testing)
yad2-watcher watch -i 15      # Loop with custom interval (15 min)
yad2-watcher get-chat-id      # Find your Telegram chat IDs
yad2-watcher test-notify      # Send a test message to all configured chats
yad2-watcher status           # Show DB stats and recent runs
```

## Adding neighborhoods

Edit `config.yaml` and add entries to the `neighborhoods` list:

```yaml
neighborhoods:
  - id: 561
    name: "גבעת הורדים, רסקו"
    url_slug: "jerusalem-area"
  - id: 567
    name: "רחביה"
    url_slug: "jerusalem-area"
```

To find neighborhood IDs: look at the Yad2 search URL — the `neighborhood=` parameter is the ID.

## Running tests

```bash
/opt/homebrew/bin/poetry run pytest tests/ -v
```

72 tests covering fetcher, store, notifier, and watcher — all mocked, no network calls.

## Project structure

```
yad2/
├── .env                           # Secrets — bot token (gitignored)
├── config.yaml                    # Configuration — neighborhoods, filters, chat IDs
├── pyproject.toml                 # Poetry project
├── com.yad2.watcher.plist         # macOS launchd scheduler
├── logs/                          # Runtime logs (gitignored)
├── src/yad2_watcher/
│   ├── fetcher.py                 # HTTP fetch + JSON parsing
│   ├── store.py                   # SQLite dedup store (~/.yad2_watcher/seen.db)
│   ├── notifier.py                # Telegram Bot sender (broadcasts to all chat IDs)
│   ├── watcher.py                 # Main orchestration loop
│   └── cli.py                     # Click CLI
└── tests/
    ├── conftest.py                # Shared fixtures and sample data
    ├── test_fetcher.py            # Fetcher + Listing tests
    ├── test_store.py              # SQLite store tests
    ├── test_notifier.py           # Message formatting + Telegram API tests
    └── test_watcher.py            # Orchestration tests
```
