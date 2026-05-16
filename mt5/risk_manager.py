"""
Gestor de riesgo por propfirm. Carga las reglas del JSON y decide si el bot
puede abrir un trade, debe reducir el riesgo o suspender el día.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


@dataclass
class PropFirmRules:
    id: str
    name: str
    type: str
    account_size_usd: float
    profit_target_usd: float
    drawdown_mode: str
    drawdown_amount_usd: float
    drawdown_lock_at_usd: Optional[float]
    max_daily_loss_usd: Optional[float]
    consistency_rule_pct: Optional[float]
    min_trading_days: Optional[int]
    max_contracts: Optional[int]
    max_lot_size: Optional[float]
    allow_overnight: bool
    allow_news_trading: bool

    @classmethod
    def from_file(cls, path: str | Path) -> "PropFirmRules":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        dd = data["drawdown"]
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            account_size_usd=float(data["account_size_usd"]),
            profit_target_usd=float(data.get("profit_target_usd") or 0),
            drawdown_mode=dd["mode"],
            drawdown_amount_usd=float(dd["amount_usd"]),
            drawdown_lock_at_usd=(
                float(dd["lock_at_usd"]) if dd.get("lock_at_usd") is not None else None
            ),
            max_daily_loss_usd=(
                float(data["max_daily_loss_usd"])
                if data.get("max_daily_loss_usd") is not None else None
            ),
            consistency_rule_pct=(
                float(data["consistency_rule_pct"])
                if data.get("consistency_rule_pct") is not None else None
            ),
            min_trading_days=data.get("min_trading_days"),
            max_contracts=data.get("max_contracts"),
            max_lot_size=data.get("max_lot_size"),
            allow_overnight=bool(data.get("allow_overnight", False)),
            allow_news_trading=bool(data.get("allow_news_trading", True)),
        )


@dataclass
class AccountSnapshot:
    balance: float
    equity: float
    high_water_mark: float       # mayor equity histórico (para trailing)
    realized_today: float        # P&L cerrado del día actual


class RiskManager:
    """
    Comprueba si una entrada es segura según las reglas de la propfirm.
    Devuelve (allowed, reason). No bloquea entradas ya abiertas — solo decide
    si se puede abrir una nueva o si toca flatten.
    """
    def __init__(self, rules: PropFirmRules):
        self.rules = rules

    def trailing_floor(self, snapshot: AccountSnapshot) -> float:
        """Mínimo de equity permitido según el modo de drawdown."""
        r = self.rules
        if r.drawdown_mode in ("static", "max_loss"):
            return r.account_size_usd - r.drawdown_amount_usd
        if r.drawdown_mode in ("trailing_intraday", "trailing_eod"):
            if r.drawdown_lock_at_usd is not None and snapshot.high_water_mark >= r.drawdown_lock_at_usd:
                return r.account_size_usd  # locked at initial balance
            return snapshot.high_water_mark - r.drawdown_amount_usd
        return r.account_size_usd - r.drawdown_amount_usd

    def can_open_trade(self, snapshot: AccountSnapshot, planned_risk_usd: float) -> tuple[bool, str]:
        r = self.rules

        # Daily loss
        if r.max_daily_loss_usd is not None:
            available_today = r.max_daily_loss_usd + snapshot.realized_today
            if planned_risk_usd > available_today * 0.95:
                return False, f"Daily loss limit: only ${available_today:.0f} buffer; trade arriesga ${planned_risk_usd:.0f}"

        # Overall drawdown
        floor_ = self.trailing_floor(snapshot)
        worst_case_equity = snapshot.equity - planned_risk_usd
        if worst_case_equity < floor_:
            return False, (
                f"Drawdown: floor=${floor_:.0f}, equity actual=${snapshot.equity:.0f}, "
                f"worst-case=${worst_case_equity:.0f}"
            )
        return True, "ok"

    def cap_volume(self, planned_volume: float) -> float:
        """Aplica caps de contratos / lots de la propfirm."""
        r = self.rules
        v = planned_volume
        if r.type == "futures" and r.max_contracts is not None:
            v = min(v, float(r.max_contracts))
        if r.type == "cfd" and r.max_lot_size is not None:
            v = min(v, r.max_lot_size)
        return v
