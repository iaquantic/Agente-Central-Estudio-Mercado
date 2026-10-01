"""Cliente del Agente Interno (control del negocio).

Contrato: `POST /v1/consulta` v1.0 (repositorio Agente-Controlador-de-Negocio, docs/especificacion/08_contrato_orquestador.md;
copia de los esquemas en docs/contratos/). Tres tipos de petición:
- `report`: informe predefinido, determinista y barato (estado_general, ventas, inventario, rentabilidad, alertas, calidad_datos).
- `tool`: una de sus 12 herramientas de solo lectura, con sus parámetros.
- `question`: pregunta libre en español; la responde el propio Agente Interno con su modelo.

`ClienteInternoDemo` implementa la misma interfaz con respuestas reales capturadas (demo/interno/fixtures.json),
para ejecutar el sistema completo sin credenciales.
"""
from __future__ import annotations

import json
import logging
import unicodedata
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx

from .tiempo import Reloj

log = logging.getLogger(__name__)

INFORMES = ("estado_general", "ventas", "inventario", "rentabilidad", "alertas", "calidad_datos")
HERRAMIENTAS = ("get_business_summary", "get_sales_summary", "get_top_products", "find_products", "get_product",
                "get_product_history", "get_inventory_status", "get_margin_analysis", "get_alerts",
                "get_returns_and_voids", "get_exchange_rate", "get_data_quality")


class Interno(Protocol):
    async def informe(self, nombre: str, desde: date | None = None, hasta: date | None = None) -> dict: ...
    async def herramienta(self, nombre: str, params: dict | None = None) -> dict: ...
    async def pregunta(self, texto: str) -> dict: ...
    async def cerrar(self) -> None: ...


def datos(respuesta: dict) -> dict | None:
    """El bloque `data` de una respuesta a `type=tool` (None si falló)."""
    if respuesta.get("status") in ("ok", "partial", "no_data"):
        return respuesta.get("tool_result")
    return None


def tasa(respuesta: dict) -> dict | None:
    return ((respuesta.get("data_quality") or {}).get("fx")) or None


def _error(codigo: str, mensaje: str, request_id: str = "") -> dict:
    return {"request_id": request_id, "version": "1.0", "status": "error",
            "error": {"code": codigo, "message": mensaje}}


class ClienteInterno:
    """Cliente HTTP del contrato v1.0. Nunca lanza excepciones de red: devuelve `status=error`."""

    def __init__(self, url: str, token: str | None, reloj: Reloj, *, timeout_s: float = 30,
                 transporte: httpx.AsyncBaseTransport | None = None):
        if not token:
            raise ValueError("Falta el token del Agente Interno (ORQUESTADOR_TOKEN).")
        self.url = url
        self.reloj = reloj
        self._http = httpx.AsyncClient(timeout=timeout_s + 5, transport=transporte,
                                       headers={"Authorization": f"Bearer {token}"})

    async def _consulta(self, cuerpo: dict) -> dict:
        peticion = {"request_id": str(uuid.uuid4()), "version": "1.0",
                    "requested_at": self.reloj.ahora().isoformat(timespec="seconds"), **cuerpo}
        try:
            r = await self._http.post(self.url, json=peticion)
            res = r.json()
        except httpx.TimeoutException:
            return _error("timeout", "El Agente Interno tardó demasiado en responder.", peticion["request_id"])
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            log.exception("No se pudo consultar al Agente Interno")
            return _error("unavailable", "El Agente Interno no está disponible.", peticion["request_id"])
        if not isinstance(res, dict) or res.get("request_id") != peticion["request_id"]:
            return _error("internal", "Respuesta del Agente Interno fuera de contrato.", peticion["request_id"])
        return res

    async def informe(self, nombre: str, desde: date | None = None, hasta: date | None = None) -> dict:
        if nombre not in INFORMES:
            return _error("invalid_request", f"Informe desconocido: {nombre}")
        cuerpo: dict[str, Any] = {"type": "report", "report": nombre}
        if desde and hasta:
            cuerpo["period"] = {"from": desde.isoformat(), "to": hasta.isoformat()}
        return await self._consulta(cuerpo)

    async def herramienta(self, nombre: str, params: dict | None = None) -> dict:
        if nombre not in HERRAMIENTAS:
            return _error("invalid_request", f"Herramienta desconocida: {nombre}")
        return await self._consulta({"type": "tool", "tool": {"name": nombre, "params": params or {}}})

    async def pregunta(self, texto: str) -> dict:
        return await self._consulta({"type": "question", "question": texto[:500]})

    async def cerrar(self) -> None:
        await self._http.aclose()


# --------------------------------------------------------------------------------------------- demo
def clave(nombre: str, params: dict | None) -> str:
    return f"{nombre}|{json.dumps(params or {}, sort_keys=True, ensure_ascii=False)}"


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


class ClienteInternoDemo:
    """Misma interfaz que `ClienteInterno`, servida desde respuestas reales capturadas."""

    def __init__(self, archivo: str | Path, reloj: Reloj):
        self.fx = None
        f = json.loads(Path(archivo).read_text(encoding="utf-8"))
        self.reloj = reloj
        self.fx = f["fx"]
        self.dq = f["data_quality"]
        self.avisos = f.get("warnings", [])
        self.respuestas = {clave(h["tool"], h["params"]): h["data"] for h in f["herramientas"]}
        self.productos = f.get("productos", {})
        nombres: dict[str, dict] = {}
        for h in f["herramientas"]:
            for it in (h["data"].get("items") or []):
                sku = it.get("sku") or it.get("key")
                if sku and "-" in str(sku):
                    nombres.setdefault(sku, {"sku": sku, "name": it["name"], "category": it.get("category"), "active": True})
        for sku, p in self.productos.items():
            nombres[sku] = {"sku": sku, "name": p["ficha"]["name"], "category": p["ficha"]["category"], "active": True}
        self.catalogo = list(nombres.values())

    def _sobre(self, estado: str, resumen: str, **extra) -> dict:
        hoy = self.reloj.hoy().isoformat()
        return {"request_id": str(uuid.uuid4()), "version": "1.0", "status": estado, "summary": resumen,
                "period": {"from": hoy, "to": hoy, "timezone": "America/Havana"}, "metrics": {}, "findings": [],
                "alerts": [], "not_available": [],
                "data_quality": {"last_data_at": self.dq["last_sale_at"], "freshness_minutes": self.dq["freshness_minutes"],
                                 "fx": self.fx, "warnings": self.avisos},
                "tools_used": [], "generated_at": self.reloj.ahora().isoformat(timespec="seconds"), **extra}

    def _buscar(self, consulta: str, limite: int = 5) -> dict:
        palabras = [w for w in _norm(consulta).split() if len(w) > 1]
        puntuados = []
        for p in self.catalogo:
            nombre = _norm(p["name"])
            aciertos = sum(1 for w in palabras if w in nombre)
            if aciertos:
                puntuados.append((aciertos / max(len(palabras), 1), p))
        puntuados.sort(key=lambda x: -x[0])
        matches = [{**p, "similarity": round(s, 2)} for s, p in puntuados[:limite]]
        ambiguo = len(matches) > 1 and matches[0]["similarity"] - matches[1]["similarity"] < 0.15
        return {"matches": matches, "ambiguous": ambiguo}

    async def herramienta(self, nombre: str, params: dict | None = None) -> dict:
        params = params or {}
        if nombre not in HERRAMIENTAS:
            return _error("invalid_request", f"Herramienta desconocida: {nombre}")
        data: dict | None = self.respuestas.get(clave(nombre, params))
        if data is None and nombre == "find_products":
            data = self._buscar(str(params.get("query", "")), int(params.get("limit") or 5))
        elif data is None and nombre in ("get_product", "get_product_history"):
            p = self.productos.get(str(params.get("sku", "")).upper())
            if p and nombre == "get_product":
                data = p["ficha"]
            elif p:
                data = {"sku": p["ficha"]["sku"], "name": p["ficha"]["name"], "granularity": "week",
                        "series": [{"period": s[0], "units": s[1], "net_usd": s[2], "stock_end": s[3]} for s in p["semanas"]]}
        elif data is None and nombre == "get_alerts":                 # filtra la captura completa
            orden = ["low", "medium", "high", "urgent"]
            minimo = orden.index(params.get("min_priority", "low")) if params.get("min_priority") in orden else 0
            todas = self.respuestas[clave("get_alerts", {})]
            data = {"alerts": [a for a in todas["alerts"] if orden.index(a["priority"]) >= minimo
                               and (not params.get("category") or a.get("category") == params["category"])],
                    "count_by_priority": todas["count_by_priority"]}
        elif data is None and nombre == "get_data_quality":
            data = {**self.dq, "warnings": self.avisos}
        if data is None:                       # en la demo solo existen las consultas capturadas
            return self._sobre("no_data", "Ese dato no está disponible en los datos de demostración.",
                               tools_used=[nombre], tool_result=None)
        return self._sobre("ok", f"Resultado de {nombre}.", tools_used=[nombre], tool_result=data)

    async def informe(self, nombre: str, desde: date | None = None, hasta: date | None = None) -> dict:
        if nombre not in INFORMES:
            return _error("invalid_request", f"Informe desconocido: {nombre}")
        bs = self.respuestas[clave("get_business_summary", {"date": self.reloj.hoy().isoformat()})]
        al = self.respuestas[clave("get_alerts", {})]
        inv = self.respuestas[clave("get_inventory_status", {"filter": "all_issues", "limit": 25})]
        alertas = [{"id": f"A{i}", **a} for i, a in enumerate(al["alerts"], 1)]
        if nombre == "estado_general":
            t = bs["today"]
            r = (f"Hoy hasta las {bs['as_of_hour']}: {t['net_usd']:.2f} USD en {t['tickets']} ventas "
                 f"({bs['vs_expected_pct']:+.1f} % frente a lo esperado). Mes: {bs['month_to_date']['net_usd']:.2f} USD.")
            return self._sobre("ok", r, alerts=[a for a in alertas if a["priority"] in ("urgent", "high")],
                               tools_used=["get_business_summary", "get_alerts"])
        if nombre == "alertas":
            n = al["count_by_priority"]
            return self._sobre("ok", f"{sum(n.values())} alertas activas: {n['urgent']} urgentes, {n['high']} altas.",
                               alerts=alertas, tools_used=["get_alerts"])
        if nombre == "inventario":
            b = inv["totals"]["by_status"]
            r = (f"{b['agotado']} agotados, {b['riesgo_rotura']} en riesgo de rotura, {b['stock_bajo']} con stock bajo; "
                 f"{inv['totals']['immobilized_value_usd']:.2f} USD inmovilizados en exceso o sin movimiento.")
            return self._sobre("ok", r, alerts=[a for a in alertas if a.get("category") == "inventario"],
                               tools_used=["get_inventory_status", "get_alerts"])
        if nombre == "ventas":
            v = self.respuestas[clave("get_sales_summary", {"from": "2026-09-01", "to": "2026-09-30", "group_by": "category", "compare": "previous_period"})]
            return self._sobre("ok", f"Ventas netas del mes {v['totals']['net_usd']:.2f} USD "
                                     f"({v['comparison']['delta_pct']['net_usd']:+.1f} % frente al periodo anterior).",
                               tools_used=["get_sales_summary", "get_top_products"])
        if nombre == "rentabilidad":
            m = self.respuestas[clave("get_margin_analysis", {"from": "2026-09-01", "to": "2026-09-30", "group_by": "category", "order": "desc"})]
            return self._sobre("ok", f"Margen bruto del periodo: {m['overall_margin_pct']} %.", tools_used=["get_margin_analysis"])
        return self._sobre("ok", f"Último dato hace {self.dq['freshness_minutes']} min.", tools_used=["get_data_quality"])

    async def pregunta(self, texto: str) -> dict:
        return self._sobre("partial", "En modo demostración el Agente Interno no responde preguntas libres: "
                                      "usa informes y herramientas.", not_available=[{"item": "pregunta libre", "reason": "modo demo"}])

    async def cerrar(self) -> None:
        return None


def crear_cliente_interno(cfg, reloj: Reloj) -> Interno:
    from .config import ruta

    i = cfg["interno"]
    if i["modo"] == "demo":
        return ClienteInternoDemo(ruta(i["fixtures"]), reloj)
    return ClienteInterno(i["url"], cfg.token_interno, reloj, timeout_s=float(i["timeout_s"]))
