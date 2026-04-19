"""
cli.py — Command-line interface for yad2-watcher.

Commands:
  run          Run a single scan pass (used by launchd)
  watch        Run in an infinite loop with configurable interval
  get-chat-id  Helper to find your Telegram chat_id
  status       Show recent run stats from the DB
  test-notify  Send a test message to verify Telegram is working
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

import os
import click
import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import print as rprint

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


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
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
    _setup_logging(verbose)


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
def watch(ctx: click.Context, interval: Optional[int]) -> None:
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
                rprint(f"[dim]⟳ Scanning...[/dim]")
                summary = watcher.run_once()
                total_new = sum(summary.values())
                if total_new:
                    rprint(f"[green]✓ {total_new} new listing(s) sent[/green]")
                else:
                    rprint(f"[dim]✓ No new listings[/dim]")
                rprint(f"[dim]Next scan in {poll_minutes} minutes...[/dim]\n")
                time.sleep(poll_seconds)
            except KeyboardInterrupt:
                rprint("\n[yellow]Stopped.[/yellow]")
                break
            except Exception as exc:
                logging.exception("Unexpected error during scan: %s", exc)
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
    console.print("\n[bold]→ Copy the chat_id above and add it to config.yaml under [cyan]telegram.chat_ids[/cyan][/bold]")


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
        console.print(
            "[yellow]chat_ids is not set.[/yellow] Run [bold]get-chat-id[/bold] first."
        )
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


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
