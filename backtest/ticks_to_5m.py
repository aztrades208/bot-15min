"""
Convierte tick data (formato MetaTrader 5 / similar) a velas 5m OHLC.

Input format (TAB-separated):
    <DATE>\\t<TIME>\\t<BID>\\t<ASK>\\t<LAST>\\t<VOLUME>\\t<FLAGS>
    2021.05.24\\t00:00:16.645\\t13401.00\\t13418.00\\t\\t6

Usa LAST cuando está disponible, sino (BID+ASK)/2. Asume timestamps en UTC
(formato típico MT5) — el bot luego convertirá a NY internamente.

Output:
    timestamp,open,high,low,close
    2021-05-24T00:00:00+00:00,13408.50,13412.0,13405.0,13410.25
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Windows: la consola cp1252 no puede imprimir '→' / '⚠' — fuerza UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def convert(in_path: str, out_path: str, source_tz: str = "UTC") -> None:
    print(f"Leyendo ticks de {in_path} ...")
    df = pd.read_csv(
        in_path,
        sep="\t",
        engine="c",
        dtype={"<BID>": "float64", "<ASK>": "float64", "<LAST>": "float64", "<VOLUME>": "Int64"},
    )
    # Normaliza columnas
    df.columns = [c.strip("<>").lower() for c in df.columns]
    print(f"  {len(df):,} ticks leídos. Columnas: {list(df.columns)}")

    # Combine date + time → datetime
    df["ts"] = pd.to_datetime(
        df["date"] + " " + df["time"],
        format="%Y.%m.%d %H:%M:%S.%f",
        errors="coerce",
        utc=False,
    )
    bad = df["ts"].isna().sum()
    if bad:
        print(f"  ⚠ {bad} timestamps no parseables — descartados")
        df = df.dropna(subset=["ts"])

    # Localiza la TZ
    df["ts"] = df["ts"].dt.tz_localize(source_tz)

    # Precio: prioridad LAST → midpoint(BID,ASK) → BID → ASK
    df["price"] = df["last"]
    no_last = df["price"].isna() | (df["price"] == 0)
    has_both = df["bid"].notna() & (df["bid"] > 0) & df["ask"].notna() & (df["ask"] > 0)
    mid_mask = no_last & has_both
    df.loc[mid_mask, "price"] = (df.loc[mid_mask, "bid"] + df.loc[mid_mask, "ask"]) / 2.0

    only_bid = no_last & ~has_both & df["bid"].notna() & (df["bid"] > 0)
    df.loc[only_bid, "price"] = df.loc[only_bid, "bid"]

    only_ask = no_last & ~has_both & df["ask"].notna() & (df["ask"] > 0) & df["price"].isna()
    df.loc[only_ask, "price"] = df.loc[only_ask, "ask"]

    # Filtra precios inválidos
    before = len(df)
    df = df[df["price"].notna() & (df["price"] > 0)]
    if len(df) < before:
        print(f"  ⚠ {before - len(df):,} ticks sin precio válido — descartados")

    # Agrupa a 5min, OHLC
    df = df.set_index("ts").sort_index()
    print(f"  Rango temporal: {df.index[0]} → {df.index[-1]}")
    bars = df["price"].resample("5min", label="left", closed="left").ohlc()
    bars = bars.dropna(subset=["open", "high", "low", "close"], how="any")
    print(f"  {len(bars):,} barras 5m generadas")

    # Output
    out = bars.reset_index().rename(columns={"ts": "timestamp"})
    out["timestamp"] = out["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z").str.replace(
        r"(\+\d{2})(\d{2})$", r"\1:\2", regex=True
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"  → {out_path}  ({Path(out_path).stat().st_size / (1024*1024):.1f} MB)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tz", default="UTC", help="TZ de los ticks (default UTC)")
    args = p.parse_args()
    convert(args.inp, args.out, args.tz)
