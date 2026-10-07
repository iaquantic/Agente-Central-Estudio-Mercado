# Contrato de datos del panel (v1.0)

`GET /api/panel` devuelve el JSON con el que se dibuja el panel (y que el bot usa para `/oportunidades` y el resumen).
Campos en español; los bloques que vienen tal cual del Agente Interno conservan sus nombres en inglés.

| Campo | Origen | Contenido |
|---|---|---|
| `version` | Central | `"1.0"` |
| `meta` | Central | `empresa{id,nombre,descripcion,pais,moneda,moneda_local}`, `titulo`, `proveedor`, `generado` (hora real del estudio), `estudio` (fecha real del estudio), `periodo{desde,hasta}` (del negocio), `demo`, `agente_central`, `zona_horaria`, `umbral_precio_pct`, `fuentes_mercado[]` |
| `resumen` | Interno · `get_business_summary` | Día y mes en curso, esperado, alertas por prioridad |
| `ventas_mensuales[]` | Interno · `get_sales_summary` (`group_by=month`) | 12 meses: `key`, `net_usd`, `gross_margin_pct`, `tickets`, `units` |
| `ventas_mes` | Interno · `get_sales_summary` (`category`, `previous_period`) | `totales`, `comparacion`, `categorias[]` |
| `rentabilidad_categorias[]`, `margen_global_pct` | Interno · `get_margin_analysis` (`category`) | Margen por categoría y su variación |
| `mas_vendidos[]`, `menos_vendidos[]` | Interno · `get_top_products` (`units`, top / bottom con ceros) | `panel.top_n` productos |
| `mas_rentables[]`, `menos_rentables[]` | Interno · `get_margin_analysis` (`product`, desc / asc) | Margen, beneficio, variación de coste y precio |
| `inventario` | Interno · `get_inventory_status` (`all_issues`) | Productos con problemas y totales por estado |
| `alertas` | Interno · `get_alerts` | Alertas activas y conteo por prioridad |
| `tasa` | Interno · `get_exchange_rate` | `actual`, `serie[]` (2 meses), `variacion`, `impacto` |
| `productos[]` | Central · cruce | Por producto vigilado: `sku`, `nombre`, `categoria`, `interno{…}`, `mercado{referencia_usd, p25_usd, p75_usd, n, mediana_usd, mediana_cup, variacion_usd_pct, tendencia_controlador, serie_usd[], anuncios, vendedores, señales[], confianza, fuentes[], limitaciones[]}`, `posicion{diferencia_pct, etiqueta}`, `propuestas[]`, `estado` |
| `propuestas[]` | Central · cruce | Todas las propuestas no informativas, ordenadas: `sku`, `nombre`, `tipo`, `prioridad` (alta·media·baja), `titulo`, `detalle`, `impacto_usd`, `base_impacto`, `tipo_evidencia` (`INFERENCIA`), `evidencia{}` |
| `calidad` | Central | `interno` (`get_data_quality`), `avisos[]` (consultas que fallaron), `confianza_mercado{sku: HIGH·MEDIUM·LOW}` |

Reglas:
- Si una consulta falla, su sección queda vacía y el fallo aparece en `calidad.avisos`; el resto del panel se genera.
- Importes en USD; los equivalentes desde CUP son estimaciones con la tasa informal de elTOQUE.
- Añadir campos no rompe el contrato (1.x); cambiar o quitar campos exige 2.0.
