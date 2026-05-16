"""
Reporta resultados del backtest. Stats típicas + curva de equity + análisis
de cumplimiento de reglas de propfirm (drawdown trailing, daily loss).
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from engine import BacktestResult, Trade


def summarize(result: BacktestResult, starting_balance: float = 50000.0) -> dict:
    n = len(result.trades)
    if n == 0:
        return {"trades": 0, "no_trade_days": result.no_trade_days, "total_days": result.total_days}

    wins = [t for t in result.trades if t.pnl_usd > 0]
    losses = [t for t in result.trades if t.pnl_usd < 0]
    breakeven = [t for t in result.trades if t.pnl_usd == 0]

    total_pnl = sum(t.pnl_usd for t in result.trades)
    avg_win = sum(t.pnl_usd for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl_usd for t in losses) / len(losses) if losses else 0.0
    gross_win = sum(t.pnl_usd for t in wins)
    gross_loss = -sum(t.pnl_usd for t in losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    # Equity curve & max drawdown
    equity = starting_balance
    peak = equity
    max_dd = 0.0
    max_dd_dollar = 0.0
    curve = [(None, equity)]
    for t in result.trades:
        equity += t.pnl_usd
        peak = max(peak, equity)
        dd_dollar = peak - equity
        if dd_dollar > max_dd_dollar:
            max_dd_dollar = dd_dollar
            max_dd = dd_dollar / peak
        curve.append((t.session_date, equity))

    longest_loss_streak = _longest_streak([t.pnl_usd < 0 for t in result.trades])
    longest_win_streak = _longest_streak([t.pnl_usd > 0 for t in result.trades])

    add_on_rate = sum(1 for t in result.trades if t.add_on_executed) / n
    exit_reasons: dict[str, int] = {}
    for t in result.trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    return {
        "trades": n,
        "no_trade_days": result.no_trade_days,
        "total_days": result.total_days,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": len(wins) / n,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "total_pnl_usd": total_pnl,
        "gross_win_usd": gross_win,
        "gross_loss_usd": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd,
        "max_drawdown_usd": max_dd_dollar,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "add_on_rate": add_on_rate,
        "exit_reasons": exit_reasons,
        "starting_balance": starting_balance,
        "final_balance": equity,
        "return_pct": (equity - starting_balance) / starting_balance,
    }


def _longest_streak(flags: list[bool]) -> int:
    longest = 0
    current = 0
    for f in flags:
        if f:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def print_summary(summary: dict, result: Optional["BacktestResult"] = None) -> None:
    print("=" * 72)
    print(f"Backtest Summary")
    print("=" * 72)
    print(f"Days in sample:        {summary['total_days']}")
    print(f"Trading days:          {summary['trades']}")
    print(f"No-trade days:         {summary['no_trade_days']}")
    if result is not None and result.gap_warnings:
        print(f"Gap warnings:          {len(result.gap_warnings)}  (posibles rollovers)")
        for g in result.gap_warnings[:5]:
            print(f"  - {g.session_date}: gap {g.gap_points:.1f} pts  ({g.prev_close} → {g.next_open})")
        if len(result.gap_warnings) > 5:
            print(f"  ... y {len(result.gap_warnings) - 5} más")
    if summary.get("trades", 0) == 0:
        return
    print(f"Wins / Losses / BE:    {summary['wins']} / {summary['losses']} / {summary['breakeven']}")
    print(f"Win rate:              {summary['win_rate']:.1%}")
    print(f"Avg win:               ${summary['avg_win_usd']:.2f}")
    print(f"Avg loss:              ${summary['avg_loss_usd']:.2f}")
    print(f"Profit factor:         {summary['profit_factor']:.2f}")
    print(f"Total P&L:             ${summary['total_pnl_usd']:.2f}")
    print(f"Return:                {summary['return_pct']:.2%}")
    print(f"Max drawdown:          ${summary['max_drawdown_usd']:.2f} ({summary['max_drawdown_pct']:.2%})")
    print(f"Longest win streak:    {summary['longest_win_streak']}")
    print(f"Longest loss streak:   {summary['longest_loss_streak']}")
    print(f"Add-on triggered:      {summary['add_on_rate']:.1%} of trades")
    print(f"Exit reasons:          {summary['exit_reasons']}")
    print(f"Starting / Final:      ${summary['starting_balance']:.0f} → ${summary['final_balance']:.2f}")
    print("=" * 72)


def write_trades_csv(result: BacktestResult, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not result.trades:
        p.write_text("", encoding="utf-8")
        return
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(result.trades[0]).keys()))
        w.writeheader()
        for t in result.trades:
            w.writerow(asdict(t))


def write_summary_json(summary: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


def check_propfirm_compliance(
    result: BacktestResult,
    rules_file: str,
    starting_balance: Optional[float] = None,
) -> dict:
    """
    Comprueba si el backtest cumple las reglas de la propfirm: drawdown,
    daily loss, consistency. NO modifica el backtest — solo reporta violations.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mt5"))
    from risk_manager import PropFirmRules

    rules = PropFirmRules.from_file(rules_file)
    sb = starting_balance if starting_balance is not None else rules.account_size_usd

    violations: list[str] = []
    equity = sb
    peak = sb
    max_dd_dollar = 0.0
    daily_pnl: dict[str, float] = {}

    floor_static = sb - rules.drawdown_amount_usd

    for t in result.trades:
        equity += t.pnl_usd
        peak = max(peak, equity)
        max_dd_dollar = max(max_dd_dollar, peak - equity)

        # Daily loss check
        daily_pnl[t.session_date] = daily_pnl.get(t.session_date, 0.0) + t.pnl_usd
        if rules.max_daily_loss_usd is not None:
            if daily_pnl[t.session_date] < -rules.max_daily_loss_usd:
                violations.append(
                    f"{t.session_date}: daily loss ${daily_pnl[t.session_date]:.0f} > -${rules.max_daily_loss_usd:.0f}"
                )

        # Trailing drawdown
        if rules.drawdown_mode in ("trailing_intraday", "trailing_eod"):
            locked = rules.drawdown_lock_at_usd is not None and peak >= rules.drawdown_lock_at_usd
            floor_ = sb if locked else (peak - rules.drawdown_amount_usd)
            if equity < floor_:
                violations.append(
                    f"{t.session_date}: equity ${equity:.0f} < floor ${floor_:.0f} (trailing)"
                )
        else:
            if equity < floor_static:
                violations.append(
                    f"{t.session_date}: equity ${equity:.0f} < floor ${floor_static:.0f} (static)"
                )

    # Consistency
    consistency_ok = True
    if rules.consistency_rule_pct is not None and result.trades:
        total = sum(t.pnl_usd for t in result.trades if t.pnl_usd > 0)
        if total > 0:
            max_day = max(
                sum(t.pnl_usd for t in result.trades if t.session_date == d and t.pnl_usd > 0)
                for d in {t.session_date for t in result.trades}
            )
            pct = max_day / total
            if pct > rules.consistency_rule_pct / 100:
                consistency_ok = False
                violations.append(
                    f"consistency: max day = {pct:.1%} of total wins (límite {rules.consistency_rule_pct}%)"
                )

    target_hit = (equity - sb) >= rules.profit_target_usd if rules.profit_target_usd > 0 else None
    return {
        "propfirm": rules.name,
        "final_equity": equity,
        "max_dd_dollar": max_dd_dollar,
        "profit_target_usd": rules.profit_target_usd,
        "profit_target_hit": target_hit,
        "consistency_ok": consistency_ok,
        "violations": violations,
        "passed": (target_hit is True or rules.profit_target_usd == 0) and not violations,
    }
