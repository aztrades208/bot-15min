"""
CLI del backtest. Carga datos, ejecuta la estrategia, imprime el summary y
opcionalmente comprueba cumplimiento de reglas de propfirm.

Uso:
    python main.py --data data/MNQ_5m.csv \\
                   --instrument MNQ \\
                   --propfirm ../propfirms/rules/apex-50k.json \\
                   --trades-csv out/trades.csv \\
                   --summary-json out/summary.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mt5"))

from strategy import InstrumentSpec, StrategyParams  # noqa: E402
from data_loader import load_csv  # noqa: E402
from engine import ExecutionConfig, run_backtest  # noqa: E402
from reporter import (  # noqa: E402
    summarize, print_summary, write_trades_csv, write_summary_json,
    check_propfirm_compliance,
)


PRESET_INSTRUMENTS = {
    "MNQ": InstrumentSpec("MNQ", 0.25, 0.50, 1, 1, 100),
    "MES": InstrumentSpec("MES", 0.25, 1.25, 1, 1, 100),
    "NQ":  InstrumentSpec("NQ",  0.25, 5.00, 1, 1, 100),
    "ES":  InstrumentSpec("ES",  0.25, 12.50, 1, 1, 100),
    "US100_CFD": InstrumentSpec("US100", 0.1, 0.10, 0.01, 0.01, 100),
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="CSV (o glob) con barras 5m")
    p.add_argument("--instrument", default="MNQ", help="Preset o 'custom'")
    p.add_argument("--custom-spec", help="JSON con InstrumentSpec si instrument=custom")
    p.add_argument("--data-tz", default="UTC", help="TZ asumida si timestamps son naive")
    p.add_argument("--risk-usd", type=float, default=2000.0)
    p.add_argument("--tp-r", type=float, default=0.5)
    p.add_argument("--add-trigger-r", type=float, default=0.4)
    p.add_argument("--add-size-mult", type=float, default=2.5)
    p.add_argument("--range-start", default="09:30")
    p.add_argument("--range-end", default="09:45")
    p.add_argument("--entry-end", default="17:00")
    p.add_argument("--tz", default="America/New_York")
    p.add_argument("--slippage-ticks", type=float, default=2.0)
    p.add_argument("--propfirm", help="ruta JSON propfirm para compliance check")
    p.add_argument("--trades-csv", help="output: trades CSV")
    p.add_argument("--summary-json", help="output: summary JSON")
    p.add_argument("--starting-balance", type=float, default=50000.0)
    args = p.parse_args()

    if args.instrument == "custom":
        if not args.custom_spec:
            print("custom requiere --custom-spec con JSON")
            return 2
        import json
        spec_dict = json.loads(Path(args.custom_spec).read_text())
        spec = InstrumentSpec(**spec_dict)
    else:
        if args.instrument not in PRESET_INSTRUMENTS:
            print(f"Instrument desconocido. Presets: {list(PRESET_INSTRUMENTS)}")
            return 2
        spec = PRESET_INSTRUMENTS[args.instrument]

    params = StrategyParams(
        range_start=args.range_start,
        range_end=args.range_end,
        entry_window_end=args.entry_end,
        timezone=args.tz,
        risk_usd=args.risk_usd,
        tp_r_multiple=args.tp_r,
        add_on_trigger_r=args.add_trigger_r,
        add_on_size_multiple=args.add_size_mult,
    )
    exec_cfg = ExecutionConfig(slippage_ticks=args.slippage_ticks)

    print(f"Cargando {args.data} (tz asumida: {args.data_tz})...")
    bars = load_csv(args.data, assume_tz=args.data_tz)
    print(f"  {len(bars)} barras cargadas.")
    print(f"  desde {bars[0].timestamp} hasta {bars[-1].timestamp}")

    print(f"Backtesting {spec.symbol} risk=${args.risk_usd:.0f}...")
    result = run_backtest(bars, params, spec, exec_cfg)
    summary = summarize(result, starting_balance=args.starting_balance)
    print_summary(summary)

    if args.propfirm:
        print()
        compliance = check_propfirm_compliance(
            result, args.propfirm, starting_balance=args.starting_balance
        )
        print("=" * 72)
        print(f"Propfirm Compliance — {compliance['propfirm']}")
        print("=" * 72)
        print(f"Final equity:     ${compliance['final_equity']:.2f}")
        print(f"Max drawdown:     ${compliance['max_dd_dollar']:.2f}")
        if compliance["profit_target_usd"] > 0:
            print(f"Profit target:    ${compliance['profit_target_usd']:.0f}"
                  f"  → {'HIT ✓' if compliance['profit_target_hit'] else 'NOT HIT ✗'}")
        if compliance["violations"]:
            print(f"Violations: {len(compliance['violations'])}")
            for v in compliance["violations"][:10]:
                print(f"  - {v}")
            if len(compliance["violations"]) > 10:
                print(f"  ... y {len(compliance['violations']) - 10} más")
        else:
            print("Violations:       NONE ✓")
        print(f"Result:           {'PASSED' if compliance['passed'] else 'FAILED'}")
        print("=" * 72)

    if args.trades_csv:
        write_trades_csv(result, args.trades_csv)
        print(f"Trades → {args.trades_csv}")
    if args.summary_json:
        write_summary_json(summary, args.summary_json)
        print(f"Summary → {args.summary_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
