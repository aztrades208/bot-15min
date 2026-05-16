# Propfirm Rules Scraper (Fase 2)

**Placeholder para fase 2 del proyecto.**

Agente que actualizará automáticamente los archivos `rules/*.json` consultando:

1. **Web scraping** de las páginas de reglas oficiales (con `playwright` o `httpx + selectolax`).
2. **APIs públicas** donde estén disponibles.
3. **LLM** (Claude) para extraer las reglas del texto bruto y normalizarlas al schema.

## Plan

1. Mantener `sources.yaml` con URLs por firma.
2. Cron diario que:
   - Descarga HTML de cada URL.
   - Diff con la versión anterior cacheada.
   - Si hay cambios significativos, pasa el texto a un agente Claude con el `schema.json` y le pide el JSON actualizado.
   - Crea un PR al repo con el cambio + diff y `last_verified` actualizado.
3. Notificación al usuario si una regla cambia (telegram/email).

## No implementado todavía

Si lo quieres acelerar, dímelo y lo construimos. Por ahora el bot opera con los archivos estáticos en `../rules/`.
