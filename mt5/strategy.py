"""
Motor puro de estrategia ORB 15m. Sin dependencias del broker — recibe ticks
y velas de 5m, decide cuándo entrar, cuándo escalar y a qué precios.

Spec: docs/strategy.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo


class StratState(str, Enum):
    WAIT_RANGE_OPEN = "wait_range_open"
    BUILDING_RANGE = "building_range"
    MONITORING = "monitoring"
    ENTERED = "entered"
    SCALED_IN = "scaled_in"
    DONE = "done"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Bar5m:
    timestamp: datetime  # tz-aware
    open: float
    high: float
    low: float
    close: float


@dataclass
class Range:
    high: float
    low: float

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass
class TradePlan:
    side: Side
    entry_price: float
    sl_price: float
    tp_price: float
    add_on_trigger_price: float
    initial_volume: float       # contratos o lotes, depende del broker
    add_on_volume: float
    sl_after_add: float          # SL combinado tras add-on


@dataclass
class StrategyParams:
    range_start: str = "09:30"
    range_end: str = "09:45"
    entry_window_end: str = "17:00"
    timezone: str = "America/New_York"

    risk_usd: float = 2000.0
    tp_r_multiple: float = 0.5
    add_on_trigger_r: float = 0.4
    add_on_size_multiple: float = 2.5
    move_sl_to_entry_on_add: bool = True

    body_breakout_only: bool = True
    one_trade_per_day: bool = True
    hold_until_resolution: bool = True


@dataclass
class InstrumentSpec:
    symbol: str
    tick_size: float           # ej. 0.1 para US100
    tick_value: float          # dólares por 1 tick por 1 lote
    volume_min: float          # ej. 0.01
    volume_step: float         # ej. 0.01
    volume_max: float          # tope del broker


@dataclass
class StrategyEngine:
    """
    State machine de la estrategia. Stateless externamente — todo el estado
    está en el dataclass. Serializable a JSON.
    """
    params: StrategyParams
    instrument: InstrumentSpec
    state: StratState = StratState.WAIT_RANGE_OPEN
    session_date: Optional[str] = None
    range_so_far: Optional[Range] = None
    range_built: Optional[Range] = None
    trade_plan: Optional[TradePlan] = None
    add_on_executed: bool = False

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.params.timezone)

    def _parse_time(self, hhmm: str) -> dtime:
        h, m = hhmm.split(":")
        return dtime(int(h), int(m))

    def _ny(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            raise ValueError("timestamps must be tz-aware")
        return dt.astimezone(self.tz)

    def _session_date_of(self, dt: datetime) -> str:
        return self._ny(dt).date().isoformat()

    def _reset_for_new_day(self, session_date: str) -> None:
        self.state = StratState.WAIT_RANGE_OPEN
        self.session_date = session_date
        self.range_so_far = None
        self.range_built = None
        self.trade_plan = None
        self.add_on_executed = False

    def on_new_5m_bar(self, bar: Bar5m) -> Optional[TradePlan]:
        """
        Llamado al cierre de cada vela de 5m. Devuelve un TradePlan si toca
        entrar AHORA (a mercado en el precio de cierre). En cualquier otro caso
        devuelve None.
        """
        ny_time = self._ny(bar.timestamp)
        sd = ny_time.date().isoformat()
        if sd != self.session_date:
            if self.state in (StratState.ENTERED, StratState.SCALED_IN):
                # No tocamos — posición abierta sobrevive (no debería pasar con propfirm sin overnight)
                return None
            self._reset_for_new_day(sd)

        range_start = self._parse_time(self.params.range_start)
        range_end = self._parse_time(self.params.range_end)
        entry_end = self._parse_time(self.params.entry_window_end)
        bar_time = ny_time.time()

        if self.state == StratState.WAIT_RANGE_OPEN:
            if range_start <= bar_time < range_end:
                self.state = StratState.BUILDING_RANGE
                self.range_so_far = Range(high=bar.high, low=bar.low)
            return None

        if self.state == StratState.BUILDING_RANGE:
            if bar_time < range_end:
                assert self.range_so_far is not None
                self.range_so_far.high = max(self.range_so_far.high, bar.high)
                self.range_so_far.low = min(self.range_so_far.low, bar.low)
                return None
            # ya pasamos 9:45 — esta vela puede ser la primera fuera del rango
            assert self.range_so_far is not None
            self.range_built = self.range_so_far
            self.state = StratState.MONITORING
            return self._maybe_enter(bar)

        if self.state == StratState.MONITORING:
            if bar_time >= entry_end:
                self.state = StratState.DONE
                return None
            return self._maybe_enter(bar)

        return None

    def _maybe_enter(self, bar: Bar5m) -> Optional[TradePlan]:
        assert self.range_built is not None
        rng = self.range_built

        if self.params.body_breakout_only:
            if bar.close > rng.high:
                return self._build_plan(Side.LONG, bar)
            if bar.close < rng.low:
                return self._build_plan(Side.SHORT, bar)
        else:
            if bar.high > rng.high:
                return self._build_plan(Side.LONG, bar)
            if bar.low < rng.low:
                return self._build_plan(Side.SHORT, bar)
        return None

    def _build_plan(self, side: Side, bar: Bar5m) -> Optional[TradePlan]:
        rng = self.range_built
        assert rng is not None
        ref_entry = rng.high if side == Side.LONG else rng.low
        sl_price = rng.low if side == Side.LONG else rng.high
        entry_price = bar.close       # market al close de la vela de ruptura

        tp_price = (ref_entry + self.params.tp_r_multiple * rng.width
                    if side == Side.LONG
                    else ref_entry - self.params.tp_r_multiple * rng.width)
        add_trigger = (ref_entry + self.params.add_on_trigger_r * rng.width
                       if side == Side.LONG
                       else ref_entry - self.params.add_on_trigger_r * rng.width)

        # Filtro de overshoot: si el cierre de la vela de ruptura ya está
        # por encima del TP (LONG) o por debajo (SHORT), no entramos —
        # la vela ya consumió el target, perseguir es perder.
        if side == Side.LONG and entry_price >= tp_price:
            self.state = StratState.DONE
            return None
        if side == Side.SHORT and entry_price <= tp_price:
            self.state = StratState.DONE
            return None

        # Sizing basado en la distancia REAL entry → SL (no en range_width).
        # Si entry está por encima de range_high (overshoot del breakout), el
        # stop real es mayor que el range_width y debemos reducir N para no
        # superar $2000 de riesgo.
        actual_stop_pts = abs(entry_price - sl_price)
        if actual_stop_pts <= 0:
            return None
        dollar_per_point_per_lot = self.instrument.tick_value / self.instrument.tick_size
        risk_per_lot = actual_stop_pts * dollar_per_point_per_lot
        if risk_per_lot <= 0:
            return None

        raw_volume = self.params.risk_usd / risk_per_lot
        initial_volume = self._round_volume_down(raw_volume)
        if initial_volume < self.instrument.volume_min:
            return None

        add_on_raw = initial_volume * self.params.add_on_size_multiple
        add_on_volume = self._round_volume_down(add_on_raw)
        if add_on_volume < self.instrument.volume_min:
            add_on_volume = 0.0  # add-on no aplica si no llega al mínimo

        sl_after_add = ref_entry  # mover al entry original (= range edge)

        plan = TradePlan(
            side=side,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            add_on_trigger_price=add_trigger,
            initial_volume=initial_volume,
            add_on_volume=add_on_volume,
            sl_after_add=sl_after_add,
        )
        self.trade_plan = plan
        self.state = StratState.ENTERED
        return plan

    def _round_volume_down(self, vol: float) -> float:
        step = self.instrument.volume_step
        if step <= 0:
            return max(0.0, vol)
        steps = int(vol / step)
        v = steps * step
        if v > self.instrument.volume_max:
            v = self.instrument.volume_max - (self.instrument.volume_max % step)
        return round(v, 8)

    def on_tick(self, price: float, ts: datetime) -> Optional[str]:
        """
        Llamado en cada tick. Devuelve "ADD_ON" si toca disparar el add-on.
        """
        if self.state != StratState.ENTERED:
            return None
        if self.add_on_executed:
            return None
        plan = self.trade_plan
        if plan is None or plan.add_on_volume <= 0:
            self.state = StratState.SCALED_IN
            return None

        crossed = (price >= plan.add_on_trigger_price
                   if plan.side == Side.LONG
                   else price <= plan.add_on_trigger_price)
        if crossed:
            self.add_on_executed = True
            self.state = StratState.SCALED_IN
            return "ADD_ON"
        return None

    def on_position_closed(self) -> None:
        """Llamar cuando broker reporta posición flat."""
        if self.state in (StratState.ENTERED, StratState.SCALED_IN):
            self.state = StratState.DONE
