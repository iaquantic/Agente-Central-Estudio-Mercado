"""Herramientas del orquestador (lo que el modelo del Agente Central puede pedir) y su ejecución.

Cada herramienta enruta a un subagente o al cruce. Los parámetros se validan con JSON Schema antes de ejecutar,
y los errores vuelven al modelo como `is_error` sin detalles técnicos.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from .externo import limpiar_producto, resumen_analisis
from .interno import HERRAMIENTAS as HERR_INTERNO
from .interno import INFORMES

if TYPE_CHECKING:
    from .servicio import Servicio

log = logging.getLogger(__name__)

PRODUCTO_MERCADO = {
    "type": "object",
    "description": "Producto a buscar en el mercado, descrito de forma genérica.",
    "properties": {
        "name": {"type": "string", "description": "Nombre genérico sin marca ni tamaño, p. ej. 'aceite de girasol'."},
        "brand": {"type": "string", "description": "Marca, solo si el dueño exige una marca concreta."},
        "variant": {"type": "string"},
        "quantity": {"type": "number", "description": "Cantidad por unidad de venta, p. ej. 1 (con unit 'L')."},
        "unit": {"type": "string", "enum": ["ml", "L", "g", "kg", "lb", "unidades"]},
        "pack_count": {"type": "integer", "description": "Unidades por paquete (p. ej. 30 huevos)."},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "Términos obligatorios adicionales."},
        "exclude_keywords": {"type": "array", "items": {"type": "string"}, "description": "Términos que descartan un anuncio."},
        "provinces": {"type": "array", "items": {"type": "string"}, "description": "Limitar a estas provincias."},
    },
    "required": ["name"],
    "additionalProperties": False,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "informe_negocio",
        "description": "Informe predefinido del Agente Interno sobre el propio negocio (rápido y determinista). "
                       "estado_general: hoy y mes en curso con alertas; ventas: totales, canales y top 5; inventario: "
                       "agotados, riesgo de rotura, excesos y dinero inmovilizado; rentabilidad: márgenes por categoría "
                       "y productos con margen bajo; alertas: todas las activas; calidad_datos: frescura y huecos.",
        "input_schema": {"type": "object", "properties": {
            "informe": {"type": "string", "enum": list(INFORMES)},
            "desde": {"type": "string", "format": "date", "description": "YYYY-MM-DD (opcional, con 'hasta')."},
            "hasta": {"type": "string", "format": "date"}},
            "required": ["informe"], "additionalProperties": False},
    },
    {
        "name": "herramienta_negocio",
        "description": "Ejecuta una herramienta de solo lectura del Agente Interno. Principales parámetros: "
                       "find_products{query}; get_product{sku}; get_product_history{sku, from, to, granularity: day|week|month}; "
                       "get_sales_summary{from, to, group_by: none|day|week|month|channel|payment_method|category, "
                       "compare: none|previous_period|previous_year}; get_top_products{from, to, metric: units|revenue|gross_profit, "
                       "order: top|bottom, limit, category, include_zero_sales}; get_inventory_status{filter: all_issues|"
                       "out_of_stock|low_stock|stockout_risk|overstock|no_movement, category}; get_margin_analysis{from, to, "
                       "group_by: product|category, below_threshold_pct, order: asc|desc}; get_alerts{min_priority, category}; "
                       "get_returns_and_voids{from, to, group_by}; get_exchange_rate{from, to}; get_business_summary{date}; "
                       "get_data_quality{}. Fechas YYYY-MM-DD.",
        "input_schema": {"type": "object", "properties": {
            "nombre": {"type": "string", "enum": list(HERR_INTERNO)},
            "parametros": {"type": "object"}},
            "required": ["nombre"], "additionalProperties": False},
    },
    {
        "name": "pregunta_negocio",
        "description": "Pregunta libre en español al Agente Interno, que la responde con sus propias herramientas. "
                       "Más lenta y cara: úsala solo si ninguna herramienta o informe encaja.",
        "input_schema": {"type": "object", "properties": {"pregunta": {"type": "string", "maxLength": 500}},
                         "required": ["pregunta"], "additionalProperties": False},
    },
    {
        "name": "analizar_mercado",
        "description": "Pide al Agente Externo (Controlador de Mercado) el análisis de un producto en el mercado: precios "
                       "por moneda y conversión estimada a USD, tendencia semanal, oferta (anuncios y vendedores), señales "
                       "(escasez, subida, competencia…), confianza y limitaciones. Si el SKU está entre los productos "
                       "vigilados, basta con 'sku'.",
        "input_schema": {"type": "object", "properties": {
            "sku": {"type": "string", "description": "SKU del negocio, si el producto está vigilado."},
            "producto": PRODUCTO_MERCADO},
            "additionalProperties": False},
    },
    {
        "name": "comparar_producto",
        "description": "Cruza negocio y mercado para un SKU: tu precio frente a la mediana del mercado, evolución del "
                       "mercado en USD, ventas, stock y margen propios, y las propuestas de decisión calculadas con su "
                       "prioridad e impacto estimado. Para productos no vigilados, indica también 'producto_mercado'.",
        "input_schema": {"type": "object", "properties": {
            "sku": {"type": "string"}, "producto_mercado": PRODUCTO_MERCADO},
            "required": ["sku"], "additionalProperties": False},
    },
    {
        "name": "resumen_panel",
        "description": "Resumen del panel actual: indicadores del mes, decisiones propuestas priorizadas para los productos "
                       "vigilados, más/menos vendidos y rentables, alertas urgentes y posición de precio frente al mercado.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

TOOL_AGENTE_MERCADO = {
    "name": "consultar_agente_mercado",
    "description": "Solicitud en lenguaje natural al Agente Externo completo (usa su propio modelo para especificar "
                   "el producto y redactar conclusiones de mercado). Úsala para preguntas de mercado abiertas o con "
                   "varios productos.",
    "input_schema": {"type": "object", "properties": {"solicitud": {"type": "string", "maxLength": 1000}},
                     "required": ["solicitud"], "additionalProperties": False},
}


def definiciones(modo_mercado: str) -> list[dict]:
    return TOOLS + ([TOOL_AGENTE_MERCADO] if modo_mercado == "agente" else [])


_VALIDADORES = {t["name"]: Draft202012Validator(t["input_schema"]) for t in TOOLS + [TOOL_AGENTE_MERCADO]}


def _err(mensaje: str) -> dict:
    return {"status": "error", "message": mensaje}


def _sin_ruido(res: dict) -> dict:
    """Quita del sobre del Agente Interno lo que no aporta al modelo."""
    return {k: v for k, v in res.items() if k not in ("request_id", "version", "generated_at", "tools_used") and v not in ([], {}, None)}


def resumen_panel(panel: dict, limite: int = 8) -> dict:
    t = ((panel.get("ventas_mes") or {}).get("totales")) or {}
    c = ((panel.get("ventas_mes") or {}).get("comparacion") or {}).get("delta_pct") or {}
    inv = ((panel.get("inventario") or {}).get("totals")) or {}
    al = panel.get("alertas") or {}
    return {
        "generado": panel["meta"]["generado"], "periodo": panel["meta"]["periodo"], "demo": panel["meta"]["demo"],
        "mes": {"ventas_netas_usd": t.get("net_usd"), "variacion_pct": c.get("net_usd"), "margen_pct": t.get("gross_margin_pct"),
                "ticket_medio_usd": t.get("avg_ticket_usd"), "dinero_inmovilizado_usd": inv.get("immobilized_value_usd")},
        "propuestas": [{k: p.get(k) for k in ("sku", "nombre", "prioridad", "titulo", "detalle", "impacto_usd", "base_impacto")}
                       for p in panel.get("propuestas", [])[:limite]],
        "posicion_precio": [{"sku": x["sku"], "nombre": x["nombre"], "precio_usd": x["interno"]["precio_usd"],
                             "mercado_usd": (x.get("mercado") or {}).get("referencia_usd"),
                             "diferencia_pct": (x.get("posicion") or {}).get("diferencia_pct"),
                             "variacion_mercado_pct": (x.get("mercado") or {}).get("variacion_usd_pct"),
                             "confianza": (x.get("mercado") or {}).get("confianza")} for x in panel.get("productos", [])],
        "mas_vendidos": [{"nombre": i["name"], "unidades": i["units"]} for i in panel.get("mas_vendidos", [])[:5]],
        "menos_vendidos": [{"nombre": i["name"], "unidades": i["units"]} for i in panel.get("menos_vendidos", [])[:5]],
        "mas_rentables": [{"nombre": i["name"], "margen_pct": i["margin_pct"]} for i in panel.get("mas_rentables", [])[:5]],
        "menos_rentables": [{"nombre": i["name"], "margen_pct": i["margin_pct"]} for i in panel.get("menos_rentables", [])[:5]],
        "alertas_urgentes": [a["title"] for a in al.get("alerts", []) if a.get("priority") == "urgent"],
        "avisos": panel["calidad"]["avisos"],
    }


def compactar_cruce(c: dict) -> dict:
    """El cruce sin series largas (el modelo no las necesita enteras)."""
    out = json.loads(json.dumps(c, default=str))
    if out.get("mercado"):
        for m in [out["mercado"], *(out["mercado"].get("segmentos") or {}).values()]:
            serie = m.pop("serie_usd", [])
            m["serie_usd_resumen"] = [s for s in serie if s.get("usd") is not None][-6:]
            anuncios = m.pop("anuncios_ref", None) or []
            m["anuncios_ejemplo"] = [{k: a.get(k) for k in ("fuente", "titulo", "precio", "moneda", "presentacion", "url")}
                                     for a in anuncios if a.get("usado")][:5]
    if out.get("interno"):
        out["interno"]["serie_semanal"] = out["interno"]["serie_semanal"][-6:]
    return out


class Ejecutor:
    def __init__(self, servicio: "Servicio"):
        self.s = servicio

    async def ejecutar(self, nombre: str, entrada: dict | None) -> tuple[dict, dict]:
        """Devuelve (resultado para el modelo, traza para el registro)."""
        entrada = entrada or {}
        v = _VALIDADORES.get(nombre)
        if v is None:
            res = _err("Herramienta desconocida.")
        else:
            fallos = list(v.iter_errors(entrada))
            if fallos:
                res = _err(f"Parámetros no válidos: {fallos[0].message}")
            else:
                try:
                    res = await getattr(self, f"_{nombre}")(**entrada)
                except ValueError as e:
                    res = _err(str(e))
                except Exception:
                    log.exception("Fallo en la herramienta %s", nombre)
                    res = _err("No se pudo completar la consulta.")
        destino = "interno" if nombre.endswith("_negocio") else "externo" if "mercado" in nombre else "central"
        return res, {"name": nombre, "destino": destino, "input": entrada, "status": res.get("status", "ok")}

    async def _informe_negocio(self, informe: str, desde: str | None = None, hasta: str | None = None) -> dict:
        d = date.fromisoformat(desde) if desde and hasta else None
        h = date.fromisoformat(hasta) if desde and hasta else None
        return _sin_ruido(await self.s.interno.informe(informe, d, h))

    async def _herramienta_negocio(self, nombre: str, parametros: dict | None = None) -> dict:
        return _sin_ruido(await self.s.interno.herramienta(nombre, parametros or {}))

    async def _pregunta_negocio(self, pregunta: str) -> dict:
        return _sin_ruido(await self.s.interno.pregunta(pregunta))

    def _producto(self, sku: str | None, producto: dict | None) -> dict:
        if producto:
            return limpiar_producto(producto)
        vig = self.s.cfg.vigilados.get((sku or "").upper())
        if not vig:
            raise ValueError("Ese SKU no está entre los productos vigilados: describe el producto de mercado.")
        return vig["mercado"]

    async def _analizar_mercado(self, sku: str | None = None, producto: dict | None = None) -> dict:
        p = self._producto(sku, producto)
        fx, _ = await self.s.tasa()
        return {"status": "ok", **resumen_analisis(await self.s.mercado.analizar(p, fx=fx))}

    async def _comparar_producto(self, sku: str, producto_mercado: dict | None = None) -> dict:
        p = self._producto(sku, producto_mercado)
        fx, serie = await self.s.tasa()
        avisos: list[str] = []
        c = await self.s.constructor.producto(sku.upper(), p, fx=fx, serie_fx=serie, avisos=avisos)
        if c is None:
            return _err("No encontré ese SKU en el negocio. Búscalo antes con find_products.")
        return {"status": "ok", **compactar_cruce(c), "avisos": avisos}

    async def _resumen_panel(self) -> dict:
        return {"status": "ok", **resumen_panel(await self.s.panel())}

    async def _consultar_agente_mercado(self, solicitud: str) -> dict:
        fx, _ = await self.s.tasa()
        r = await self.s.mercado.consultar_agente(solicitud, fx=fx)
        if "error" in r:
            return _err("El Agente Externo no pudo completar el análisis.")
        return {"status": "ok", "resumen": r.get("summary"), "analisis": [
            {"conclusiones": (a.get("market_summary") or {}).get("conclusions"),
             "advertencias": (a.get("market_summary") or {}).get("model_warnings"), "datos": resumen_analisis(a)} for a in r.get("analyses", [])], "no_resuelto": r.get("unresolved_requests")}
