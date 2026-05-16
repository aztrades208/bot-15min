"""
Adapter MT5: conecta con la terminal MT5 vía Python API.
Expone una interfaz simple: connect, get_symbol_spec, get_5m_bars, market_order,
modify_position_sl, get_position.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

try:
    import MetaTrader5 as mt5
except ImportError as e:
    raise SystemExit(
        "MetaTrader5 no instalado. Ejecuta `pip install -r requirements.txt` "
        "en una máquina Windows con MT5 terminal instalado."
    ) from e

from strategy import Bar5m, InstrumentSpec

log = logging.getLogger("broker_mt5")


@dataclass
class OpenPositionInfo:
    ticket: int
    volume: float
    sl: float
    tp: float
    price_open: float
    side: str  # "long" / "short"


class BrokerMT5:
    def __init__(self, login: int, password: str, server: str,
                 terminal_path: Optional[str], deviation_points: int,
                 magic_number: int):
        self.login = login
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self.deviation = deviation_points
        self.magic = magic_number

    def connect(self) -> None:
        kwargs = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
        if not mt5.login(self.login, password=self.password, server=self.server):
            raise RuntimeError(f"mt5.login failed: {mt5.last_error()}")
        log.info("Connected to MT5 server=%s account=%s", self.server, self.login)

    def shutdown(self) -> None:
        mt5.shutdown()

    def get_symbol_spec(self, symbol: str) -> InstrumentSpec:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info({symbol}) is None — ¿símbolo correcto?")
        if not info.visible:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
        return InstrumentSpec(
            symbol=symbol,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            volume_min=info.volume_min,
            volume_step=info.volume_step,
            volume_max=info.volume_max,
        )

    def get_5m_bars(self, symbol: str, count: int) -> list[Bar5m]:
        """Devuelve las últimas `count` velas de 5 minutos (ascendente por tiempo)."""
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count)
        if rates is None or len(rates) == 0:
            return []
        bars: list[Bar5m] = []
        for r in rates:
            # rates['time'] es UTC segundos
            ts = datetime.fromtimestamp(int(r["time"]), tz=ZoneInfo("UTC"))
            bars.append(Bar5m(
                timestamp=ts,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
            ))
        return bars

    def get_last_tick(self, symbol: str) -> Optional[tuple[float, datetime]]:
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            return None
        ts = datetime.fromtimestamp(t.time, tz=ZoneInfo("UTC"))
        # uso el bid para LONG (peor caso) y ask para SHORT en otros lados,
        # aquí devuelvo el LAST para el check de add-on (suficientemente preciso)
        price = t.last if t.last > 0 else (t.bid + t.ask) / 2.0
        return price, ts

    def market_order(self, symbol: str, side: str, volume: float,
                     sl: Optional[float], tp: Optional[float], comment: str) -> int:
        info_tick = mt5.symbol_info_tick(symbol)
        if info_tick is None:
            raise RuntimeError(f"symbol_info_tick({symbol}) None")
        price = info_tick.ask if side == "long" else info_tick.bid
        order_type = mt5.ORDER_TYPE_BUY if side == "long" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment[:30],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if sl is not None:
            request["sl"] = sl
        if tp is not None:
            request["tp"] = tp
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"market_order failed: retcode={result.retcode} {result.comment}")
        log.info("ORDER FILLED %s vol=%.2f price=%.5f sl=%s tp=%s ticket=%s",
                 side.upper(), volume, price, sl, tp, result.order)
        return result.order

    def get_open_positions(self, symbol: str) -> list[OpenPositionInfo]:
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        out: list[OpenPositionInfo] = []
        for p in positions:
            if p.magic != self.magic:
                continue
            out.append(OpenPositionInfo(
                ticket=p.ticket,
                volume=p.volume,
                sl=p.sl,
                tp=p.tp,
                price_open=p.price_open,
                side="long" if p.type == mt5.POSITION_TYPE_BUY else "short",
            ))
        return out

    def modify_position(self, ticket: int, sl: float, tp: Optional[float] = None) -> None:
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            raise RuntimeError(f"ticket {ticket} not found")
        p = pos[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": p.symbol,
            "position": ticket,
            "sl": sl,
            "tp": tp if tp is not None else p.tp,
            "magic": self.magic,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"modify_position failed: retcode={result.retcode} {result.comment}")
        log.info("MODIFY ticket=%s sl=%.5f tp=%.5f", ticket, sl, request["tp"])

    def get_account_snapshot(self) -> tuple[float, float]:
        ai = mt5.account_info()
        if ai is None:
            raise RuntimeError("account_info None")
        return float(ai.balance), float(ai.equity)
