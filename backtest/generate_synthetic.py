"""
Genera un CSV de 5m sintético para probar el backtester sin datos reales.
Simula N días donde:
- 9:30-9:45: rango aleatorio entre 30 y 80 puntos
- 9:45-17:00: tendencia aleatoria (alcista, bajista o lateral)
- Movimientos ~ random walk con drift

NO es un backtest realista — es solo para verificar la mecánica del motor.
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def gen(days: int, out: str, base_price: float = 17000.0, seed: int = 42) -> None:
    rng = random.Random(seed)
    ny = ZoneInfo("America/New_York")
    rows: list[dict] = []
    current_price = base_price

    # Empezar lunes
    day = datetime(2026, 1, 5, 9, 30, tzinfo=ny)  # lunes
    days_added = 0
    while days_added < days:
        if day.weekday() >= 5:
            day = (day + timedelta(days=1)).replace(hour=9, minute=30)
            continue

        # Rango del día
        range_width = rng.uniform(30, 80)
        bias = rng.choice(["bull", "bear", "chop", "chop", "chop"])

        # Fase 1: construir rango (3 barras 9:30 9:35 9:40)
        range_high = current_price + range_width * 0.5
        range_low = current_price - range_width * 0.5
        for i in range(3):
            t = day + timedelta(minutes=5 * i)
            o = current_price
            h = rng.uniform(current_price, range_high)
            l = rng.uniform(range_low, current_price)
            c = rng.uniform(l, h)
            rows.append({"timestamp": t.isoformat(), "open": o, "high": h, "low": l, "close": c})
            current_price = c

        # Fase 2: 9:45 - 17:00 (88 barras de 5m)
        bars_after = 88
        for i in range(bars_after):
            t = day + timedelta(minutes=15 + 5 * i)
            drift = 0.0
            if bias == "bull":
                drift = range_width * 0.012
            elif bias == "bear":
                drift = -range_width * 0.012
            noise = rng.gauss(0, range_width * 0.08)
            move = drift + noise
            o = current_price
            c = o + move
            h = max(o, c) + abs(rng.gauss(0, range_width * 0.04))
            l = min(o, c) - abs(rng.gauss(0, range_width * 0.04))
            rows.append({"timestamp": t.isoformat(), "open": o, "high": h, "low": l, "close": c})
            current_price = c

        days_added += 1
        day = (day + timedelta(days=1)).replace(hour=9, minute=30)

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Generadas {days} sesiones ({len(rows)} barras) → {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--out", default="data/synthetic.csv")
    p.add_argument("--base", type=float, default=17000.0)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    gen(args.days, args.out, args.base, args.seed)
