# Backtest

Motor de backtest sobre datos históricos de 5 minutos. Reproduce la estrategia
ORB 15m exactamente igual que en producción (mismo `StrategyEngine` de `mt5/`).

## Uso

```bash
cd backtest
python main.py \
  --data data/MNQ_5m.csv \
  --instrument MNQ \
  --data-tz America/New_York \
  --propfirm ../propfirms/rules/apex-50k.json \
  --trades-csv out/trades.csv \
  --summary-json out/summary.json
```

## Formato de datos

Acepta **3 formatos** automáticamente:

1. **CSV estándar con header**:
   ```
   timestamp,open,high,low,close
   2026-01-05T09:30:00-05:00,17000.5,17012.25,16995.75,17008.0
   ```

2. **NinjaTrader 8 con header** (separador `;` o `,`):
   ```
   Date;Time;Open;High;Low;Close;Volume
   20260105;093000;17000.5;17012.25;16995.75;17008.0;1523
   ```

3. **NinjaTrader 8 sin header**:
   ```
   20260105 093000;17000.5;17012.25;16995.75;17008.0;1523
   ```

Si los timestamps son naive (sin TZ), se asume la TZ pasada en `--data-tz`
(default `UTC`). Para datos de NinjaTrader exportados desde un PC en NY,
pasa `--data-tz America/New_York`.

## Cómo exportar datos desde NinjaTrader 8

1. Abre un chart 5m del símbolo (ej. `MNQ 06-26`).
2. Click derecho en el chart → **Save chart data**.
3. Elige formato CSV.
4. Verifica que cubre el período que quieres (NT8 limita historia descargable —
   puede que necesites descargar más con *Tools → Historical Data Manager*).

## Parámetros del backtest

| Flag | Default | Significado |
|------|---------|-------------|
| `--data` | obligatorio | Ruta o glob al CSV |
| `--instrument` | `MNQ` | Preset: MNQ, MES, NQ, ES, US100_CFD, o `custom` con `--custom-spec` |
| `--data-tz` | `UTC` | TZ a asumir si los timestamps son naive |
| `--risk-usd` | `2000` | Riesgo $ por trade |
| `--tp-r` | `0.5` | TP en R |
| `--add-trigger-r` | `0.4` | Trigger del add-on |
| `--add-size-mult` | `2.5` | Tamaño del add-on |
| `--range-start/end` | `09:30/09:45` | Rango NY |
| `--entry-end` | `17:00` | Ventana de entrada NY |
| `--slippage-ticks` | `2.0` | Slippage por orden market (cada lado) |
| `--propfirm` | `null` | Path JSON propfirm para compliance check |
| `--trades-csv` | `null` | Output trades a CSV |
| `--summary-json` | `null` | Output summary a JSON |

## Output

**Stats principales:**
- Trading days, no-trade days
- Win/loss/breakeven count, win rate
- Avg win, avg loss
- Profit factor
- Total P&L, return %
- **Max drawdown** ($ y %)
- Longest win/loss streak
- Tasa de activación del add-on
- Distribución de razones de salida (tp / sl_initial / sl_after_add / eod)

**Compliance check (con `--propfirm`):**
- Final equity vs profit target
- Max drawdown vs floor trailing
- Daily loss violations
- Consistency rule
- Veredicto: PASSED / FAILED

## Limitaciones del simulador

1. **Equity actualizada solo al cierre del trade** — el drawdown intra-trade no
   se contabiliza para reglas trailing intraday (Apex). Resultado: backtest
   ligeramente optimista vs. realidad.
2. **Slippage simétrico** — solo en entrada. TP/SL se asumen llenados exactos
   al precio (no realista en gaps grandes).
3. **Mismo bar TP+SL** — si ambos están dentro del high/low de la misma vela,
   se asume **SL primero** (pesimista, configurable).
4. **No simula spread bid/ask** ni comisiones (añadir comisión: restar del P&L
   total post-hoc; típico MNQ = $1-$2/contrato round-turn).
5. **Add-on intra-bar simplificado** — si la misma vela que cruza el 0.4R
   también toca TP, se asume add-on primero y luego TP (más realista para
   continuación de tendencia).

## Generar datos sintéticos

Para probar el motor sin datos reales:

```bash
python generate_synthetic.py --days 60 --out data/synthetic.csv
```

⚠️ Los datos sintéticos están sesgados y NO son representativos del mercado real.
Solo sirven para smoke test del motor de backtest.

## Workflow recomendado

1. Exportar histórico de MNQ 5m desde NT8 (mínimo 6 meses recomendado).
2. Correr el backtest **sin** propfirm para ver stats base.
3. Correr el backtest **con** propfirm para ver si la cuenta sobreviviría.
4. Ajustar `--slippage-ticks` para ser realista (2-3 ticks para MNQ líquido).
5. Probar varios períodos: trending, choppy, alta volatilidad.
6. Solo después de pasar backtest, ir a SIM con NT8 en horario real.
