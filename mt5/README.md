# MT5 — Opening Range Breakout 15m (CFDs)

Bot Python + MetaTrader5 que implementa la misma spec que el NinjaScript pero para CFDs en MT5 (Darwinex Zero, Vantage, Axi Select, FTMO).

## Estructura

```
mt5/
├── strategy.py          Motor puro de estrategia (sin dependencias del broker)
├── broker_mt5.py        Adapter MT5 (Python API oficial)
├── risk_manager.py      Comprueba reglas de propfirm (drawdown, daily loss, caps)
├── main.py              Loop principal y persistencia de estado
├── config.example.yaml  Plantilla de configuración
└── requirements.txt
```

## Instalación

Requiere **Windows** con MT5 terminal instalado (la librería oficial `MetaTrader5` solo funciona en Windows).

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
# editar config.yaml con tus credenciales MT5 y propfirm
python main.py --config config.yaml
```

## Configuración (`config.yaml`)

| Sección | Campo | Significado |
|---------|-------|-------------|
| `strategy` | `range_start/end` | Hora NY del rango (default 09:30 - 09:45) |
| `strategy` | `entry_window_end` | Hora NY tope para buscar entradas (default 17:00) |
| `strategy` | `risk_usd` | Pérdida máxima del trade inicial |
| `strategy` | `tp_r_multiple` | TP en R (default 0.5) |
| `strategy` | `add_on_trigger_r` | Trigger del add-on (default 0.4) |
| `strategy` | `add_on_size_multiple` | Tamaño add-on (default 2.5x) |
| `instrument` | `symbol` | Símbolo MT5 (ej. `US100`, `EURUSD`) |
| `broker` | `login`/`password`/`server` | Credenciales MT5 |
| `propfirm` | `active_rules_file` | Ruta al JSON de la propfirm |

## Sizing en CFDs

Difiere de futuros: aquí el "tamaño" es **lotes** (0.01 mínimo, 0.01 step).

```
dollar_per_point_per_lot = tick_value / tick_size
risk_per_lot = stop_pts × dollar_per_point_per_lot
lots = floor(risk_usd / risk_per_lot)
```

Ejemplo US100 en Vantage (tick_size=0.1, tick_value=$0.10 por mini-lote):
- `dpp/lot = $0.10 / 0.1 = $1/pt/lot`
- Rango de 50 puntos: `risk_per_lot = 50 × $1 = $50`
- `lots = 2000/50 = 40 lotes`

⚠ El bot lee `symbol_info` de MT5 al arranque para obtener valores reales — no hace falta hardcoding.

## Persistencia

El bot guarda su estado de máquina (`state.json`) tras cada evento. Si se reinicia, retoma sin perder el contexto del día.

## Limitaciones

- **Solo Windows**: la API MetaTrader5 no funciona en Linux/Mac.
- **Una sola terminal MT5**: la API conecta a UNA terminal abierta. Para multi-cuenta, abrir varias terminales con `terminal_path` distinto.
- **`realized_today`** en risk_manager está en `0.0` placeholder — implementar consulta de `history_deals_get` para precisión.
- **Slippage**: el motor usa `deviation_points` para tolerancia. En noticias de alto impacto puede rechazar órdenes.

## Testing

```bash
python -m pytest ../tests/
```

Tests unitarios cubren la lógica de `strategy.py` (sin tocar MT5). Para test end-to-end usar **cuenta demo MT5** primero.
