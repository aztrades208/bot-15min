# bot-15min — Opening Range Breakout (NY 9:30-9:45)

Bot de trading que opera el rango de apertura de Nueva York (9:30 - 9:45 ET) y entra con ruptura de cuerpo en 5 minutos. Diseñado para pasar/operar cuentas de fondeo (prop firms) en futuros y CFDs.

## Estrategia (resumen)

1. **Caja**: rango High/Low de las velas de 15m entre 9:30 y 9:45 ET.
2. **Entrada**: vela de 5m que cierra con cuerpo (close) fuera del rango → entrada market en la dirección de la ruptura.
3. **SL inicial**: extremo opuesto del rango 15m. Riesgo total fijo $2000 (4% de cuenta 50k).
4. **TP**: nivel 0.5R sobre el borde del rango roto (ej. ~$1000 con sizing inicial).
5. **Add-on al 0.4R**: añadir 2.5x el tamaño original, mover SL combinado al precio de entrada original (breakeven del original). Si stopea = -$2000. Si llega al TP = $1500 (3% de cuenta 50k).
6. **Sizing**: contratos calculados dinámicamente para que `(N × stop_ticks × tick_value) = $2000`. Rango cerrado → más contratos. Rango amplio → menos.
7. **Ventana de entrada**: hasta las 17:00 ET. Si no hay ruptura válida, no se opera el día.
8. **Solo 1 trade por día**. Una vez en posición, NO se cierra antes de TP/SL.

Spec completa: [`docs/strategy.md`](docs/strategy.md)
Matemática del add-on: [`docs/strategy.md#matemática-del-add-on`](docs/strategy.md#matemática-del-add-on)

## Estructura

```
bot-15min/
├── backtest/              Motor de backtest sobre datos históricos de 5m
├── docs/                  Spec de la estrategia y arquitectura
├── ninjatrader/           NinjaScript (C#) para futuros MNQ/MES en NinjaTrader 8
├── mt5/                   Python + MetaTrader5 para CFDs (Darwinex Zero, Vantage, Axi)
├── propfirms/             Reglas por prop firm (estático ahora, scraping después)
│   ├── rules/             JSON por firma con drawdown, target, consistencia, etc.
│   └── scraper/           Agente futuro para auto-actualizar reglas
└── tests/                 Tests de la lógica de estrategia
```

## Plataformas soportadas

| Plataforma     | Tipo     | Instrumentos típicos | Carpeta         |
|----------------|----------|----------------------|-----------------|
| NinjaTrader 8  | Futuros  | MNQ, MES, NQ, ES     | `ninjatrader/`  |
| MetaTrader 5   | CFDs/FX  | US100, US500, EURUSD | `mt5/`          |

## Prop firms soportadas (config inicial)

Futuros: Apex Trader Funding, TopStep, MyFundedFutures, TakeProfit Trader, LucidFlex.
CFDs: Darwinex Zero, Vantage Prop, Axi Select, FTMO.

Cada una con su archivo en `propfirms/rules/<firma>.json` definiendo trailing/EOD drawdown, profit target, consistencia, días mínimos, etc.

## Estado del proyecto

- [x] Spec de estrategia documentada
- [x] Estructura de propfirms con reglas top firms
- [x] NinjaScript: estrategia ORB con add-on
- [x] Python/MT5: motor de estrategia + adapter MT5
- [x] Tests unitarios de cálculo de sizing y add-on
- [ ] Agente de scraping de propfirms (Fase 2)
- [x] Backtesting framework

## Disclaimer

Este bot ejecuta órdenes reales. Probarlo SIEMPRE en simulado/demo antes de cuenta real. El usuario es responsable de cumplir las reglas de su prop firm. El autor no garantiza resultados.
