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

def test_sizing_mnq_range_50_points():
    """
    MNQ tick_value=$0.50 sobre tick_size=$0.25 → $2/punto/contrato.
    Rango 50 puntos × $2 = $100/contrato. N = 2000/100 = 20.
    """
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    # Construye un rango de 50 puntos exactos
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17050, 17000, 17040))
    eng.on_new_5m_bar(make_bar(9, 35, 17040, 17040, 17020, 17030))
    eng.on_new_5m_bar(make_bar(9, 40, 17030, 17050, 17000, 17050))
    eng.on_new_5m_bar(make_bar(9, 45, 17050, 17050, 17040, 17045))
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17075, 17040, 17070))

    assert plan is not None
    assert eng.range_built.width == 50
    assert plan.initial_volume == 20
    assert plan.add_on_volume == 50  # 20 × 2.5


def test_sizing_tight_range_more_contracts():
    """Rango más chico → más contratos."""
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    # Rango 20 puntos
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17010, 17000, 17005))
    eng.on_new_5m_bar(make_bar(9, 35, 17005, 17015, 17000, 17010))
    eng.on_new_5m_bar(make_bar(9, 40, 17010, 17020, 17005, 17015))
    eng.on_new_5m_bar(make_bar(9, 45, 17015, 17018, 17010, 17012))
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17012, 17030, 17012, 17025))

    assert eng.range_built.width == 20
    # 20 pts × $2 = $40/contrato. N = 2000/40 = 50
    assert plan.initial_volume == 50


def test_sizing_wide_range_fewer_contracts():
    eng = StrategyEngine(params=default_params(), instrument=mnq_spec())
    # Rango 100 puntos
    eng.on_new_5m_bar(make_bar(9, 30, 17000, 17100, 17000, 17050))
    eng.on_new_5m_bar(make_bar(9, 35, 17050, 17080, 17000, 17040))
    eng.on_new_5m_bar(make_bar(9, 40, 17040, 17090, 17000, 17080))
    eng.on_new_5m_bar(make_bar(9, 45, 17080, 17085, 17070, 17075))
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17075, 17120, 17075, 17110))

    assert eng.range_built.width == 100
    # 100 pts × $2 = $200/contrato. N = 2000/200 = 10
    assert plan.initial_volume == 10


# -----------------------------------------------------------------------------
# Add-on math: la propiedad CRÍTICA de la spec
# -----------------------------------------------------------------------------

def _pl_at_price(side: Side, volume: float, entry: float, exit_: float,
                 spec: InstrumentSpec) -> float:
    """P&L en dólares de una posición que entra a `entry` y sale a `exit_`."""
    pts = (exit_ - entry) if side == Side.LONG else (entry - exit_)
    return pts * (spec.tick_value / spec.tick_size) * volume


def test_total_pl_at_tp_equals_1500():
    """
    Con add-on 2.5x ejecutado, P&L al TP debe ser exactamente $1500
    (3% sobre 50k = 1.5R sobre 2000 = 1500). Ésta es la propiedad clave de la spec.
    """
    spec = mnq_spec()
    eng = StrategyEngine(params=default_params(), instrument=spec)
    # Setup rango 50 pts
    for b in [
        make_bar(9, 30, 17000, 17050, 17000, 17040),
        make_bar(9, 35, 17040, 17040, 17020, 17030),
        make_bar(9, 40, 17030, 17050, 17000, 17050),
        make_bar(9, 45, 17050, 17050, 17040, 17045),
    ]:
        eng.on_new_5m_bar(b)
    plan = eng.on_new_5m_bar(make_bar(9, 50, 17045, 17075, 17040, 17070))
    assert plan is not None
    rng = eng.range_built
    ref_entry = rng.high  # 17050 — entry de referencia

    # Add-on entra al 0.4R = 17050 + 0.4*50 = 17070
    # TP al 0.5R = 17050 + 0.5*50 = 17075
    add_entry_price = plan.add_on_trigger_price
    assert add_entry_price == pytest.approx(17070)
    assert plan.tp_price == pytest.approx(17075)

    pl_initial = _pl_at_price(Side.LONG, plan.initial_volume, ref_entry, plan.tp_price, spec)
    pl_add = _pl_at_price(Side.LONG, plan.add_on_volume, add_entry_price, plan.tp_price, spec)

    assert pl_initial == pytest.approx(1000)
    assert pl_add == pytest.approx(500)
    assert pl_initial + pl_add == pytest.approx(1500)


def test_total_pl_at_sl_after_add_equals_minus_2000():
    """
    Si después del add-on stopea (SL movido al entry original), pérdida total = $2000.
    """
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
    rng = eng.range_built
    ref_entry = rng.high
    add_entry = plan.add_on_trigger_price
    sl_after = plan.sl_after_add  # debe ser ref_entry

    assert sl_after == pytest.approx(ref_entry)

    # Stopea — exit a sl_after
    pl_initial = _pl_at_price(Side.LONG, plan.initial_volume, ref_entry, sl_after, spec)
    pl_add = _pl_at_price(Side.LONG, plan.add_on_volume, add_entry, sl_after, spec)

    assert pl_initial == pytest.approx(0)        # original BE
    assert pl_add == pytest.approx(-2000)
    assert pl_initial + pl_add == pytest.approx(-2000)


def test_pl_at_initial_sl_without_addon_equals_minus_2000():
    """Si stopea ANTES de llegar al 0.4R, pierde exactamente la R inicial."""
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
    ref_entry = eng.range_built.high

    pl = _pl_at_price(Side.LONG, plan.initial_volume, ref_entry, plan.sl_price, spec)
    assert pl == pytest.approx(-2000)


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
    """Verifica que el sizing también funciona con CFDs (lotaje fraccionado)."""
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
    plan = eng.on_new_5m_bar(make_bar(9, 50, 18012, 18030, 18012, 18025))

    # US100: $1/pt/lot. Rango 20pts → $20/lot. N = 2000/20 = 100 lotes
    assert plan.initial_volume == 100.0
