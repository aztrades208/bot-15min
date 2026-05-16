"""
Entry point del bot CFD/MT5. Carga config, conecta MT5, corre el loop de
estrategia y persiste estado en disco para sobrevivir reinicios.

Uso:
    python main.py --config config.yaml

El loop:
  - cada 5 minutos (al cierre de la última vela): on_new_5m_bar
  - cada N segundos (TICK_POLL): on_tick para chequear add-on
  - persiste el estado de la estrategia tras cada evento
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from broker_mt5 import BrokerMT5
from risk_manager import AccountSnapshot, PropFirmRules, RiskManager
from strategy import (
    Bar5m,
    InstrumentSpec,
    Range,
    Side,
    StratState,
    StrategyEngine,
    StrategyParams,
    TradePlan,
)

TICK_POLL_SECONDS = 1.0
BAR_POLL_SECONDS = 5.0   # poll the latest 5m bar every 5s to detect close
log = logging.getLogger("main")


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def save_state(engine: StrategyEngine, path: str) -> None:
    data = {
        "state": engine.state.value,
        "session_date": engine.session_date,
        "range_so_far": asdict(engine.range_so_far) if engine.range_so_far else None,
        "range_built": asdict(engine.range_built) if engine.range_built else None,
        "trade_plan": _plan_to_dict(engine.trade_plan),
        "add_on_executed": engine.add_on_executed,
    }
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_state(engine: StrategyEngine, path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    engine.state = StratState(data["state"])
    engine.session_date = data["session_date"]
    engine.range_so_far = Range(**data["range_so_far"]) if data["range_so_far"] else None
    engine.range_built = Range(**data["range_built"]) if data["range_built"] else None
    engine.trade_plan = _plan_from_dict(data["trade_plan"])
    engine.add_on_executed = data["add_on_executed"]


def _plan_to_dict(p: Optional[TradePlan]) -> Optional[dict]:
    if p is None:
        return None
    return {**asdict(p), "side": p.side.value}


def _plan_from_dict(d: Optional[dict]) -> Optional[TradePlan]:
    if d is None:
        return None
    d2 = dict(d)
    d2["side"] = Side(d2["side"])
    return TradePlan(**d2)


def build_engine(cfg: dict, spec: InstrumentSpec) -> StrategyEngine:
    s = cfg["strategy"]
    params = StrategyParams(
        range_start=s["range_start"],
        range_end=s["range_end"],
        entry_window_end=s["entry_window_end"],
        timezone=s["timezone"],
        risk_usd=float(s["risk_usd"]),
        tp_r_multiple=float(s["tp_r_multiple"]),
        add_on_trigger_r=float(s["add_on_trigger_r"]),
        add_on_size_multiple=float(s["add_on_size_multiple"]),
        move_sl_to_entry_on_add=bool(s["move_sl_to_entry_on_add"]),
        body_breakout_only=bool(s["body_breakout_only"]),
        one_trade_per_day=bool(s["one_trade_per_day"]),
        hold_until_resolution=bool(s["hold_until_resolution"]),
    )
    return StrategyEngine(params=params, instrument=spec)


def main(config_path: str) -> int:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(cfg["state"]["log_file"]),
        ],
    )

    rules = PropFirmRules.from_file(cfg["propfirm"]["active_rules_file"])
    risk = RiskManager(rules)
    log.info("Propfirm: %s (max_dd=$%.0f, daily_loss=%s)",
             rules.name, rules.drawdown_amount_usd, rules.max_daily_loss_usd)

    broker_cfg = cfg["broker"]
    broker = BrokerMT5(
        login=int(broker_cfg["login"]),
        password=str(broker_cfg["password"]),
        server=str(broker_cfg["server"]),
        terminal_path=broker_cfg.get("terminal_path"),
        deviation_points=int(broker_cfg["deviation_points"]),
        magic_number=int(cfg["instrument"]["magic_number"]),
    )
    broker.connect()
    try:
        symbol = cfg["instrument"]["symbol"]
        spec = broker.get_symbol_spec(symbol)
        log.info("Instrument %s: tick=%s value=$%s vol_min=%s step=%s",
                 symbol, spec.tick_size, spec.tick_value, spec.volume_min, spec.volume_step)

        engine = build_engine(cfg, spec)
        load_state(engine, cfg["state"]["file"])

        run_loop(engine, broker, risk, cfg)
    finally:
        broker.shutdown()
    return 0


def run_loop(engine: StrategyEngine, broker: BrokerMT5,
             risk: RiskManager, cfg: dict) -> None:
    symbol = cfg["instrument"]["symbol"]
    state_file = cfg["state"]["file"]
    tz = ZoneInfo(cfg["strategy"]["timezone"])

    last_bar_time: Optional[datetime] = None
    hwm = None  # high water mark de la cuenta

    while True:
        try:
            now = datetime.now(tz=ZoneInfo("UTC"))
            now_ny = now.astimezone(tz)

            # 1) Cargar últimas barras 5m y detectar nuevas
            bars = broker.get_5m_bars(symbol, count=200)
            if not bars:
                time.sleep(BAR_POLL_SECONDS)
                continue
            latest = bars[-1]
            if last_bar_time is None or latest.timestamp > last_bar_time:
                # Procesa la barra anterior (cerrada) — la última de la lista normalmente
                # está aún formándose, así que iteramos hasta la penúltima.
                for bar in bars[:-1]:
                    if last_bar_time is not None and bar.timestamp <= last_bar_time:
                        continue
                    plan = engine.on_new_5m_bar(bar)
                    if plan is not None:
                        execute_entry(engine, plan, broker, risk, symbol)
                    save_state(engine, state_file)
                last_bar_time = bars[-2].timestamp  # marca última procesada

            # 2) On-tick: check add-on
            tick = broker.get_last_tick(symbol)
            if tick is not None and engine.state == StratState.ENTERED:
                price, ts = tick
                signal = engine.on_tick(price, ts)
                if signal == "ADD_ON":
                    execute_add_on(engine, broker, symbol)
                    save_state(engine, state_file)

            # 3) Check si la posición se cerró por TP/SL
            positions = broker.get_open_positions(symbol)
            if not positions and engine.state in (StratState.ENTERED, StratState.SCALED_IN):
                log.info("Position flat — TP o SL alcanzado")
                engine.on_position_closed()
                save_state(engine, state_file)

            time.sleep(TICK_POLL_SECONDS)
        except KeyboardInterrupt:
            log.info("Interrupt — saliendo")
            return
        except Exception:
            log.exception("Error en loop — reintenta en 10s")
            time.sleep(10)


def execute_entry(engine: StrategyEngine, plan: TradePlan,
                  broker: BrokerMT5, risk: RiskManager, symbol: str) -> None:
    balance, equity = broker.get_account_snapshot()
    snapshot = AccountSnapshot(
        balance=balance, equity=equity,
        high_water_mark=max(balance, equity),
        realized_today=0.0,  # TODO: calcular desde history para ser preciso
    )
    allowed, reason = risk.can_open_trade(snapshot, engine.params.risk_usd)
    if not allowed:
        log.warning("Bloqueado por propfirm: %s", reason)
        engine.state = StratState.DONE
        return

    capped_vol = risk.cap_volume(plan.initial_volume)
    if capped_vol < engine.instrument.volume_min:
        log.warning("Volumen bajo mínimo tras cap: %.2f", capped_vol)
        engine.state = StratState.DONE
        return
    if capped_vol != plan.initial_volume:
        log.info("Volumen recortado por propfirm: %.2f → %.2f", plan.initial_volume, capped_vol)
        plan.initial_volume = capped_vol

    broker.market_order(
        symbol=symbol,
        side=plan.side.value,
        volume=plan.initial_volume,
        sl=plan.sl_price,
        tp=plan.tp_price,
        comment=f"ORB15 {plan.side.value}",
    )


def execute_add_on(engine: StrategyEngine, broker: BrokerMT5, symbol: str) -> None:
    plan = engine.trade_plan
    assert plan is not None

    # 1) Mete add-on
    if plan.add_on_volume > 0:
        broker.market_order(
            symbol=symbol,
            side=plan.side.value,
            volume=plan.add_on_volume,
            sl=plan.sl_after_add,
            tp=plan.tp_price,
            comment=f"ORB15 add {plan.side.value}",
        )

    # 2) Mueve SL de todas las posiciones existentes al entry original
    for pos in broker.get_open_positions(symbol):
        broker.modify_position(pos.ticket, sl=plan.sl_after_add, tp=plan.tp_price)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="ruta al YAML de config")
    args = parser.parse_args()
    sys.exit(main(args.config))
