"""
cli.py — Command-line interface for yad2-watcher.

Commands:
  run               Run a single scan pass (used by launchd)
  watch             Run in an infinite loop with configurable interval
  get-chat-id       Helper to find your Telegram chat_id
  status            Show recent run stats from the DB
  test-notify       Send a test message to verify Telegram is working
  bootstrap-cookies Open a real browser to seed session cookies (one-time CAPTCHA fix)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import click
from curl_cffi import requests  # type: ignore[import-untyped]
import yaml
from dotenv import load_dotenv
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from .fetcher import fetch_item_customer, fetch_item_data, fetch_single_listing
from .notifier import TelegramNotifier
from .store import SeenStore
from .watcher import Watcher

console = Console()

DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "config.yaml"


def _load_config(config_path: Path) -> dict:
    # Load .env from the project root (same dir as config.yaml, or CWD)
    env_file = config_path.parent / ".env"
    load_dotenv(dotenv_path=env_file, override=False)

    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Inject bot_token from environment — never from config.yaml
    bot_token = os.environ.get("YAD2_BOT_TOKEN", "")
    if not bot_token:
        console.print(
            "[red]YAD2_BOT_TOKEN is not set.[/red]\n"
            "Add it to [bold].env[/bold]:\n"
            "  YAD2_BOT_TOKEN=<your-token>"
        )
        sys.exit(1)
    config.setdefault("telegram", {})["bot_token"] = bot_token

    return config


def _setup_logging(
    verbose: bool, log_path_str: str = "~/.yad2_watcher/logs", max_log_size_mb: int = 5
) -> None:
    import logging.handlers

    level = logging.DEBUG if verbose else logging.INFO

    log_dir = Path(log_path_str).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            log_dir / "watcher.log",
            maxBytes=max_log_size_mb * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ]

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


@click.group()
@click.option(
    "--config",
    "-c",
    default=str(DEFAULT_CONFIG),
    show_default=True,
    help="Path to config.yaml",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, config: str, verbose: bool) -> None:
    """🏠 Yad2 apartment listing watcher — get Telegram alerts for new listings."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config)
    ctx.obj["verbose"] = verbose

    # Try to extract log_dir from config for early logging setup
    log_dir = "~/.yad2_watcher/logs"
    max_log_size_mb = 5
    try:
        if Path(config).exists():
            with open(config) as f:
                cfg = yaml.safe_load(f)
                if cfg and "watcher" in cfg:
                    if "log_dir" in cfg["watcher"]:
                        log_dir = cfg["watcher"]["log_dir"]
                    if "max_log_size_mb" in cfg["watcher"]:
                        max_log_size_mb = cfg["watcher"]["max_log_size_mb"]
    except Exception:
        pass

    _setup_logging(verbose, log_path_str=log_dir, max_log_size_mb=max_log_size_mb)


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run a single scan pass across all configured neighborhoods."""
    config = _load_config(ctx.obj["config_path"])

    if not config.get("telegram", {}).get("chat_ids"):
        console.print(
            "[yellow]⚠ chat_ids is not set in config.yaml.[/yellow]\n"
            "Run [bold]yad2-watcher get-chat-id[/bold] after messaging @yad2_jlm_bot."
        )
        sys.exit(1)

    with Watcher(config) as watcher:
        summary = watcher.run_once()

    total_new = sum(summary.values())
    if total_new:
        rprint(f"[green]✓ Done. {total_new} new listing(s) found and sent.[/green]")
    else:
        rprint("[dim]✓ Done. No new listings this pass.[/dim]")

    for name, count in summary.items():
        rprint(f"  [bold]{name}[/bold]: {count} new")


@cli.command()
@click.option(
    "--interval",
    "-i",
    default=None,
    type=int,
    help="Override poll interval in minutes (default: from config.yaml)",
)
@click.pass_context
def watch(ctx: click.Context, interval: int | None) -> None:
    """Run continuously, polling every N minutes. Ctrl+C to stop."""
    config = _load_config(ctx.obj["config_path"])

    if not config.get("telegram", {}).get("chat_ids"):
        console.print(
            "[yellow]⚠ chat_ids is not set in config.yaml.[/yellow]\n"
            "Run [bold]yad2-watcher get-chat-id[/bold] after messaging @yad2_jlm_bot."
        )
        sys.exit(1)

    poll_minutes = interval or config.get("watcher", {}).get("interval_minutes", 30)
    poll_seconds = poll_minutes * 60

    rprint(f"[bold green]🏠 Yad2 Watcher started[/bold green] — polling every {poll_minutes} min")
    rprint(f"Neighborhoods: {len(config.get('neighborhoods', []))}")
    rprint("Press Ctrl+C to stop.\n")

    with Watcher(config) as watcher:
        while True:
            try:
                rprint("[dim]⟳ Scanning...[/dim]")
                summary = watcher.run_once()
                total_new = sum(summary.values())
                if total_new:
                    rprint(f"[green]✓ {total_new} new listing(s) sent[/green]")
                else:
                    rprint("[dim]✓ No new listings[/dim]")
                rprint(f"[dim]Next scan in {poll_minutes} minutes...[/dim]\n")
                time.sleep(poll_seconds)
            except KeyboardInterrupt:
                rprint("\n[yellow]Stopped.[/yellow]")
                break
            except Exception as exc:
                logging.exception("Unexpected error during scan: %s", exc)
                watcher._notifier.send_error(f"Unexpected error during continuous scan: `{exc}`")
                rprint(f"[red]Error: {exc}[/red]. Retrying in {poll_minutes} min...")
                time.sleep(poll_seconds)


@cli.command("get-chat-id")
@click.pass_context
def get_chat_id(ctx: click.Context) -> None:
    """Print your Telegram chat_id. Send any message to @yad2_jlm_bot first."""
    config = _load_config(ctx.obj["config_path"])
    bot_token = config["telegram"]["bot_token"]  # guaranteed by _load_config

    console.print("Fetching updates from Telegram...")
    chats = TelegramNotifier.get_chat_id(bot_token)

    if not chats:
        console.print(
            "[yellow]No messages found.[/yellow]\n"
            "Please send any message to @yad2_jlm_bot in Telegram, then run this command again."
        )
        return

    table = Table(title="Telegram Chats Found")
    table.add_column("chat_id", style="bold cyan")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("Type")

    seen_ids: set[str] = set()
    for chat in chats:
        cid = chat["chat_id"]
        if cid not in seen_ids:
            table.add_row(cid, chat["name"].strip(), chat.get("username", ""), chat["type"])
            seen_ids.add(cid)

    console.print(table)
    console.print(
        "\n[bold]→ Copy the chat_id above and add it to config.yaml under [cyan]telegram.chat_ids[/cyan][/bold]"  # noqa: E501
    )


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show recent watcher run statistics from the database."""
    config = _load_config(ctx.obj["config_path"])
    db_path = config.get("watcher", {}).get("db_path", "~/.yad2_watcher/seen.db")

    with SeenStore(db_path) as store:
        stats = store.stats()

    rprint(f"[bold]Total seen listings:[/bold] {stats['total_seen']}")
    rprint(f"[bold]Total runs logged:[/bold] {stats['total_runs']}")
    rprint()

    if not stats["recent_runs"]:
        rprint("[dim]No runs recorded yet.[/dim]")
        return

    table = Table(title="Recent Runs (last 10)")
    table.add_column("Time", style="dim")
    table.add_column("Neighborhood ID")
    table.add_column("Fetched", justify="right")
    table.add_column("New", justify="right", style="green")
    table.add_column("Error", style="red")

    for run in stats["recent_runs"]:
        table.add_row(
            run["run_at"],
            str(run["neighborhood_id"]),
            str(run["fetched"]),
            str(run["new"]),
            run["error"] or "",
        )

    console.print(table)


@cli.command("test-notify")
@click.pass_context
def test_notify(ctx: click.Context) -> None:
    """Send a test Telegram message to verify the bot is configured correctly."""
    config = _load_config(ctx.obj["config_path"])
    telegram_cfg = config.get("telegram", {})
    bot_token = telegram_cfg["bot_token"]  # guaranteed by _load_config
    chat_ids = [str(c) for c in telegram_cfg.get("chat_ids", [])]

    if not chat_ids:
        console.print("[yellow]chat_ids is not set.[/yellow] Run [bold]get-chat-id[/bold] first.")
        sys.exit(1)

    notifier = TelegramNotifier(bot_token, chat_ids)
    console.print(f"Sending test message to {len(chat_ids)} chat(s): {', '.join(chat_ids)}...")
    success = notifier.send_text(
        "🏠 *Yad2 Watcher* — בדיקת חיבור\n\nהבוט עובד! תקבל כאן התראות על דירות חדשות. ✅"
    )
    if success:
        console.print("[green]✓ Test message sent successfully![/green]")
    else:
        console.print("[red]✗ Failed to send test message. Check the logs.[/red]")
        sys.exit(1)


@cli.command("send-listing")
@click.argument("link_or_token")
@click.pass_context
def send_listing_cmd(ctx: click.Context, link_or_token: str) -> None:
    """Fetch a Yad2 listing by URL or token and send it as a Telegram alert."""
    config = _load_config(ctx.obj["config_path"])
    telegram_cfg = config.get("telegram", {})
    bot_token = telegram_cfg["bot_token"]
    chat_ids = [str(c) for c in telegram_cfg.get("chat_ids", [])]

    if not chat_ids:
        console.print("[yellow]chat_ids is not set.[/yellow] Run [bold]get-chat-id[/bold] first.")
        sys.exit(1)

    token = link_or_token
    if "http" in token or "yad2" in token:
        parsed = urlparse(token)
        token = parsed.path.rstrip("/").split("/")[-1]

    if not token:
        console.print("[red]✗ Could not extract a valid token from the input.[/red]")
        sys.exit(1)

    console.print(f"[dim]Fetching listing [bold]{token}[/bold]...[/dim]")
    try:
        listing = fetch_single_listing(token)
    except Exception as e:
        console.print(f"[red]✗ Failed to fetch listing: {e}[/red]")
        sys.exit(1)

    listing.phone = fetch_item_customer(token)

    notifier = TelegramNotifier(bot_token, chat_ids)
    console.print(f"Sending to {len(chat_ids)} chat(s)...")
    success = notifier.send_photo(listing)
    if success:
        console.print("[green]✓ Listing sent successfully![/green]")
    else:
        console.print("[red]✗ Failed to send listing. Check the logs.[/red]")
        sys.exit(1)


@cli.command("download")
@click.argument("link_or_token")
def download_apartment(link_or_token: str) -> None:
    """Download apartment details and photos by Yad2 URL or token."""

    # Extract token
    token = link_or_token
    if "http" in token or "yad2.co.il" in token:
        parsed = urlparse(token)
        token = parsed.path.rstrip("/").split("/")[-1]

    if not token:
        console.print("[red]✗ Could not extract a valid token from the input.[/red]")
        sys.exit(1)

    console.print(f"[dim]Fetching data for apartment [bold]{token}[/bold]...[/dim]")
    try:
        data = fetch_item_data(token)
    except Exception as e:
        console.print(f"[red]✗ Failed to fetch data: {e}[/red]")
        sys.exit(1)

    out_dir = Path("downloads") / token
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save raw JSON
    json_path = out_dir / "details.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Save a human-readable markdown summary
    md_path = out_dir / "summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        meta = data.get("metaData", {})
        addr = data.get("address", {})
        addr_parts = [
            addr.get("street", {}).get("text", ""),
            str(addr.get("house", {}).get("number", "")),
            addr.get("neighborhood", {}).get("text", ""),
            addr.get("city", {}).get("text", ""),
        ]
        addr_str = ", ".join(filter(bool, addr_parts))

        f.write(f"# Apartment Details: {token}\n\n")
        f.write(f"- **Price:** {data.get('price', 'N/A')} ₪\n")
        f.write(f"- **Address:** {addr_str}\n\n")

        f.write("## Description\n\n")
        f.write(meta.get("description", "No description provided.") + "\n")

    # Download images
    images = data.get("metaData", {}).get("images", [])
    if images:
        console.print(f"Downloading {len(images)} photo(s) to [bold]{out_dir}[/bold]...")
        for i, img_url in enumerate(images, 1):
            try:
                resp = requests.get(img_url, stream=True, timeout=10)
                resp.raise_for_status()
                # Try to preserve original extension if possible
                ext = ".jpg"
                if "jpeg" in img_url.lower():
                    ext = ".jpeg"
                elif "png" in img_url.lower():
                    ext = ".png"
                elif "webp" in img_url.lower():
                    ext = ".webp"

                img_path = out_dir / f"photo_{i:02d}{ext}"
                with open(img_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
            except Exception as e:
                console.print(f"[yellow]⚠ Failed to download image {img_url}: {e}[/yellow]")
    else:
        console.print("[dim]No photos found for this listing.[/dim]")

    console.print(f"[green]✓ Done! All files saved in [bold]{out_dir.absolute()}[/bold][/green]")


@cli.command("bootstrap-cookies")
@click.option(
    "--url",
    default="https://www.yad2.co.il/realestate/rent/jerusalem-area?area=7&city=3000&neighborhood=561",
    show_default=True,
    help="Yad2 page to open in the browser.",
)
@click.pass_context
def bootstrap_cookies(ctx: click.Context, url: str) -> None:
    """
    Open a stealth Chromium window to earn ShieldSquare trust cookies.

    Run this once when the watcher is CAPTCHA-blocked. A browser window opens
    at Yad2. The Radware JS challenge runs automatically. Once the page loads
    normally (you can see listings), press Enter — cookies are saved and reused.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        console.print(
            "[red]playwright is not installed.[/red]\n"
            "Run: [bold]poetry run playwright install chromium[/bold]"
        )
        sys.exit(1)

    # Resolve cookies destination
    cookies_path_raw = "~/.yad2_watcher/cookies.json"
    try:
        config_path = ctx.obj["config_path"]
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            cookies_path_raw = cfg.get("watcher", {}).get("cookies_path", cookies_path_raw)
    except Exception:
        pass
    cookies_path = Path(os.path.expanduser(cookies_path_raw))

    # Persistent user-data dir so the browser accumulates trust across runs
    user_data_dir = str(Path(os.path.expanduser("~/.yad2_watcher/browser_profile")))

    # Inline stealth patches — covers the main properties Radware/ShieldSquare inspect
    _STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { app: { isInstalled: false }, runtime: {}, csi: function(){}, loadTimes: function(){} };
const _origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
  p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : _origQuery(p);
Object.defineProperty(navigator, 'plugins', { get: () => {
  const a = [
    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
  ];
  a.__proto__ = PluginArray.prototype; return a;
}});
Object.defineProperty(navigator, 'languages', { get: () => ['he-IL','he','en-US','en'] });
const _getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
  if (p === 37445) return 'Intel Inc.';
  if (p === 37446) return 'Intel Iris OpenGL Engine';
  return _getParam.call(this, p);
};
"""

    console.print("[bold cyan]Opening stealth Chromium window...[/bold cyan]")
    console.print(
        "\n[bold yellow]What to do:[/bold yellow]\n"
        " 1. A browser window will open at Yad2.\n"
        " 2. If listings load normally — great, press Enter.\n"
        " 3. If a spinner / CAPTCHA appears, wait ~10s for it to auto-complete.\n"
        "    If still stuck, press Ctrl+R once to refresh.\n"
        " 4. Once you can see rental listings, press [bold]Enter[/bold] here.\n"
    )

    with sync_playwright() as p:
        # launch_persistent_context has fewer automation signals than new_context
        context = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
                "--lang=he-IL",
            ],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="he-IL",
            viewport={"width": 1280, "height": 900},
            timezone_id="Asia/Jerusalem",
        )
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            console.print(f"[yellow]Page load timeout: {exc}[/yellow] (window still open)")

        input("\n>>> Press Enter once you can see Yad2 listings... ")

        raw_cookies = context.cookies()
        context.close()

    if not raw_cookies:
        console.print("[red]No cookies found. Did the page load? Try again.[/red]")
        sys.exit(1)

    uzm_cookies = [c for c in raw_cookies if c["name"].startswith(("__uzm", "__ss", "__uzmaj"))]
    if not uzm_cookies:
        console.print(
            "[yellow]ShieldSquare cookies (__uzm*) not found yet.[/yellow]\n"
            "The challenge may not have resolved. Try running again and wait longer."
        )

    cookies_dict = {c["name"]: c["value"] for c in raw_cookies}
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    cookies_path.write_text(json.dumps(cookies_dict, ensure_ascii=False))

    console.print(
        f"[green]Saved {len(cookies_dict)} cookies -> {cookies_path}[/green]\n"
        f"[dim]ShieldSquare (__uzm*) cookies captured: {len(uzm_cookies)}[/dim]\n"
        "[dim]Run [bold]yad2-watcher run[/bold] to test.[/dim]"
    )



@cli.command("import-cookies")
@click.argument("cookie_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def import_cookies(ctx: click.Context, cookie_file: str) -> None:
    """
    Import cookies from a browser export file into the watcher session.

    COOKIE_FILE should be a JSON file exported by the "Cookie Editor" Chrome
    extension (https://cookie-editor.com). Supported formats:

    \b
    - Array:  [{\"name\": \"...\", \"value\": \"...\", ...}, ...]   ← Cookie Editor default
    - Flat:   {\"name\": \"value\", ...}                          ← watcher's own format

    Steps:
    \b
    1. Install "Cookie Editor" from the Chrome Web Store.
    2. Visit https://www.yad2.co.il in Chrome (complete any CAPTCHA if shown).
    3. Click the Cookie Editor icon → Export → Export as JSON.
       (It copies JSON to your clipboard.)
    4. Paste into a file:  pbpaste > /tmp/yad2_cookies.json
    5. Run:  poetry run yad2-watcher import-cookies /tmp/yad2_cookies.json
    """
    src = Path(cookie_file)
    try:
        raw = json.loads(src.read_text())
    except Exception as exc:
        console.print(f"[red]✗ Could not read {src}: {exc}[/red]")
        sys.exit(1)

    # Normalise to a list of {name, value, domain} objects (new domain-aware format).
    _BLOCKED_DOMAINS = ("perfdrive.com", "shieldsquare")

    if isinstance(raw, list):
        # Standard Cookie Editor export — array of browser cookie objects.
        cookie_list = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".yad2.co.il"),
            }
            for c in raw
            if "name" in c and "value" in c
            and not any(b in c.get("domain", "") for b in _BLOCKED_DOMAINS)
        ]
    elif isinstance(raw, dict):
        # Legacy flat {name: value} format — assume .yad2.co.il domain.
        cookie_list = [
            {"name": k, "value": str(v), "domain": ".yad2.co.il"}
            for k, v in raw.items()
        ]
    else:
        console.print("[red]✗ Unrecognised cookie format. Expected a JSON array or object.[/red]")
        sys.exit(1)

    if not cookie_list:
        console.print("[red]✗ No cookies found in file.[/red]")
        sys.exit(1)

    # Resolve destination from config
    cookies_path_raw = "~/.yad2_watcher/cookies.json"
    try:
        config_path = ctx.obj["config_path"]
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            cookies_path_raw = cfg.get("watcher", {}).get("cookies_path", cookies_path_raw)
    except Exception:
        pass
    cookies_path = Path(os.path.expanduser(cookies_path_raw))
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    cookies_path.write_text(json.dumps(cookie_list, ensure_ascii=False))

    console.print(
        f"[green]✓ Imported {len(cookie_list)} cookies → {cookies_path}[/green]\n"
        "[dim]The watcher will reuse these cookies on the next run.[/dim]"
    )


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
