"""FastAPI operator API + minimal HTML UI.

Exposes the running bot's state for operators: live equity, positions, signals,
risk-audit log, recent fills, plus the Prometheus scrape endpoint, kill-switch
controls, and a WebSocket stream for the UI.

Designed so :class:`OperatorState` can be injected from the live engine (M7) or
a backtest result (M4) — the same UI works against both.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.logging import get_logger
from core.schemas import Fill, Position, Signal
from monitoring.health import HealthMonitor
from monitoring.metrics import MetricsRegistry
from risk import KillSwitch, RiskDecision

log = get_logger(__name__)

UTC = timezone.utc


@dataclass
class OperatorState:
    """The slice of bot state the API exposes. Injected by the live engine or
    backtest harness."""

    portfolio_provider: Callable[[], dict[str, Any]]
    positions_provider: Callable[[], list[Position]]
    signals_provider: Callable[[int], list[Signal]]
    fills_provider: Callable[[int], list[Fill]]
    risk_audit_provider: Callable[[int], list[RiskDecision]]
    strategies_provider: Callable[[], list[dict[str, Any]]] = field(
        default_factory=lambda: lambda: []
    )
    kill_switch: KillSwitch | None = None
    health_monitor: HealthMonitor | None = None
    metrics: MetricsRegistry | None = None


def _serialize(obj: Any) -> Any:
    """Recursive JSON-friendly conversion for our schemas."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def create_app(state: OperatorState, *, ui_path: Path | None = None) -> FastAPI:
    """Build a FastAPI app bound to the given operator state. Multiple instances
    are safe — state is per-app and isolated."""
    app = FastAPI(title="algotrader operator", version="0.1")

    @app.get("/")
    async def index() -> HTMLResponse:
        ui_html = (ui_path or _default_ui_path()).read_text()
        return HTMLResponse(ui_html)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        if state.health_monitor is None:
            return JSONResponse({"healthy": True, "checks": {}, "note": "no health monitor"})
        results = state.health_monitor.run_all()
        return JSONResponse(
            {
                "healthy": all(s.healthy for s in results.values()),
                "checks": {
                    name: {
                        "healthy": s.healthy,
                        "detail": s.detail,
                        "last_checked": s.last_checked.isoformat(),
                        "metadata": s.metadata,
                    }
                    for name, s in results.items()
                },
            }
        )

    @app.get("/api/portfolio")
    async def portfolio() -> JSONResponse:
        return JSONResponse(_serialize(state.portfolio_provider()))

    @app.get("/api/positions")
    async def positions() -> JSONResponse:
        return JSONResponse(_serialize(state.positions_provider()))

    @app.get("/api/signals")
    async def signals(limit: int = 50) -> JSONResponse:
        return JSONResponse(_serialize(state.signals_provider(limit)))

    @app.get("/api/fills")
    async def fills(limit: int = 50) -> JSONResponse:
        return JSONResponse(_serialize(state.fills_provider(limit)))

    @app.get("/api/risk-audit")
    async def risk_audit(limit: int = 50) -> JSONResponse:
        decisions = state.risk_audit_provider(limit)
        out = []
        for d in decisions:
            out.append(
                {
                    "signal": _serialize(d.signal),
                    "allowed": d.allowed,
                    "order": _serialize(d.order) if d.order else None,
                    "rejections": [
                        {"limit": r.limit, "reason": r.reason, "metadata": r.metadata}
                        for r in d.rejections
                    ],
                    "sizing_meta": d.sizing_meta,
                }
            )
        return JSONResponse(out)

    @app.get("/api/strategies")
    async def strategies() -> JSONResponse:
        return JSONResponse(_serialize(state.strategies_provider()))

    @app.get("/api/kill-switch")
    async def kill_switch_status() -> JSONResponse:
        if state.kill_switch is None:
            raise HTTPException(status_code=503, detail="kill switch not wired")
        return JSONResponse(
            {"tripped": state.kill_switch.is_tripped, "reasons": state.kill_switch.reasons}
        )

    @app.post("/api/kill-switch/trip")
    async def kill_switch_trip(reason: str = "operator manual trip") -> JSONResponse:
        if state.kill_switch is None:
            raise HTTPException(status_code=503, detail="kill switch not wired")
        state.kill_switch.trip(reason)
        return JSONResponse({"tripped": True, "reasons": state.kill_switch.reasons})

    @app.post("/api/kill-switch/reset")
    async def kill_switch_reset() -> JSONResponse:
        if state.kill_switch is None:
            raise HTTPException(status_code=503, detail="kill switch not wired")
        state.kill_switch.reset()
        return JSONResponse({"tripped": False, "reasons": []})

    @app.get("/metrics")
    async def metrics() -> Response:
        if state.metrics is None:
            return Response(content="# metrics not wired\n", media_type="text/plain")
        return Response(content=state.metrics.export(), media_type="text/plain; version=0.0.4")

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                snapshot = {
                    "ts": datetime.now(tz=UTC).isoformat(),
                    "portfolio": _serialize(state.portfolio_provider()),
                    "positions": _serialize(state.positions_provider()),
                    "kill_switch": (
                        {"tripped": state.kill_switch.is_tripped, "reasons": state.kill_switch.reasons}
                        if state.kill_switch is not None
                        else None
                    ),
                }
                await socket.send_json(snapshot)
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return

    return app


def _default_ui_path() -> Path:
    return Path(__file__).parent.parent / "ui" / "index.html"


@dataclass
class OperatorAPI:
    """Convenience wrapper — usually you'd call :func:`create_app` directly."""

    state: OperatorState

    def app(self) -> FastAPI:
        return create_app(self.state)
