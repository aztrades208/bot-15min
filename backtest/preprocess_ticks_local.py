#!/usr/bin/env python3
"""
Pre-procesa tick data MT5/NT8 → CSV de 5m OHLC, en chunks para no agotar RAM.

Uso (en tu PC con el CSV grande):
    pip install pandas pyarrow
    python preprocess_ticks_local.py \\
        --in NQ_M_202105_202605.csv \\
        --out nq_5m.csv \\
        --tz UTC \\
        --chunk-rows 2000000

Resultado: un nq_5m.csv de ~10MB que ya puedes subir directo al chat o al repo.
Después en el container hago: `gzip nq_5m.csv` → ~3MB y commit a git directo.

El archivo grande SE QUEDA EN TU PC. Solo subes el agregado.
"""
from __future__ import annotations

import argparse
import gc
import sys
from pathlib import Path

import pandas as pd

# Windows: la consola cp1252 no puede imprimir '→' — fuerza UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def preprocess(in_path: str, out_path: str, tz: str = "UTC",
               chunk_rows: int = 2_000_000) -> None:
    print(f"Procesando {in_path} en chunks de {chunk_rows:,} filas ...")
    p_in = Path(in_path)
    p_out = Path(out_path)
    p_out.parent.mkdir(parents=True, exist_ok=True)

    # Detecta el separador: NT8/MT5 normalmente usan tab
    with open(p_in, "r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline()
    sep = "\t" if "\t" in first_line else (";" if ";" in first_line else ",")
    print(f"  separador detectado: {repr(sep)}")
    print(f"  header: {first_line.strip()[:120]}")

    all_bars: list[pd.DataFrame] = []
    total_rows = 0

    reader = pd.read_csv(
        in_path,
        sep=sep,
        engine="c",
        chunksize=chunk_rows,
        dtype=str,  # parse manual para no fallar en filas raras
    )

    for i, chunk in enumerate(reader):
        chunk.columns = [c.strip("<>").lower() for c in chunk.columns]
        total_rows += len(chunk)
        print(f"  chunk {i+1}: {len(chunk):,} filas (acum {total_rows:,})")

        # Combina fecha + hora
        ts = pd.to_datetime(
            chunk["date"].astype(str) + " " + chunk["time"].astype(str),
            format="%Y.%m.%d %H:%M:%S.%f",
            errors="coerce",
        )
        bad = ts.isna().sum()
        if bad:
            chunk = chunk[ts.notna()].copy()
            ts = ts.dropna()

        # Precio prioridad LAST → mid → BID → ASK
        last = pd.to_numeric(chunk.get("last", pd.Series()), errors="coerce")
        bid = pd.to_numeric(chunk.get("bid", pd.Series()), errors="coerce")
        ask = pd.to_numeric(chunk.get("ask", pd.Series()), errors="coerce")

        price = last.where(last.notna() & (last > 0))
        mid = (bid + ask) / 2.0
        price = price.fillna(mid.where(bid.notna() & ask.notna() & (bid > 0) & (ask > 0)))
        price = price.fillna(bid.where(bid.notna() & (bid > 0)))
        price = price.fillna(ask.where(ask.notna() & (ask > 0)))

        df = pd.DataFrame({"ts": ts.values, "price": price.values})
        df = df.dropna(subset=["price"])
        df = df[df["price"] > 0]

        if df.empty:
            continue
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(tz)
        df = df.set_index("ts").sort_index()
        bars = df["price"].resample("5min", label="left", closed="left").ohlc().dropna()
        all_bars.append(bars)

        del chunk, ts, last, bid, ask, price, mid, df
        gc.collect()

    if not all_bars:
        print("Sin datos procesables.")
        return

    print(f"Concatenando {len(all_bars)} chunks ...")
    full = pd.concat(all_bars).sort_index()
    # Re-agrega por si dos chunks se solapan en el mismo bucket de 5m
    full = full.groupby(level=0).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    print(f"  total barras 5m: {len(full):,}")
    print(f"  rango temporal: {full.index[0]} → {full.index[-1]}")

    out = full.reset_index().rename(columns={"ts": "timestamp"})
    out["timestamp"] = out["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z").str.replace(
        r"(\+\d{2})(\d{2})$", r"\1:\2", regex=True
    )
    out.to_csv(p_out, index=False)
    size_mb = p_out.stat().st_size / (1024 * 1024)
    print(f"  → {p_out} ({size_mb:.1f} MB)")
    print()
    print("Subida sugerida: gzip nq_5m.csv → nq_5m.csv.gz, súbelo al chat o al repo.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True, help="Tick CSV original (MT5/NT8)")
    p.add_argument("--out", required=True, help="Output CSV 5m OHLC")
    p.add_argument("--tz", default="UTC", help="TZ asumida de los ticks")
    p.add_argument("--chunk-rows", type=int, default=2_000_000,
                   help="Filas por chunk (default 2M, baja si tu PC tiene poca RAM)")
    args = p.parse_args()
    preprocess(args.inp, args.out, args.tz, args.chunk_rows)
