"""Construcción de los datos del panel (contrato en docs/contrato_panel.md).

El panel combina en un solo JSON lo que informan los dos subagentes y el cruce del Agente Central.
Cada sección es independiente: si una consulta falla, el panel se genera igual y lo indica en `calidad.avisos`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from . import __version__
from .cruce import PRIORIDADES, cruzar
from .externo import ClienteMercado
from .interno import Interno, datos, tasa
from .moneda import TasaDia, formateador, fx_de
from .tiempo import Reloj

log = logging.getLogger(__name__)


def _meses_atras(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def periodos(hoy: date, meses_historial: int = 12) -> dict[str, date]:
    lunes = hoy - timedelta(days=hoy.weekday())
    return {"mes_desde": hoy.replace(day=1), "hasta": hoy,
            "historial_desde": _meses_atras(hoy, meses_historial - 1),
            "tasa_desde": _meses_atras(hoy, 2),
            "semanas_desde": lunes - timedelta(weeks=12)}


class ConstructorPanel:
    def __init__(self, cfg, interno: Interno, mercado: ClienteMercado, reloj: Reloj, reloj_estudio: Reloj | None = None,
                 tasa_dia: TasaDia | None = None):
        self.cfg = cfg
        self.interno = interno
        self.mercado = mercado
        self.reloj = reloj                              # negocio (congelado en el modo demo)
        self.reloj_estudio = reloj_estudio or reloj      # fecha real del estudio
        self.tasa_dia = tasa_dia                         # tasa USD→CUP de hoy (elTOQUE)

    async def _h(self, nombre: str, params: dict, avisos: list[str]) -> dict | None:
        res = await self.interno.herramienta(nombre, params)
        d = datos(res)
        if d is None:
            msg = (res.get("error") or {}).get("message") or res.get("summary") or "sin datos"
            avisos.append(f"Agente Interno · {nombre}: {msg}")
        return d

    async def producto(self, sku: str, producto_mercado: dict, *, fx: dict | None, serie_fx: list | None,
                       avisos: list[str] | None = None, forzar: bool = False) -> dict | None:
        """`fx` es la tasa con la que se convierten los precios de hoy (la del día de elTOQUE si está disponible)."""
        """Cruce completo de un producto (ficha + historial interno + análisis de mercado)."""
        avisos = [] if avisos is None else avisos
        p = periodos(self.reloj.hoy())
        ficha, hist = await asyncio.gather(
            self._h("get_product", {"sku": sku}, avisos),
            self._h("get_product_history", {"sku": sku, "from": p["semanas_desde"].isoformat(),
                                            "to": p["hasta"].isoformat(), "granularity": "week"}, avisos))
        if not ficha:
            return None
        try:
            analisis = await self.mercado.analizar(producto_mercado, fx=fx, forzar=forzar)
        except Exception as e:                       # una fuente rota no tumba el panel
            log.exception("Fallo del análisis de mercado de %s", sku)
            avisos.append(f"Agente Externo · {sku}: {type(e).__name__}")
            analisis = None
        return cruzar(ficha, (hist or {}).get("series") or [], analisis, fx=fx, serie_fx=serie_fx,
                      reglas_cfg=self.cfg["reglas"], hoy=self.reloj.hoy(),
                      dinero=formateador((fx or {}).get("usd_cup"), self.cfg["panel"]["moneda_principal"]))

    async def construir(self, *, forzar_mercado: bool = False) -> dict[str, Any]:
        cfg, hoy = self.cfg, self.reloj.hoy()
        p = periodos(hoy, int(cfg["panel"]["meses_historial"]))
        mes = {"from": p["mes_desde"].isoformat(), "to": hoy.isoformat()}
        n = int(cfg["panel"]["top_n"])
        avisos: list[str] = []
        (resumen, mensual, categorias, top, bottom, rent_alta, rent_baja, rent_cat, inventario, alertas,
         cambio, calidad) = await asyncio.gather(
            self._h("get_business_summary", {"date": hoy.isoformat()}, avisos),
            self._h("get_sales_summary", {"from": p["historial_desde"].isoformat(), "to": hoy.isoformat(), "group_by": "month"}, avisos),
            self._h("get_sales_summary", {**mes, "group_by": "category", "compare": "previous_period"}, avisos),
            self._h("get_top_products", {**mes, "metric": "units", "limit": n}, avisos),
            self._h("get_top_products", {**mes, "metric": "units", "order": "bottom", "limit": n, "include_zero_sales": True}, avisos),
            self._h("get_margin_analysis", {**mes, "group_by": "product", "order": "desc", "limit": n}, avisos),
            self._h("get_margin_analysis", {**mes, "group_by": "product", "order": "asc", "limit": n}, avisos),
            self._h("get_margin_analysis", {**mes, "group_by": "category", "order": "desc"}, avisos),
            self._h("get_inventory_status", {"filter": "all_issues", "limit": 25}, avisos),
            self._h("get_alerts", {}, avisos),
            self._h("get_exchange_rate", {"from": p["tasa_desde"].isoformat(), "to": hoy.isoformat()}, avisos),
            self._h("get_data_quality", {}, avisos),
        )
        fx = (cambio or {}).get("today") or tasa(await self.interno.herramienta("get_data_quality", {}))
        serie_fx = (cambio or {}).get("series") or []
        tasa_dia = await self.tasa_dia.obtener(fx) if self.tasa_dia else None
        if tasa_dia and tasa_dia.get("aviso"):
            avisos.append(tasa_dia["aviso"])
        fx = fx_de(tasa_dia) or fx                       # los precios de hoy se convierten con la tasa de hoy

        productos = []
        for sku, prod in cfg.vigilados.items():          # en serie: las fuentes web se consultan con cortesía
            r = await self.producto(sku, prod["mercado"], fx=fx, serie_fx=serie_fx, avisos=avisos, forzar=forzar_mercado)
            if r:
                productos.append(r)

        propuestas = [{**pr, "sku": x["sku"], "nombre": x["nombre"]} for x in productos for pr in x["propuestas"]
                      if pr["prioridad"] != "info"]
        propuestas.sort(key=lambda q: (PRIORIDADES.index(q["prioridad"]), -(q["impacto_usd"] or 0)))
        return {
            "version": "1.0",
            "meta": {
                "empresa": {k: cfg.empresa.get(k) for k in ("id", "nombre", "descripcion", "pais", "moneda", "moneda_local")},
                "titulo": cfg["panel"]["titulo"], "proveedor": cfg["marca"]["proveedor"],
                "generado": self.reloj_estudio.ahora().isoformat(timespec="minutes"),
                "estudio": self.reloj_estudio.hoy().isoformat(),
                "tasa_dia": tasa_dia or (fx and {"usd_cup": fx.get("usd_cup"), "fecha": str(fx.get("date") or "")[:10],
                                                 "fuente": fx.get("source")}),
                "moneda_principal": cfg["panel"]["moneda_principal"],
                "periodo": {"desde": mes["from"], "hasta": mes["to"]},
                "demo": cfg["interno"]["modo"] == "demo",
                "agente_central": __version__,
                "zona_horaria": cfg.empresa["zona_horaria"],
                "umbral_precio_pct": cfg["reglas"]["diferencia_precio_pct"],
                "fuentes_mercado": sorted({f for x in productos for f in ((x.get("mercado") or {}).get("fuentes") or [])})
                or [f.get("source_name") or f.get("source_id") for f in self.mercado.fuentes()],
            },
            "resumen": resumen,
            "ventas_mensuales": (mensual or {}).get("groups") or [],
            "ventas_mes": {"totales": (categorias or {}).get("totals"), "comparacion": (categorias or {}).get("comparison"),
                           "categorias": (categorias or {}).get("groups") or []},
            "rentabilidad_categorias": (rent_cat or {}).get("items") or [],
            "mas_vendidos": (top or {}).get("items") or [],
            "menos_vendidos": (bottom or {}).get("items") or [],
            "mas_rentables": (rent_alta or {}).get("items") or [],
            "menos_rentables": (rent_baja or {}).get("items") or [],
            "margen_global_pct": (rent_cat or {}).get("overall_margin_pct"),
            "inventario": inventario,
            "alertas": alertas,
            "tasa": {"actual": fx, "serie": serie_fx, "variacion": (cambio or {}).get("change_pct"),
                     "impacto": (cambio or {}).get("impact")},
            "productos": productos,
            "propuestas": propuestas,
            "calidad": {"interno": calidad, "avisos": avisos,
                        "confianza_mercado": {x["sku"]: (x.get("mercado") or {}).get("confianza") for x in productos}},
        }
