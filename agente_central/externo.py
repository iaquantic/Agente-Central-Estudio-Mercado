"""Cliente del Agente Externo (Controlador de Mercado, paquete `controlador-mercado`).

El Controlador de Mercado es una librería Python (no expone API HTTP), así que el Agente Central la usa en proceso:
- modo `motor`: `MarketAnalyzer` determinista sobre las fuentes autorizadas. Sin coste de modelo; el Agente Central
  (su propio Claude) decide qué producto buscar.
- modo `agente`: `MarketControllerAgent`, que usa Claude para especificar el producto y redactar conclusiones
  externas a partir de una solicitud en lenguaje natural.

Las cifras las calcula siempre el motor del Controlador de Mercado: este módulo no reescribe números.
Las conversiones de moneda usan la tasa elTOQUE que informa el Agente Interno (la misma con la que el negocio
convierte sus precios), etiquetadas como ESTIMACION por el propio motor.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from controlador_mercado import (AnalyzerConfig, ExchangeRate, ExchangeRateProvider, JsonFileSource, MarketAnalyzer,
                                 SourceRegistry, TargetProduct)

from .config import ruta
from .tiempo import Reloj

log = logging.getLogger(__name__)
VERSION_ANALISIS = 2     # cambia la clave de la caché cuando cambia lo que se guarda de cada análisis (2: anuncios)
MAX_ANUNCIOS = 250       # anuncios de referencia que se guardan por producto
MARGEN_CAPTURA = timedelta(hours=2)    # duración máxima de una descarga (las webs se consultan con pausas)
CACHE_INCOMPLETO_S = 15 * 60      # análisis pobres (pocos precios, fuentes caídas o vacías): reintentar pronto
MIN_PRECIOS_CACHE = 5             # las webs a veces devuelven muy pocos resultados de forma puntual
CAMPOS_PRODUCTO = ("name", "brand", "model", "variant", "quantity", "unit", "pack_count", "condition", "keywords",
                   "exclude_keywords", "provinces")


def construir_registro(fuentes: list[dict]) -> SourceRegistry:
    """Fuentes del perfil. Las web admiten `opciones` que se pasan al adaptador
    (p. ej. cuballama: default_province, channels, max_pages; revolico: max_pages)."""
    reg = SourceRegistry()
    for f in fuentes:
        if f["tipo"] == "archivo":
            reg.add(JsonFileSource(ruta(f["ruta"]), source_id=f.get("id")))
            continue
        from controlador_mercado.adapters.store import RecordingSource    # importación diferida: dependencias web
        nombre, op = f["nombre"], dict(f.get("opciones") or {})
        if nombre == "revolico":
            from controlador_mercado.adapters import RevolicoSource
            reg.add(RecordingSource(RevolicoSource(**op)))
        elif nombre == "cuballama":
            from controlador_mercado.adapters import CuballamaSource
            reg.add(RecordingSource(CuballamaSource(**op)))
        elif nombre == "cubamax":
            from controlador_mercado.adapters import CubamaxSource
            reg.add(RecordingSource(CubamaxSource(**op)))
        elif nombre == "cubatel":
            from controlador_mercado.adapters import CubatelSource
            reg.add(CubatelSource(**op))
    return reg


def limpiar_producto(producto: dict) -> dict:
    """Solo los campos de TargetProduct, sin vacíos. Lanza ValueError si falta el nombre."""
    p = {k: v for k, v in (producto or {}).items() if k in CAMPOS_PRODUCTO and v not in (None, "", [])}
    if not str(p.get("name", "")).strip():
        raise ValueError("El producto de mercado necesita 'name'.")
    return p


def completo(analisis: dict) -> bool:
    """Un análisis se guarda en caché larga solo si tiene suficientes precios válidos y todas las fuentes respondieron."""
    fuentes = analisis.get("sources") or []
    precios = (analisis.get("analysis_period") or {}).get("valid_price_observation_count") or 0
    return precios >= MIN_PRECIOS_CACHE and bool(fuentes) and all(f.get("status") == "ok" for f in fuentes)


def tasas_desde_fx(fx: dict | None) -> list[ExchangeRate]:
    """Convierte la tasa del Agente Interno ({date, usd_cup, source}) en un tipo de cambio autorizado y fechado."""
    if not fx or not fx.get("usd_cup"):
        return []
    as_of = datetime.fromisoformat(str(fx["date"])).replace(hour=12, tzinfo=timezone.utc)
    return [ExchangeRate("USD", "CUP", float(fx["usd_cup"]), as_of, str(fx.get("source") or "elTOQUE") + " (vía Agente Interno)")]


def _presentacion(p: dict | None) -> str | None:
    """«1 kg», «2 lb», «30 × 1 kg», «30 u» a partir de la presentación detectada en el anuncio."""
    raw = (p or {}).get("raw_value") or {}
    q, u, pack = raw.get("quantity"), raw.get("unit"), raw.get("pack_count")
    base = f"{q:g} {u}" if q and u else None
    if pack and pack > 1:
        return f"{pack:g} × {base}" if base else f"{pack:g} u"
    return base


def anuncios_de(trazas: list[dict], nombres: dict[str, str]) -> tuple[list[dict], dict]:
    """Anuncios válidos del producto (con precio y moneda) y para qué se usaron: son las referencias de los precios."""
    salida, resumen = [], {"validos": 0, "duplicados": 0, "excluidos": 0, "atipicos": 0}
    for t in trazas:
        if (t.get("match") or {}).get("level") in (None, "NO_MATCH"):
            continue                                         # otro producto (el historial guarda todas las búsquedas)
        if t.get("status") == "duplicado":
            resumen["duplicados"] += 1
            continue
        if t.get("status") != "valida":
            resumen["excluidos"] += 1          # fuera del periodo, sin precio, de otra provincia…
            continue
        resumen["validos"] += 1
        precio = ((t.get("price") or {}).get("normalized_value") or {})
        atipico = (t.get("outlier") or {}).get("direction") or (t.get("unit_price_outlier") or {}).get("direction")
        resumen["atipicos"] += bool(atipico)
        salida.append({
            "fuente": nombres.get(t.get("source_id")) or t.get("source_id"), "titulo": t.get("title"), "url": t.get("url"),
            "precio": precio.get("price"), "moneda": precio.get("currency"),
            "presentacion": _presentacion(t.get("presentation")),
            "cantidad_estandar": (((t.get("presentation") or {}).get("normalized_value") or {}).get("standard_quantity")),
            "unidad_estandar": (((t.get("presentation") or {}).get("normalized_value") or {}).get("standard_unit")),
            "precio_unidad": t.get("unit_price"),
            "en_presentacion": bool(t.get("used_in_price_stats")), "en_unidad": bool(t.get("used_in_unit_price_stats")),
            "atipico": atipico, "coincidencia": (t.get("match") or {}).get("level"),
            "provincia": t.get("province"), "capturado": (t.get("captured_at") or "")[:10],
        })
    salida.sort(key=lambda a: (not (a["en_presentacion"] or a["en_unidad"]), a["moneda"] or "", a["precio"] or 0))
    return salida[:MAX_ANUNCIOS], resumen


class ClienteMercado:
    def __init__(self, cfg, reloj: Reloj, *, registro: SourceRegistry | None = None, directorio_cache: Path | None = None):
        self.m = cfg["mercado"]
        self.reloj = reloj
        self.modelo = cfg["modelo"]
        self.registro = registro or construir_registro(self.m["fuentes"])
        self.cache_s = float(self.m["cache_horas"]) * 3600
        self._cache: dict[str, tuple[float, dict]] = {}
        self.directorio_cache = directorio_cache
        if directorio_cache:
            directorio_cache.mkdir(parents=True, exist_ok=True)

    def fuentes(self) -> list[dict]:
        try:
            return self.registro.describe()
        except Exception:                    # describe() no debe tumbar nada
            return []

    # ------------------------------------------------------------------------------------- motor
    def _clave(self, producto: dict, fx: dict | None) -> str:
        base = json.dumps({"v": VERSION_ANALISIS, "p": producto, "fx": (fx or {}).get("date"), "prov": self.m["provincias"]}, sort_keys=True,
                          ensure_ascii=False)
        return hashlib.sha256(base.encode()).hexdigest()[:16]

    def _leer_disco(self, clave: str) -> dict | None:
        if not self.directorio_cache:
            return None
        f = self.directorio_cache / f"{clave}.json"
        if f.exists() and time.time() - f.stat().st_mtime < self.cache_s:
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def _analizar_sync(self, producto: dict, fx: dict | None) -> dict:
        p = dict(producto)
        if self.m["provincias"] and not p.get("provinces"):
            p["provinces"] = list(self.m["provincias"])
        objetivo = TargetProduct.from_dict(p)
        ahora = self.reloj.ahora().astimezone(timezone.utc)
        historial = ahora - timedelta(weeks=int(self.m["semanas_historial"]))
        # Las capturas de esta consulta se fechan al descargarse, después de "ahora": el periodo se cierra cuando
        # termina la descarga (y con margen en la petición), o el análisis descartaría los anuncios recién capturados.
        capturas = self.registro.fetch_all(objetivo, historial, ahora + MARGEN_CAPTURA)
        fin = max(ahora, self.reloj.ahora().astimezone(timezone.utc),
                  max((o.captured_at for c in capturas for o in c.observations if o.captured_at), default=ahora))
        inicio = fin - timedelta(days=int(self.m["dias_periodo"]))
        tasas = tasas_desde_fx(fx)
        res = MarketAnalyzer(AnalyzerConfig(granularity=self.m["granularidad"])).analyze(
            objetivo, [o for c in capturas for o in c.observations],
            analysis_start=inicio, analysis_end=fin, now=fin, exchange_rates=tasas,
            convert_to=self.m["convertir_a"] if tasas else None,
            source_status=[c.status_dict() for c in capturas])
        trazas = res.pop("observations", None) or []   # la traza completa no viaja al panel ni al modelo
        nombres = {c.source_id: c.source_name for c in capturas}
        nombres.update({f.get("source_id"): f.get("source_name") for f in res.get("sources") or [] if f.get("source_name")})
        res["anuncios"], res["anuncios_resumen"] = anuncios_de(trazas, nombres)
        return res

    async def analizar(self, producto: dict, *, fx: dict | None = None, forzar: bool = False) -> dict:
        """Contrato completo del Controlador de Mercado (sin traza). Caché en memoria y en disco."""
        producto = limpiar_producto(producto)
        clave = self._clave(producto, fx)
        if not forzar:
            en_memoria = self._cache.get(clave)
            if en_memoria and time.monotonic() - en_memoria[0] < (self.cache_s if completo(en_memoria[1]) else CACHE_INCOMPLETO_S):
                return en_memoria[1]
            en_disco = self._leer_disco(clave)
            if en_disco:
                self._cache[clave] = (time.monotonic(), en_disco)
                return en_disco
        res = await asyncio.to_thread(self._analizar_sync, producto, fx)
        self._cache[clave] = (time.monotonic(), res)
        if self.directorio_cache and completo(res):
            (self.directorio_cache / f"{clave}.json").write_text(json.dumps(res, ensure_ascii=False, default=str),
                                                                 encoding="utf-8")
        return res

    # ------------------------------------------------------------------------------------ agente
    def _agente_sync(self, solicitud: str, fx: dict | None) -> dict:
        from controlador_mercado.agent import AgentError, MarketControllerAgent

        agente = MarketControllerAgent(
            self.registro, rate_provider=ExchangeRateProvider(tasas_desde_fx(fx)),
            analyzer_config=AnalyzerConfig(granularity=self.m["granularidad"]),
            now_fn=lambda: self.reloj.ahora().astimezone(timezone.utc))
        try:
            return agente.run(solicitud).to_dict(include_traces=False)
        except AgentError as e:
            return {"error": str(e)}

    async def consultar_agente(self, solicitud: str, *, fx: dict | None = None) -> dict:
        """Solicitud en lenguaje natural al Controlador de Mercado completo (usa Claude; solo en modo `agente`)."""
        return await asyncio.to_thread(self._agente_sync, solicitud, fx)


# --------------------------------------------------------------------------- resumen para el modelo
def resumen_analisis(a: dict) -> dict[str, Any]:
    """Versión compacta del contrato para pasarla al modelo (menos tokens, mismas cifras)."""
    ps = a.get("price_statistics") or {}
    precios = {}
    for moneda, b in (ps.get("by_currency") or {}).items():
        pp = b.get("presentation_price") or {}
        precios[moneda] = {k: pp.get(k) for k in ("n", "price_median", "price_min", "price_max", "p25", "p75",
                                                    "price_spread_pct", "outliers_excluded")}
    conv = (ps.get("converted") or {}).get("by_currency") or {}
    tr = a.get("trends") or {}
    return {
        "analysis_id": a.get("analysis_id"),
        "producto": {k: (a.get("product") or {}).get(k) for k in ("name", "brand", "geographic_scope")},
        "periodo": {k: (a.get("analysis_period") or {}).get(k) for k in ("analysis_start", "analysis_end", "observation_count")},
        "precios_por_moneda": precios,
        "conversion_estimada": {m: {k: v.get(k) for k in ("price_median", "rate", "rate_as_of", "rate_source")} for m, v in conv.items()},
        "tendencia_precio": tr.get("price_trend"),
        "serie_mediana_semanal": tr.get("price_series"),
        "tendencia_anuncios": tr.get("listing_trend"),
        "oferta": {k: (a.get("supply_statistics") or {}).get(k) for k in ("listing_count", "seller_count", "availability_level", "source_count")},
        "indicadores": a.get("external_indicators"),
        "senales": [{k: s.get(k) for k in ("type", "strength", "description")} for s in a.get("market_signals") or []],
        "afirmaciones": (a.get("market_summary") or {}).get("statements"),
        "confianza": a.get("confidence"),
        "limitaciones": a.get("limitations"),
        "fuentes": [{k: s.get(k) for k in ("source_id", "source_name", "status", "observations_received")} for s in a.get("sources") or []],
    }
