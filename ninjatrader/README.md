# NinjaTrader 8 — Opening Range Breakout 15m

Estrategia NinjaScript (C#) que implementa la spec de [`../docs/strategy.md`](../docs/strategy.md) para futuros (MNQ, MES, NQ, ES).

## Instalación

1. Abrir NinjaTrader 8.
2. Menú **Tools → Import → NinjaScript Add-On**.
3. Importar `OpeningRangeBreakout15m.cs` (o copiarlo a `Documents/NinjaTrader 8/bin/Custom/Strategies/`).
4. **Compilar** desde el NinjaScript Editor (F5).
5. La estrategia aparecerá en *Strategies → OpeningRangeBreakout15m*.

## Configuración del chart

- Aplicar a un chart de **5 minutos** del instrumento deseado (MNQ 06-26, MES 06-26, etc.).
- La sesión por defecto sirve. La estrategia internamente convierte a hora NY (ET) con DST automático.

## Parámetros

| Param | Default | Significado |
|-------|---------|-------------|
| `RiskUSD` | `2000` | Pérdida máxima si stopea con SL original |
| `TpRMultiple` | `0.5` | TP en 0.5R sobre la entrada |
| `AddOnTriggerR` | `0.4` | Trigger del add-on al 0.4R favorable |
| `AddOnSizeMultiple` | `2.5` | Tamaño del add-on como múltiplo del inicial |
| `RangeStart` | `09:30` | Inicio del rango (hora NY) |
| `RangeEnd` | `09:45` | Fin del rango (hora NY) |
| `EntryWindowEnd` | `17:00` | Cierre de ventana para buscar entradas |
| `MaxContracts` | `10` | Cap por propfirm (Apex evaluación = 10 micros) |

## Lógica resumida

```
Estado WaitRangeOpen
  → al llegar 9:30 ET pasa a BuildingRange
Estado BuildingRange
  → entre 9:30-9:45 actualiza H/L del rango
  → al llegar 9:45 pasa a Monitoring (registra Range Width)
Estado Monitoring
  → entre 9:45-17:00 ET, en cada cierre de vela 5m:
    - si Close[1] > rangeHigh → EnterLong
    - si Close[1] < rangeLow  → EnterShort
  → al llegar 17:00 sin entry pasa a Done
Estado Entered
  → en cada tick (Calculate.OnPriceChange) chequea si Close[0]
    cruzó el nivel 0.4R → ejecuta add-on:
    - mete +2.5x contratos a mercado
    - mueve SL de toda la posición al entry original
    - mantiene TP
  → pasa a ScaledIn
Estado ScaledIn / Done
  → espera resolución por TP o SL (no cierra por tiempo)
```

## Cálculo de contratos

```
StopPts        = |entryClose - SL|   (≈ rangeHigh - rangeLow si no hay overshoot)
RiskPerContract = StopPts × PointValue
N initial      = floor(RiskUSD / RiskPerContract)
N add-on       = floor(N initial × 2.5)
```

El sizing usa la distancia real entry → SL: si la vela de ruptura cierra con
overshoot, `N initial` baja para no superar el riesgo. Ejemplo aproximado para
`MNQ` (`PointValue=$2`) con rango de 50 puntos y entrada pegada al rango:
- `RiskPerContract = 50 × $2 = $100`
- `N initial = 20` contratos
- `N add-on = 50` contratos
- **Total al 0.4R = 70 micros**

⚠ Apex en evaluación cap a 10 micros — la estrategia respeta `MaxContracts` y recorta. Para cuentas que permitan más (TopstepX, MFFU funded), ajustar `MaxContracts`.

## Limitaciones conocidas

- **`Calculate.OnPriceChange`** dispara add-on al instante del primer tick que cruza el nivel. Si el cruce es entre velas con gap, dispara al primer tick visible.
- **TimeZone**: usa `Eastern Standard Time` del sistema. En servidores Windows debería resolverse. En Linux/Mac con NinjaTrader puede haber issues — verificar que la ID exista.
- **Cierre forzado de la propfirm**: Apex cierra a las 16:59 ET. Si seguimos en posición a esa hora, el broker la cierra; la estrategia detecta el flat y pasa a Done. Para evitarlo, baja `EntryWindowEnd` a `15:00` o `16:00` y deja margen de runway.
- **No re-entrada** tras stop ni tras TP — sólo 1 trade/día por diseño.

## Testing

Antes de cuenta real:
1. **Playback / Market Replay** con datos históricos (1 mes mínimo).
2. **Sim101** en horario real con datos en vivo, 2 semanas.
3. Solo entonces cuenta de propfirm (evaluación).
