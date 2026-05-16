# Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│                       propfirms/rules/*.json                      │
│    (Apex, Topstep, MFFU, FTMO, Darwinex Zero, Vantage, Axi…)      │
└─────────────────┬─────────────────────────┬──────────────────────┘
                  │                         │
                  │ leído por               │ leído por
                  ▼                         ▼
       ┌──────────────────┐        ┌──────────────────┐
       │  NinjaScript     │        │  Python (mt5/)   │
       │  (futuros)       │        │  (CFDs)          │
       │                  │        │                  │
       │  C# ─ NT8        │        │  strategy.py     │
       │  estrategia      │        │  broker_mt5.py   │
       │  monolítica      │        │  risk_manager.py │
       │                  │        │  main.py         │
       └──────────────────┘        └──────────────────┘
                  │                         │
                  ▼                         ▼
       ┌──────────────────┐        ┌──────────────────┐
       │ NinjaTrader 8    │        │ MetaTrader 5     │
       │ → Rithmic /      │        │ → Vantage /      │
       │   Tradovate /    │        │   Darwinex /     │
       │   CQG            │        │   Axi / FTMO     │
       └──────────────────┘        └──────────────────┘
                                          ▲
                                          │ (fase 2)
                          ┌───────────────┴────────────┐
                          │  propfirms/scraper/        │
                          │  (agente que actualiza     │
                          │   rules/*.json)            │
                          └────────────────────────────┘
```

## Decisiones clave

| Tema | Decisión | Razón |
|------|----------|-------|
| Dos proyectos paralelos | NinjaScript + Python/MT5 | Las APIs y modelos de contratos son distintos. Misma spec, dos motores. |
| Sizing por riesgo fijo | Contratos = floor($2000 / risk_per_contract) | Rango cerrado → más contratos, sin tocar el riesgo. |
| Add-on 2.5x con SL al BE | Única combinación que cumple TP=$1500 + riesgo=$2000 | Ver `strategy.md#matemática-del-add-on`. |
| Reglas propfirm en JSON | Estático ahora, scraping luego | Las webs cambian poco, pero cambian. Empezamos manual. |
| Una operación por día | `one_trade_per_day=True` por defecto | Reglas de consistencia, evitar over-trading. |
| Hold to resolution | No cerrar por tiempo si entró | El usuario quiere ver la operación cumplir hasta TP/SL. |

## Flujo de datos (versión Python)

```
MT5 Terminal ──► copy_rates_from_pos ──► main.py loop
                                            │
                                            ▼ on_new_5m_bar
                                       strategy.py
                                            │
                                            ▼ TradePlan?
                                       risk_manager.py  ◄── propfirms/rules/*.json
                                            │
                                            ▼ allowed?
                                       broker_mt5.py
                                            │
                                            ▼ order_send
                                       MT5 Terminal ──► broker real
```

## Flujo de datos (versión NinjaScript)

NinjaScript es monolítico: una sola clase `OpeningRangeBreakout15m` que NT8 carga directo en la chart. No hay broker adapter porque NT8 ya orquesta la conexión.

Las reglas de propfirm (cap de contratos, etc.) están **leídas a mano** del JSON y reflejadas en el parámetro `MaxContracts` de la estrategia. Una fase 2 puede leer el JSON directamente desde C# usando `Newtonsoft.Json`.

## Estado y persistencia

| Plataforma | Persistencia |
|------------|--------------|
| NinjaScript | En memoria del proceso NT. Si NT se reinicia, el estado se pierde — al re-arrancar, si todavía estamos antes del rango lo construye, si no, espera al día siguiente. |
| Python/MT5 | `state.json` en disco tras cada evento. Sobrevive a reinicios. |

Para máxima resiliencia, recomendable en Windows correr NinjaTrader y/o el script Python como **servicio** o supervisado por NSSM o Task Scheduler.
