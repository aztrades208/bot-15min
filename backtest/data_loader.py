"""
Cargador de datos históricos para backtesting.

Formatos soportados:
1. CSV "estándar" con header: timestamp,open,high,low,close[,volume]
2. CSV de NinjaTrader 8 sin header: yyyyMMdd HHmmss;open;high;low;close;volume
3. CSV de NinjaTrader 8 con header: Date;Time;Open;High;Low;Close;Volume (separador ; o ,)

Si el formato no se detecta por header, intenta el formato NT8 sin header.

Granularidad: 5 minutos (se valida).
Timezone: si los timestamps son naive, se asume `assume_tz` (default UTC).
NinjaTrader exporta en la hora del PC del usuario por defecto — si lo exportas
desde un servidor en otra TZ, pasa --data-tz acorde.
"""
from __future__ import annotations

import csv
import glob
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mt5"))
from strategy import Bar5m  # noqa: E402


_REQUIRED = {"timestamp", "open", "high", "low", "close"}
_NT8_ALIASES = {"date": "date", "time": "time"}


def load_csv(path_or_glob: str, assume_tz: str = "UTC") -> list[Bar5m]:
    paths = sorted(glob.glob(path_or_glob)) or [path_or_glob]
    assume = ZoneInfo(assume_tz)
    bars: list[Bar5m] = []
    for p in paths:
        bars.extend(_load_one(p, assume))
    bars.sort(key=lambda b: b.timestamp)
    _validate_5m(bars)
    return bars


def _load_one(path: str, assume: ZoneInfo) -> list[Bar5m]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    sep = _detect_separator(raw)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []

    first = lines[0]
    has_header = _looks_like_header(first)
    if has_header:
        return _parse_with_header(lines, sep, assume)
    return _parse_nt8_no_header(lines, sep, assume)


def _detect_separator(text: str) -> str:
    sample = text[:4096]
    if sample.count(";") > sample.count(","):
        return ";"
    return ","


def _looks_like_header(first_line: str) -> bool:
    lower = first_line.lower()
    return any(tok in lower for tok in ("timestamp", "date", "open", "high", "low", "close"))


def _parse_with_header(lines: list[str], sep: str, assume: ZoneInfo) -> list[Bar5m]:
    reader = csv.reader(lines, delimiter=sep)
    headers = [h.strip().lower() for h in next(reader)]
    has_ts = "timestamp" in headers
    has_date_time = "date" in headers and "time" in headers
    if not (has_ts or has_date_time):
        raise ValueError(f"Header sin 'timestamp' ni 'date'+'time': {headers}")
    bars: list[Bar5m] = []
    idx = {h: i for i, h in enumerate(headers)}
    for row in reader:
        if not row or len(row) < len(headers):
            continue
        if has_ts:
            ts_raw = row[idx["timestamp"]]
        else:
            ts_raw = f"{row[idx['date']]} {row[idx['time']]}"
        ts = _parse_ts(ts_raw, assume)
        bars.append(Bar5m(
            timestamp=ts,
            open=float(row[idx["open"]]),
            high=float(row[idx["high"]]),
            low=float(row[idx["low"]]),
            close=float(row[idx["close"]]),
        ))
    return bars


def _parse_nt8_no_header(lines: list[str], sep: str, assume: ZoneInfo) -> list[Bar5m]:
    """
    Formato NinjaTrader: `yyyyMMdd HHmmss;open;high;low;close;volume`
    Algunas exportaciones usan `,` y/o separan fecha+hora con ' '.
    """
    bars: list[Bar5m] = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(sep)]
        if len(parts) < 5:
            continue
        ts = _parse_ts(parts[0], assume)
        bars.append(Bar5m(
            timestamp=ts,
            open=float(parts[1]),
            high=float(parts[2]),
            low=float(parts[3]),
            close=float(parts[4]),
        ))
    return bars


def _parse_ts(s: str, assume: ZoneInfo) -> datetime:
    s = s.strip()
    formats = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y%m%d %H%M%S",
        "%Y%m%d%H%M%S",
        "%Y%m%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=assume)
            return dt
        except ValueError:
            continue
    if s.endswith("Z"):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def _validate_5m(bars: list[Bar5m]) -> None:
    if len(bars) < 2:
        return
    deltas = []
    for i in range(1, min(len(bars), 200)):
        d = (bars[i].timestamp - bars[i - 1].timestamp).total_seconds()
        if 0 < d <= 3600:
            deltas.append(d)
    if not deltas:
        return
    common = min(deltas)
    if common != 300:
        raise ValueError(f"Granularidad esperada 5m (300s), detectada {common}s")


def iter_session_days(bars: list[Bar5m], tz: str = "America/New_York") -> Iterator[tuple[str, list[Bar5m]]]:
    """Agrupa por día de sesión NY. Yields (YYYY-MM-DD, bars_of_day)."""
    z = ZoneInfo(tz)
    current_day: str | None = None
    chunk: list[Bar5m] = []
    for b in bars:
        d = b.timestamp.astimezone(z).date().isoformat()
        if d != current_day:
            if chunk:
                yield current_day, chunk
            current_day = d
            chunk = [b]
        else:
            chunk.append(b)
    if chunk and current_day is not None:
        yield current_day, chunk
