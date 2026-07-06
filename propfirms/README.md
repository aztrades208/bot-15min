# Prop Firm Rules

Archivos JSON con las reglas operativas de cada propfirm. El bot los lee en arranque para conocer límites (drawdown, daily loss, consistency, contratos máximos, etc.) y ajustar comportamiento.

## Estructura

```
propfirms/
├── schema.json          Esquema JSON de validación
├── rules/
│   ├── apex-50k.json
│   ├── topstep-50k.json
│   ├── mffu-50k.json
│   ├── takeprofit-50k.json
│   ├── lucid-50k.json
│   ├── ftmo-50k.json
│   ├── darwinex-zero.json
│   ├── vantage-prop.json
│   └── axi-select.json
└── scraper/             (Fase 2: scraping/API para auto-actualizar)
```

## Cómo añadir una propfirm

1. Copiar `rules/apex-50k.json` como template.
2. Rellenar todos los campos según las reglas oficiales.
3. Validar con el `schema.json`.
4. Actualizar `last_verified` con la fecha.
5. Anotar `source_url` (web oficial donde se verificaron).

## Campos clave

| Campo | Significado |
|-------|-------------|
| `type` | `futures` (usa motor NinjaTrader) o `cfd` (usa motor MT5) |
| `drawdown.mode` | `trailing_intraday`, `trailing_eod`, `static`, `max_loss`, `static_then_lock` |
| `drawdown.amount_usd` | Cantidad del drawdown (siempre positiva) |
| `drawdown.lock_at_usd` | Para trailing: equity al que se bloquea |
| `max_daily_loss_usd` | Pérdida máxima en un día (TopStep tiene; Apex no) |
| `consistency_rule_pct` | % máximo de profit de un día sobre profit total |
| `min_trading_days` | Días operativos mínimos antes de poder retirar |
| `max_contracts` / `max_lot_size` | Tope por trade |
| `allow_overnight` | Si permite mantener posiciones overnight |
| `scaling_plan_required` | Si tiene scaling plan (Apex) |

## Aviso sobre las reglas

Las reglas pueden cambiar sin previo aviso. **Verificar siempre con la web oficial antes de operar real.** El campo `last_verified` indica la última fecha de revisión manual.

La Fase 2 del proyecto incluye un agente que automatiza esta actualización mediante scraping o API.
