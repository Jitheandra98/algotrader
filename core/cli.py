"""Operator CLI.

Lightweight commands to validate the foundation and (in later milestones) to start the
data ingestion, strategy engine, and execution daemons. Built with Typer.
"""

from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console
from rich.table import Table

from core.bus import RedisStreamBus
from core.config import load_settings
from core.logging import configure_logging, get_logger
from core.secrets import EnvSecretProvider

app = typer.Typer(no_args_is_help=True, help="Algotrader operator CLI.")
console = Console()
log = get_logger("cli")


@app.command()
def version() -> None:
    """Print version and environment summary."""
    settings = load_settings()
    table = Table(title="algotrader")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("env", settings.env)
    table.add_row("live (global)", str(settings.live))
    table.add_row("broker", f"{settings.broker.name} (live={settings.broker.live})")
    table.add_row("redis", settings.redis.url)
    table.add_row("db", settings.database.url.split("@")[-1])
    table.add_row("log level", settings.logging.level)
    table.add_row("log format", settings.logging.format)
    console.print(table)


@app.command()
def doctor() -> None:
    """Probe Redis and Postgres connectivity and print results."""
    settings = load_settings()
    configure_logging(settings.logging.level, settings.logging.format)

    async def _check() -> int:
        ok = True

        # Redis
        bus = RedisStreamBus(settings.redis.url)
        try:
            await bus.connect()
            console.print("[green]✓[/] redis reachable")
        except Exception as exc:  # noqa: BLE001 — we want to report any failure mode
            console.print(f"[red]✗[/] redis: {exc}")
            ok = False
        finally:
            await bus.close()

        # DB — import lazily so docker-less environments still pass --help
        try:
            from persistence.db import Database

            db = Database(settings.database.url)
            await db.connect()
            async with db.acquire() as conn:
                version_row = await conn.fetchval("SELECT version()")
            await db.close()
            console.print(f"[green]✓[/] postgres reachable: {version_row.split(',')[0]}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]✗[/] postgres: {exc}")
            ok = False

        # Secrets
        sp = EnvSecretProvider()
        if sp.get_optional("ALPACA_API_KEY") is None:
            console.print("[yellow]![/] ALPACA_API_KEY not set (paper trading still works for M1)")
        else:
            console.print("[green]✓[/] ALPACA_API_KEY present")

        return 0 if ok else 1

    exit_code = asyncio.run(_check())
    sys.exit(exit_code)


@app.command()
def gates(strategy: str = typer.Argument(..., help="Strategy name")) -> None:
    """Show whether the given strategy is permitted to place real-money trades."""
    settings = load_settings()
    allowed = settings.is_live_strategy(strategy)
    console.print(
        f"strategy=[bold]{strategy}[/]  live-permitted=[{'red' if allowed else 'green'}]{allowed}[/]"
    )
    console.print(
        f"global LIVE={settings.live}  broker.live={settings.broker.live}"
        f"  strategy.live={next((s.live for s in settings.strategies if s.name == strategy), 'N/A')}"
    )


@app.command()
def ingest(
    symbols: list[str] = typer.Option(..., "--symbol", "-s", help="Symbol(s) to ingest"),
    asset_class: str = typer.Option("ETF", "--asset-class", "-a"),
    timeframe: str = typer.Option("1m", "--timeframe", "-t"),
    source: str = typer.Option("alpaca", "--source", help="alpaca | fixture"),
    fixture_path: str | None = typer.Option(None, "--fixture-path"),
) -> None:
    """Run the ingestion pipeline. Tick → bus → time-bar aggregator → bus."""
    from core.schemas import AssetClass, BarTimeframe, Instrument
    from data.ingestion import run_ingestion

    settings = load_settings()
    configure_logging(settings.logging.level, settings.logging.format)

    instruments = [Instrument(symbol=s, asset_class=AssetClass(asset_class)) for s in symbols]
    tf = BarTimeframe(timeframe)

    bus = RedisStreamBus(settings.redis.url, stream_max_len=settings.redis.stream_max_len)

    # Lazy adapter imports so we don't drag in httpx/websockets unless needed.
    if source == "alpaca":
        from data.adapters.alpaca import AlpacaAdapter
        adapter = AlpacaAdapter()
    elif source == "fixture":
        if not fixture_path:
            console.print("[red]--fixture-path required for source=fixture[/]")
            sys.exit(2)
        from data.adapters.fixture import FixtureAdapter
        adapter = FixtureAdapter(fixture_path)
    else:
        console.print(f"[red]unknown source: {source}[/]")
        sys.exit(2)

    console.print(f"Starting ingestion: source={source}  instruments={symbols}  tf={tf.value}")
    try:
        asyncio.run(run_ingestion(adapter, bus, instruments, timeframes=(tf,)))
    except KeyboardInterrupt:
        console.print("\nstopped")


if __name__ == "__main__":
    app()
