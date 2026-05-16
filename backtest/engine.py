"""
Motor de backtest. Reproduce la lógica de la estrategia barra por barra y
simula la ejecución de TP, SL, y add-on al 0.4R con SL combinado al BE.

Asunciones de ejecución (configurables):
- Entrada market al cierre de la vela de ruptura.
- Slippage en ticks aplicado en la dirección desfavorable.
- Add-on dispara cuando una vela posterior toca el 0.4R intra-bar (usa high/low).
- TP / SL dentro de la misma vela: si ambos están dentro del rango high/low de
  una vela, se asume el peor caso (SL primero).
- No simula spread bid/ask explícito — usar slippage_ticks como proxy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mt5"))

from strategy import (  # noqa: E402
    Bar5m,
    InstrumentSpec,
    Side,
    StratState,
    StrategyEngine,
    StrategyParams,
    TradePlan,
)


@dataclass
class Trade:
    session_date: str
    side: str                  # "long" / "short"
    range_width: float
    initial_volume: float
    add_on_volume: float
    entry_time: str
    entry_price: float
    sl_initial: float
    tp: float
    add_trigger: float
    add_on_executed: bool
    exit_time: str
    exit_price: float
    exit_reason: str           # "tp" | "sl_initial" | "sl_after_add" | "eod"
    pnl_usd: float


@dataclass
class GapWarning:
    session_date: str
    timestamp: str
    gap_points: float
    prev_close: float
    next_open: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    no_trade_days: int = 0
    total_days: int = 0
    gap_warnings: list[GapWarning] = field(default_factory=list)


@dataclass
class ExecutionConfig:
    slippage_ticks: float = 2.0   # ticks de slippage en cada orden market
    pessimistic_intra_bar: bool = True  # si TP y SL están en la misma vela, asume SL primero
    gap_warning_threshold_pts: float = 50.0  # detecta gaps > este valor (puntos) entre velas consecutivas


def _dollars_per_point_per_lot(spec: InstrumentSpec) -> float:
    return spec.tick_value / spec.tick_size


def run_backtest(
    bars: list[Bar5m],
    params: StrategyParams,
    instrument: InstrumentSpec,
    exec_cfg: ExecutionConfig | None = None,
) -> BacktestResult:
    """
    Itera todas las barras agrupadas por día y simula la estrategia.
    """
    exec_cfg = exec_cfg or ExecutionConfig()
    result = BacktestResult()

    # Detect inter-day gaps (posibles rollovers de contrato)
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(params.timezone)
    for i in range(1, len(bars)):
        prev = bars[i - 1]
        curr = bars[i]
        prev_day = prev.timestamp.astimezone(tz).date()
        curr_day = curr.timestamp.astimezone(tz).date()
        if curr_day != prev_day:
            gap = abs(curr.open - prev.close)
            if gap > exec_cfg.gap_warning_threshold_pts:
                result.gap_warnings.append(GapWarning(
                    session_date=curr_day.isoformat(),
                    timestamp=curr.timestamp.isoformat(),
                    gap_points=gap,
                    prev_close=prev.close,
                    next_open=curr.open,
                ))

    # Agrupa por día en el TZ de la estrategia
    from data_loader import iter_session_days  # local import to avoid cycle
    for session_date, day_bars in iter_session_days(bars, params.timezone):
        result.total_days += 1
        trade = simulate_day(day_bars, params, instrument, exec_cfg)
        if trade is None:
            result.no_trade_days += 1
        else:
            result.trades.append(trade)
    return result


def simulate_day(
    day_bars: list[Bar5m],
    params: StrategyParams,
    instrument: InstrumentSpec,
    exec_cfg: ExecutionConfig,
) -> Optional[Trade]:
    eng = StrategyEngine(params=params, instrument=instrument)
    plan: Optional[TradePlan] = None
    entry_bar_idx: Optional[int] = None

    # Fase 1: encontrar entrada
    for i, bar in enumerate(day_bars):
        candidate = eng.on_new_5m_bar(bar)
        if candidate is not None:
            plan = candidate
            entry_bar_idx = i
            break

    if plan is None or entry_bar_idx is None:
        return None

    # Aplica slippage al entry
    slip = exec_cfg.slippage_ticks * instrument.tick_size
    if plan.side == Side.LONG:
        entry_filled = plan.entry_price + slip
    else:
        entry_filled = plan.entry_price - slip

    ref_entry = plan.sl_after_add  # SL post-addon = entry original
    sl = plan.sl_price
    tp = plan.tp_price
    add_trigger = plan.add_on_trigger_price

    initial_vol = plan.initial_volume
    add_vol = plan.add_on_volume
    add_executed = False

    dpppl = _dollars_per_point_per_lot(instrument)

    exit_reason = "eod"
    exit_price = day_bars[-1].close
    exit_time = day_bars[-1].timestamp.isoformat()

    # Fase 2: avanzar barras posteriores y simular ejecución
    for bar in day_bars[entry_bar_idx + 1:]:
        if plan.side == Side.LONG:
            hit_sl = bar.low <= sl
            hit_tp = bar.high >= tp
            hit_add = (not add_executed) and (bar.high >= add_trigger)
            if hit_add and not (hit_sl or hit_tp):
                # Add-on activado mid-bar; SL se mueve al BE original
                add_executed = True
                sl = ref_entry
                continue
            if hit_add and (hit_sl or hit_tp):
                # En la misma vela hay add-on + TP o SL. Asumimos:
                # add-on primero (trigger más cerca), luego TP/SL.
                add_executed = True
                sl = ref_entry
            if hit_sl and hit_tp:
                # Ambos en la misma vela
                exit_reason = "sl_after_add" if add_executed else "sl_initial"
                exit_price = sl if exec_cfg.pessimistic_intra_bar else tp
                if not exec_cfg.pessimistic_intra_bar:
                    exit_reason = "tp"
                exit_time = bar.timestamp.isoformat()
                break
            if hit_sl:
                exit_reason = "sl_after_add" if add_executed else "sl_initial"
                exit_price = sl
                exit_time = bar.timestamp.isoformat()
                break
            if hit_tp:
                exit_reason = "tp"
                exit_price = tp
                exit_time = bar.timestamp.isoformat()
                break
        else:  # SHORT
            hit_sl = bar.high >= sl
            hit_tp = bar.low <= tp
            hit_add = (not add_executed) and (bar.low <= add_trigger)
            if hit_add and not (hit_sl or hit_tp):
                add_executed = True
                sl = ref_entry
                continue
            if hit_add and (hit_sl or hit_tp):
                add_executed = True
                sl = ref_entry
            if hit_sl and hit_tp:
                exit_reason = "sl_after_add" if add_executed else "sl_initial"
                exit_price = sl if exec_cfg.pessimistic_intra_bar else tp
                if not exec_cfg.pessimistic_intra_bar:
                    exit_reason = "tp"
                exit_time = bar.timestamp.isoformat()
                break
            if hit_sl:
                exit_reason = "sl_after_add" if add_executed else "sl_initial"
                exit_price = sl
                exit_time = bar.timestamp.isoformat()
                break
            if hit_tp:
                exit_reason = "tp"
                exit_price = tp
                exit_time = bar.timestamp.isoformat()
                break

    # P&L
    def pl(side: Side, vol: float, entry: float, exit_: float) -> float:
        pts = (exit_ - entry) if side == Side.LONG else (entry - exit_)
        # Asumimos slippage también a la salida para SL/TP market — TP/SL son
        # límites en este sim, pero podríamos restar slippage en sl/tp si fueran
        # market. Para simplicidad: solo slippage de entrada.
        return pts * dpppl * vol

    pnl_initial = pl(plan.side, initial_vol, entry_filled, exit_price)
    pnl_add = 0.0
    if add_executed and add_vol > 0:
        # add-on entry con slippage
        add_filled = add_trigger + slip if plan.side == Side.LONG else add_trigger - slip
        pnl_add = pl(plan.side, add_vol, add_filled, exit_price)

    return Trade(
        session_date=day_bars[0].timestamp.astimezone(__import__("zoneinfo").ZoneInfo(params.timezone)).date().isoformat(),
        side=plan.side.value,
        range_width=eng.range_built.width if eng.range_built else 0.0,
        initial_volume=initial_vol,
        add_on_volume=add_vol if add_executed else 0.0,
        entry_time=day_bars[entry_bar_idx].timestamp.isoformat(),
        entry_price=entry_filled,
        sl_initial=plan.sl_price,
        tp=tp,
        add_trigger=add_trigger,
        add_on_executed=add_executed,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_usd=pnl_initial + pnl_add,
    )
