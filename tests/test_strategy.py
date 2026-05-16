"""
Tests del motor de estrategia. Sin dependencias de MT5/NinjaTrader.

Verifica:
- Cálculo del rango 9:30-9:45 ET
- Detección de breakout por cuerpo
- Sizing inicial y add-on
- Matemática del P&L en TP y en SL
- State machine
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# Permite importar desde mt5/
sys.path.insert(0, str(Path(__file__).parent.parent / "mt5"))

from strategy import (  # noqa: E402
    Bar5m,
    InstrumentSpec,
    Side,
    StratState,
    StrategyEngine,
    StrategyParams,
)


NY = ZoneInfo("America/New_York")


def mnq_spec() -> InstrumentSpec:
    """MNQ micro: tick=0.25, tick_value=$0.50."""
    return InstrumentSpec(
        symbol="MNQ",
        tick_size=0.25,
        tick_value=0.50,
        volume_min=1.0,
        volume_step=1.0,
        volume_max=100.0,
    )


def us100_cfd_spec() -> InstrumentSpec:
    """US100 CFD genérico: tick=0.1, tick_value=$0.10."""
    return InstrumentSpec(
        symbol="US100",
        tick_size=0.1,
        tick_value=0.10,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
    )


def default_params() -> StrategyParams:
    return StrategyParams(
        range_start="09:30",
        range_end="09:45",
        entry_window_end="17:00",
        risk_usd=2000.0,
        tp_r_multiple=0.5,
        add_on_trigger_r=0.4,
        add_on_size_multiple=2.5,
    )


def make_bar(hh: int, mm: int, o: float, h: float, l: float, c: float,
             day: str = "2026-05-15") -> Bar5m:
    y, mo, d = (int(x) for x in day.split("-"))
    return Bar5m(
        timestamp=datetime(y, mo, d, hh, mm, tzinfo=NY),
        open=o, high=h, low=l, close=c,
    )


# -----------------------------------------------------------------------------
# Range build
# -----------------------------------------------------------------------------

def test_range_built_from_three_5m_bars():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 16990, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17060, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17055, 17015, 17050))
    # La barra 9:45 ya está fuera del rango — dispara range_built
    plan = eng.on_new_5m_bar(make_bar(9, 45, 17050, 17052, 17042, 17048))

    assert eng.range_built is not None
    assert eng.range_built.high == 17060
    assert eng.range_built.low == 16990
    assert eng.range_built.width == 70
    # Esa misma barra no rompió (close 17048 < 17060), así que no entra
    assert plan is None
    assert eng.state == StratState.MONITORING


# -----------------------------------------------------------------------------
# Breakout detection
# -----------------------------------------------------------------------------

def test_long_breakout_with_body():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 16990, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17060, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17055, 17015, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17052, 17042, 17048))  # no rompe

    # 9:50 vela rompe alcista con cuerpo (close > rangeHigh=17060)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17048, 17075, 17045, 17070))
    assert plan is not None
    assert plan.side == Side.LONG
    assert eng.state == StratState.ENTERED


def test_short_breakout_with_body():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 16990, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17060, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17055, 17015, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17052, 17042, 17048))

    # 9:50 vela rompe bajista con cuerpo (close < rangeLow=16990)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17048, 17050, 16980, 16985))
    assert plan is not None
    assert plan.side == Side.SHORT
    assert eng.state == StratState.ENTERED


def test_wick_only_does_not_enter():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 16990, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17060, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17055, 17015, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17052, 17042, 17048))

    # Mecha rompe arriba (high 17075) pero close (17055) < rangeHigh (17060)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17050, 17075, 17048, 17055))
    assert plan is None
    assert eng.state == StratState.MONITORING


# -----------------------------------------------------------------------------
# Sizing
# -----------------------------------------------------------------------------

def test_sizing_mnq_range_50_points_ideal_entry():
    """
    Sizing con entrada ideal (close justo por encima del rango, sin overshoot).
    MNQ tick_value=$0.50/tick_size=$0.25 → $2/pt/contrato.
    Range 50 puntos, entry ~50 puntos del SL → N ≈ 2000/(50×2) = 20.
    """
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 17000, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17040, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17050, 17000, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17050, 17040, 17045))
    # close = 17050.25 → 1 tick por encima del rango (entry ideal sin overshoot)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17052, 17040, 17050.25))

    assert plan is not None
    assert eng.range_built.width == 50
    # Stop real: 17050.25 - 17000 = 50.25 → N = floor(2000/(50.25×2)) = 19
    assert plan.initial_volume == 19


def test_sizing_with_overshoot_reduces_contracts():
    """
    Si el cierre del breakout overshoots significativamente, N se reduce
    para respetar el riesgo de $2000.
    """
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 17000, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17040, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17050, 17000, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17050, 17040, 17045))
    # Close 17070 → overshoot de 20pts. Stop real = 70pts → N = floor(2000/140) = 14
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17075, 17040, 17070))

    assert plan is not None
    assert plan.initial_volume == 14  # menos que el ideal (19) porque overshoot


def test_sizing_tight_range_more_contracts():
    """Rango más chico → más contratos."""
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    # Rango 20 puntos
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17010, 17000, 17005))
    eng.on_new_5m_bar(make_bar(9, 35, 17005, 17015, 17000, 17010))
    eng.on_new_5m_bar(make_bar(9, 40, 17010, 17020, 17005, 17015))
    eng.on_new_5m_bar(make_bar(9, 45, 17015, 17018, 17010, 17012))
    # close apenas 1 tick por encima del range_high (17020 + 0.25)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17012, 17022, 17012, 17020.25))

    assert plan is not None
    assert eng.range_built.width == 20
    # Stop real: 17020.25 - 17000 = 20.25 → N = floor(2000/40.5) = 49
    assert plan.initial_volume == 49


def test_sizing_wide_range_fewer_contracts():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    # Rango 100 puntos
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17100, 17000, 17050))
    eng.on_new_5m_bar(make_bar(9, 35, 17050, 17080, 17000, 17040))
    eng.on_new_5m_bar(make_bar(9, 40, 17040, 17090, 17000, 17080))
    eng.on_new_5m_bar(make_bar(9, 45, 17080, 17085, 17070, 17075))
    # close 17100.25 → 1 tick por encima del rango
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17075, 17105, 17075, 17100.25))

    assert plan is not None
    assert eng.range_built.width == 100
    # Stop real: 100.25 → N = floor(2000/200.5) = 9
    assert plan.initial_volume == 9


def test_overshoot_past_tp_skips_entry():
    """Si la vela de ruptura cierra más allá del TP, NO se entra."""
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 17000, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17040, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17050, 17000, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17050, 17040, 17045))
    # Range high=17050, TP = 17050 + 0.5*50 = 17075. Close 17080 > TP → skip
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17085, 17040, 17080))

    assert plan is None
    assert eng.state == StratState.DONE


# -----------------------------------------------------------------------------
# Add-on math: la propiedad CRÍTICA de la spec
# -----------------------------------------------------------------------------

def _pl_at_price(side: Side, volume: float, entry: float, exit_: float,
                 spec: InstrumentSpec) -> float:
    """P&L en dólares de una posición que entra a `entry` y sale a `exit_`."""
    pts = (exit_ - entry) if side == Side.LONG else (entry - exit_)
    return pts * (spec.tick_value / spec.tick_size) * volume


def _ideal_entry_setup():
    """Setup con entry close = range_high + 1 tick (sin overshoot)."""
    spec = mnq_spec()
    eng = StrategyEngine(params=default_params(), instrument=spec)
    for b in [
        make_bar(9, 30, 17000, 17050, 17000, 17040),
        make_bar(9, 35, 17040, 17040, 17020, 17030),
        make_bar(9, 40, 17030, 17050, 17000, 17050),
        make_bar(9, 45, 17050, 17050, 17040, 17045),
    ]:
        eng.on_new_5m_bar(b)
    eng.on_new_5m_bar(make_bar(9, 50, 17045, 17052, 17040, 17050.25))
    return eng, spec


def test_total_pl_at_tp_close_to_1500_with_ideal_entry():
    """
    Con entrada ideal (sin overshoot del breakout) y add-on al 0.4R, el P&L al TP
    es ≈ $1500. La cifra exacta depende del redondeo de contratos (sizing entero).
    """
    eng, spec = _ideal_entry_setup()
    plan = eng.trade_plan
    assert plan is not None
    pl_initial = _pl_at_price(Side.LONG, plan.initial_volume, plan.entry_price, plan.tp_price, spec)
    pl_add = _pl_at_price(Side.LONG, plan.add_on_volume, plan.add_on_trigger_price, plan.tp_price, spec)
    total = pl_initial + pl_add
    # Con N=19 y M=47, total ≈ $1428 (un poco menos por redondeo, no $1500)
    assert 1300 <= total <= 1550


def test_total_pl_at_sl_after_add_at_most_minus_2000():
    """
    Si después del add-on stopea (SL combinado al ref_entry), la pérdida total
    nunca debe exceder ~$2000 (con tolerancia por redondeo).
    """
    eng, spec = _ideal_entry_setup()
    plan = eng.trade_plan
    assert plan is not None
    sl_after = plan.sl_after_add
    pl_initial = _pl_at_price(Side.LONG, plan.initial_volume, plan.entry_price, sl_after, spec)
    pl_add = _pl_at_price(Side.LONG, plan.add_on_volume, plan.add_on_trigger_price, sl_after, spec)
    total = pl_initial + pl_add
    assert -2050 <= total <= -1850


def test_pl_at_initial_sl_at_most_minus_2000():
    """Si stopea antes del add-on, pérdida ≤ ~$2000 (garantía del sizing-real)."""
    eng, spec = _ideal_entry_setup()
    plan = eng.trade_plan
    assert plan is not None
    pl = _pl_at_price(Side.LONG, plan.initial_volume, plan.entry_price, plan.sl_price, spec)
    assert -2050 <= pl <= -1900


# -----------------------------------------------------------------------------
# State machine
# -----------------------------------------------------------------------------

def test_no_entry_after_17_00():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    for b in [
        make_bar(9, 30, 17000, 17050, 16990, 17040),
        make_bar(9, 35, 17040, 17060, 17020, 17030),
        make_bar(9, 40, 17030, 17055, 17015, 17050),
        make_bar(9, 45, 17050, 17052, 17042, 17048),
    ]:
        eng.on_new_5m_bar(b)

    # Una ruptura clara pero a las 17:05 — debe ser ignorada
    plan = eng.on_new_5m_bar(make_bar(17, 5, 17050, 17080, 17050, 17075))
    assert plan is None
    assert eng.state == StratState.DONE


def test_addon_triggers_on_tick():
    spec = mnq_spec()
    eng = StrategyEngine(params=default_params(), instrument=spec)
    for b in [
        make_bar(9, 30, 17000, 17050, 17000, 17040),
        make_bar(9, 35, 17040, 17040, 17020, 17030),
        make_bar(9, 40, 17030, 17050, 17000, 17050),
        make_bar(9, 45, 17050, 17050, 17040, 17045),
    ]:
        eng.on_new_5m_bar(b)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17075, 17040, 17070))
    add_trigger = plan.add_on_trigger_price  # 17070

    # Ticks por debajo del trigger no disparan
    assert eng.on_tick(17065, datetime(2026, 5, 15, 9, 55, tzinfo=NY)) is None
    # Tick que cruza
    sig = eng.on_tick(17070, datetime(2026, 5, 15, 9, 56, tzinfo=NY))
    assert sig == "ADD_ON"
    assert eng.state == StratState.SCALED_IN
    # Doble disparo no se repite
    assert eng.on_tick(17072, datetime(2026, 5, 15, 9, 57, tzinfo=NY)) is None


def test_one_trade_per_day():
    """Después de entrar, no se vuelve a evaluar entrada el mismo día."""
    spec = mnq_spec()
    eng = StrategyEngine(params=default_params(), instrument=spec)
    for b in [
        make_bar(9, 30, 17000, 17050, 17000, 17040),
        make_bar(9, 35, 17040, 17040, 17020, 17030),
        make_bar(9, 40, 17030, 17050, 17000, 17050),
        make_bar(9, 45, 17050, 17050, 17040, 17045),
    ]:
        eng.on_new_5m_bar(b)
    plan1 = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17075, 17040, 17070))
    assert plan1 is not None

    # Otra ruptura más tarde — no debe generar plan
    plan2 = eng.on_new_5m_bar(make_bar(10, 30, 17070, 17100, 17040, 17095))
    assert plan2 is None


def test_us100_cfd_sizing():
    """Sizing en CFDs con lotaje fraccionado, entry ideal sin overshoot."""
    spec = us100_cfd_spec()
    eng = StrategyEngine(params=default_params(), instrument=spec)
    # Rango 20 puntos en US100
    for b in [
        make_bar(9, 30, 18000, 18010, 18000, 18005),
        make_bar(9, 35, 18005, 18015, 18000, 18010),
        make_bar(9, 40, 18010, 18020, 18005, 18015),
        make_bar(9, 45, 18015, 18018, 18010, 18012),
    ]:
        eng.on_new_5m_bar(b)
    # close = 18020.1 → 1 tick por encima del rango
    plan = eng.on_new_5m_bar(make_bar(9, 50, 18012, 18022, 18012, 18020.1))

    # Stop real: 18020.1 - 18000 = 20.1 → $20.1/lot → N = floor(2000/20.1) = 99.50
    assert plan.initial_volume == pytest.approx(99.50, abs=0.01)
