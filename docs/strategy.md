# Estrategia: Opening Range Breakout 15m (NY)

## Definiciones

- **R** (riesgo unitario): pérdida en dólares si stopea con la posición INICIAL. Por defecto **$2000** (4% de cuenta 50k).
- **Caja / rango**: máximo y mínimo entre **09:30:00 y 09:44:59 ET** (3 velas de 5m, o 1 de 15m).
- **Ruptura válida**: una vela de **5 minutos** que cierra con su **cuerpo (close)** fuera del rango. La mecha no cuenta. Cierre por encima del High → ruptura alcista. Cierre por debajo del Low → ruptura bajista.

## Flujo del día (hora NY / ET)

```
09:30 ─┐
       │  Construye rango: max(High_5m_1, High_5m_2, High_5m_3),
09:45 ─┘  min(Low_5m_1, Low_5m_2, Low_5m_3)
       
09:45 ─┐
       │  Modo "monitor". Espera vela de 5m con cierre fuera del rango.
17:00 ─┘  
       
17:00 ─  Si no hubo ruptura válida → NO se opera el día.
         Si hubo entrada antes → se mantiene a TP/SL (NO cerrar por tiempo).
```

## Cálculo del sizing inicial

Sea:
- `Range = RangeHigh - RangeLow` (en puntos)
- `TickSize` = tamaño mínimo (ej. 0.25 para MNQ/MES/ES/NQ)
- `TickValue` = valor de un tick ($0.50 para MNQ, $1.25 para MES, $5.00 para NQ, $12.50 para ES)
- `RiskUSD = $2000` (configurable)

```
StopPts = |EntryPrice - SL_initial|         # ≈ Range si la entrada cierra pegada al rango
StopTicks = StopPts / TickSize
RiskPerContract = StopTicks × TickValue
N = floor(RiskUSD / RiskPerContract)        # contratos iniciales
ActualRisk = N × RiskPerContract            # ≤ $2000 (no excede por redondeo)
```

El sizing usa la distancia **real** entry → SL (no el ancho nominal del rango): si la
vela de ruptura cierra con overshoot por encima/debajo del rango, `N` se reduce para
no superar los $2000 de riesgo.

### Ejemplos (MNQ, tick=0.25, tickValue=$0.50)

| Range puntos | StopTicks | RiskPerContract | N contratos | ActualRisk |
|--------------|-----------|-----------------|-------------|------------|
| 20           | 80        | $40             | 50          | $2000      |
| 50           | 200       | $100            | 20          | $2000      |
| 100          | 400       | $200            | 10          | $2000      |
| 150          | 600       | $300            | 6           | $1800      |

Rango cerrado → más contratos. Rango amplio → menos. Riesgo siempre ≤ $2000.
(Los ejemplos asumen entrada exactamente en el borde del rango.)

## Entrada

Cuando se cierra una vela de 5m con su `Close > RangeHigh` (alcista) o `Close < RangeLow` (bajista):

- **Long break**: `EntryPrice = RangeHigh + 1 tick` (Stop-Market triggered) o market a `Open` de la siguiente vela.
- **Short break**: `EntryPrice = RangeLow - 1 tick` o market a `Open` de la siguiente vela.

Implementación recomendada: orden **Market** al cierre de la vela 5m de ruptura. Más realista que stop-on-break (que sufre slippage).

**Filtro de overshoot**: si la vela de ruptura cierra ya más allá del nivel de TP (0.5R), NO se entra — la vela consumió el target y el día queda cerrado.

## Stop Loss inicial

- Long: `SL_initial = RangeLow`
- Short: `SL_initial = RangeHigh`

Distancia: `|EntryPrice - SL_initial| ≈ Range` (puede ser un tick mayor si entry es RangeHigh+1tick).

## Take Profit (objetivo)

Nivel fijo en **0.5R** anclado al borde del rango roto (no al fill real):

- Long: `TP = RangeHigh + 0.5 × Range`
- Short: `TP = RangeLow - 0.5 × Range`

Con sizing inicial (sin add-on) y entrada pegada al rango: `Profit_TP = N × StopTicks × 0.5 × TickValue ≈ 0.5 × RiskUSD = $1000`.

## Trigger de add-on (al 0.4R)

Cuando el precio alcanza el nivel **0.4R** **a favor** (anclado al borde del rango):

- Long: `AddTriggerPrice = RangeHigh + 0.4 × Range`
- Short: `AddTriggerPrice = RangeLow - 0.4 × Range`

Acciones simultáneas:
1. **Añadir 2.5 × N contratos a mercado** al precio actual (≈ AddTriggerPrice).
2. **Mover SL de TODA la posición (original + add-on) al borde del rango roto** (`RangeHigh` en long / `RangeLow` en short = entrada de referencia, ≈ breakeven del original).
3. **Mantener TP fijo** en el nivel 0.5R original.

## Matemática del add-on

Sea `R = $2000`, `N` contratos iniciales, `M = 2.5 × N` contratos add-on, `Range` puntos de stop, `TickValue × StopTicks = R/N` por contrato por movimiento de 1R.

**Si gana (precio toca TP en 0.5R):**

| Posición | Entry | Exit | Movimiento (R) | Contratos | P&L |
|----------|-------|------|----------------|-----------|------|
| Original | 0R    | 0.5R | +0.5R          | N         | +0.5 × N × (R/N) = **$1000** |
| Add-on   | 0.4R  | 0.5R | +0.1R          | 2.5N      | +0.1 × 2.5N × (R/N) = **$500**  |
| **Total**|       |      |                |           | **+$1500** (3% de 50k) ✓ |

**Si pierde (precio vuelve al SL combinado en 0R):**

| Posición | Entry | Exit | Movimiento (R) | Contratos | P&L |
|----------|-------|------|----------------|-----------|------|
| Original | 0R    | 0R   | 0              | N         | **$0** |
| Add-on   | 0.4R  | 0R   | −0.4R          | 2.5N      | −0.4 × 2.5N × (R/N) = **−$2000** |
| **Total**|       |      |                |           | **−$2000** (= R original) ✓ |

**Si pierde antes del add-on (precio nunca llega al 0.4R y vuelve al SL inicial en `-1R`):**

| Posición | Entry | Exit | Movimiento (R) | Contratos | P&L |
|----------|-------|------|----------------|-----------|------|
| Original | 0R    | −1R  | −1R            | N         | **−$2000** |
| **Total**|       |      |                |           | **−$2000** ✓ |

Riesgo siempre acotado a $2000 en cualquier escenario.

## Estado de máquina

```
INIT
  │
  ▼
WAIT_RANGE_OPEN    (espera 09:30 ET)
  │
  ▼
BUILDING_RANGE    (09:30 - 09:45 ET) → registra H/L
  │
  ▼
MONITORING        (09:45 - 17:00 ET, busca ruptura 5m)
  │  vela 5m cerrada con cuerpo fuera de rango
  ▼
ENTERED           (long o short, SL=opuesto, TP=0.5R)
  │  precio toca 0.4R
  ▼
SCALED_IN         (add-on +2.5N, SL combinado movido a entry original)
  │  hit TP o SL
  ▼
DONE              (1 trade por día — no re-entrada)


Si llega 17:00 ET en MONITORING sin ruptura → DONE (sin operar)
```

## Reglas operativas

- **Solo 1 trade por día**: gane o pierda, no se vuelve a entrar.
- **No cierres por tiempo**: una vez en `ENTERED` o `SCALED_IN`, solo TP/SL cierran.
- **No traillings adicionales** más allá del movimiento del SL al 0.4R.
- **No partial fills planificados** (todo se ejecuta a tamaño completo).
- **No re-entry tras stop**: el día queda cerrado.
- **Slippage tolerado**: configurable, default 2 ticks por orden.

## Reglas de propfirm (capa adicional)

Antes de operar, el bot consulta el archivo de la propfirm activa (`propfirms/rules/<firma>.json`):

- **MaxDailyLoss**: si la pérdida acumulada del día se acerca, ajusta riesgo o suspende.
- **TrailingDrawdown**: si la equity está cerca del trailing, suspende.
- **ProfitTarget**: si ya se alcanzó, opcionalmente reduce riesgo.
- **ConsistencyRule**: limita el % de profit del día más grande.
- **MinTradingDays**: rastrea días operados.
- **NewsBlackout**: si está cerca de noticias high-impact (NFP, FOMC), bloquea entrada (Fase 2).

## Parametrización

Todos estos valores son configurables por archivo:

```yaml
strategy:
  range_start: "09:30"   # NY/ET
  range_end:   "09:45"
  entry_window_end: "17:00"
  risk_usd: 2000
  tp_r_multiple: 0.5
  add_on_trigger_r: 0.4
  add_on_size_multiple: 2.5     # del tamaño original
  move_sl_to_entry_on_add: true
  one_trade_per_day: true
  hold_until_resolution: true   # no cierra por tiempo
  body_breakout_only: true      # mecha no cuenta
  breakout_timeframe_minutes: 5

instrument:
  symbol: "MNQ"
  tick_size: 0.25    # en MT5 se leen automáticamente de symbol_info del broker
  tick_value: 0.50

propfirm:
  active_rules_file: "propfirms/rules/apex-50k.json"
```
