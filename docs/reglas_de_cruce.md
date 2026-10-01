# Reglas de cruce negocio × mercado

Implementadas en `agente_central/cruce.py`. Todos los umbrales están en `reglas` del perfil de empresa.

## Medidas de partida

| Medida | Origen | Cálculo |
|---|---|---|
| Precio propio, coste medio, margen 30 d, stock, estado, cobertura | Agente Interno (`get_product`) | Tal cual |
| Velocidad con stock | Agente Interno (`get_product_history`, semanal) | Media de unidades/día de las últimas 8 semanas completas con stock o ventas (evita que un agotado parezca "no se vende") |
| Tendencia de unidades | Ídem | Últimas 4 semanas completas frente a las 4 anteriores |
| Referencia de mercado (USD) | Agente Externo | Mediana por anuncio de la misma presentación, por moneda; los CUP se convierten con la tasa del día; se ponderan por nº de anuncios (**estimación**). Con menos de 3 anuncios de la misma presentación, precio por unidad estándar (USD/kg, USD/L…) de todas las presentaciones × cantidad del producto |
| Banda p25–p75 | Ídem | Igual que la referencia, con los cuartiles |
| Variación del mercado (USD) | Ídem + serie de tasas del Interno | Mediana semanal en USD equivalentes (CUP ÷ tasa de esa semana); media de las 2 primeras semanas frente a las 2 últimas |
| Diferencia de precio | — | (precio propio − referencia) ÷ referencia |

## Reglas

`d` = diferencia de precio, `v` = variación del mercado, umbrales por defecto entre paréntesis.

| # | Regla | Condición | Prioridad | Impacto estimado |
|---|---|---|---|---|
| 1 | Reponer ya | stock agotado / riesgo de rotura / bajo **y** (señal `POSSIBLE_SHORTAGE` o `SUPPLY_DECREASE`, o `v ≥ 8 %`) | alta | margen unitario × velocidad con stock × 30 días |
| 1b | Reponer | stock en falta sin escasez en el mercado | media | ídem |
| 2 | Subir precio | `d ≤ −8 %`, sin falta ni exceso de stock | alta si margen < 10 %, si no media | (0,97 × referencia − precio) × velocidad × 30 días |
| 3 | Revisar coste | margen < 10 % y `d > −8 %` (ya en precio de mercado) | alta | — |
| 4 | Bajar precio | `d ≥ +8 %` | alta si exceso / sin movimiento o unidades −15 %; si no media | valor a coste del stock parado |
| 5 | Liquidar | exceso / sin movimiento **y** `v ≤ −8 %` | alta | valor del stock × caída del mercado |
| 6 | Vigilar | `v ≤ −8 %` sin exceso y sin regla 4 | media | — |
| — | En línea | ninguna de las anteriores | info | — |

- Con **confianza de mercado LOW**, las prioridades alta y media bajan un nivel y el texto pide confirmar.
- Sin precio de mercado comparable, la única salida es "Sin precio de mercado comparable" (info).
- Las reglas 1 y 2 sugieren un precio cercano a la referencia cuando `d ≤ −8 %`.
- Las propuestas se ordenan por prioridad y, dentro de cada una, por impacto.
- Todas son **INFERENCIAS**: el panel y el bot las presentan como propuestas, nunca como hechos ni acciones.

## Ejemplos (casos que verifican las pruebas automáticas, con mercado sintético de prueba)

| Producto | Situación | Propuesta |
|---|---|---|
| Aceite de girasol 1 L | Agotado; mercado +23,6 % con menos anuncios | Reponer ya (alta) y acercar el precio a ~5 USD |
| Huevos cartón 30 u | Margen 4,1 %; 16 % por debajo de un mercado que sube | Subir precio (alta), ≈135 USD/mes |
| Ventilador de pedestal 18" | 462 u en exceso; 14 % por encima de un mercado que cae 20 % | Bajar precio y liquidar (alta) |
| Freidora de aire 5 L | Sin ventas desde agosto; 25 % por encima del mercado | Bajar precio (alta) |
| Arroz 1 kg | −3,9 % frente al mercado, estable | En línea (info) |
