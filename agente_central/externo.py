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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from controlador_mercado import (AnalyzerConfig, ExchangeRate, ExchangeRateProvider, JsonFileSource, MarketAnalyzer,
                                 SourceRegistry, TargetProduct)

from .config import ruta
from .tiempo import Reloj

log = logging.getLogger(__name__)
VERSION_ANALISIS = 4     # cambia la clave de la caché cuando cambia lo que se guarda (2: anuncios; 3: segmentos)
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


def de_fuentes(source_id: str | None, fuentes: list[str]) -> bool:
    """¿El anuncio es de alguna de esas fuentes? «cuballama» incluye cuballama_mercado y cuballama_envios."""
    sid = (source_id or "").lower()
    return any(sid == f or sid.startswith(f + "_") or sid.startswith(f + ":") for f in fuentes)


def _presentacion(p: dict | None) -> str | None:
    """«1 kg», «2 lb», «30 × 1 kg», «30 u» a partir de la presentación detectada en el anuncio."""
    raw = (p or {}).get("raw_value") or {}
    q, u, pack = raw.get("quantity"), raw.get("unit"), raw.get("pack_count")
    base = f"{q:g} {u}" if q and u else None
    if pack and pack > 1:
        return f"{pack:g} × {base}" if base else f"{pack:g} u"
    return base


def moneda_mal_puesta(trazas: list[dict], usd_cup: float | None) -> set[str]:
    """Anuncios publicados «en USD» cuyo importe solo cuadra en CUP (p. ej. café de 250 g a 1 400 «USD» en Revolico).

    Criterio: con al menos 3 anuncios del producto en CUP, un precio en USD que es más de 20 veces la mediana en CUP
    (pasada a USD) y que, leído como CUP, queda entre 0,2 y 5 veces esa mediana.
    """
    if not usd_cup:
        return set()
    validos = [t for t in trazas if t.get("status") == "valida" and (t.get("match") or {}).get("level") not in (None, "NO_MATCH")
               and (t.get("match") or {}).get("presentation_status") in ("MISMA", "NO_ESPECIFICADA")]
    cup = sorted(((t.get("price") or {}).get("normalized_value") or {}).get("price") for t in validos
                 if ((t.get("price") or {}).get("normalized_value") or {}).get("currency") == "CUP")
    cup = [x for x in cup if x]
    if len(cup) < 3:
        return set()
    ref = cup[len(cup) // 2] / usd_cup                       # mediana en CUP, en USD
    salida = set()
    for t in validos:
        v = (t.get("price") or {}).get("normalized_value") or {}
        p = v.get("price")
        if v.get("currency") == "USD" and p and p >= 20 * ref and 0.2 * ref <= p / usd_cup <= 5 * ref:
            salida.add(t.get("observation_id"))
    return salida


def anuncios_de(trazas: list[dict], nombres: dict[str, str], corregidos: set[str] | None = None) -> tuple[list[dict], dict]:
    """Anuncios válidos del producto (con precio y moneda) y para qué se usaron: son las referencias de los precios."""
    salida, resumen = [], {"validos": 0, "duplicados": 0, "excluidos": 0, "atipicos": 0, "moneda_corregida": len(corregidos or ())}
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
            "moneda_corregida": t.get("observation_id") in (corregidos or set()),
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
        base = json.dumps({"v": VERSION_ANALISIS, "seg": self.m.get("segmentos"), "p": producto, "fx": (fx or {}).get("date"), "prov": self.m["provincias"]}, sort_keys=True,
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
        nombres = {c.source_id: c.source_name for c in capturas}

        def analizar(obs: list, estados: list) -> dict:
            def una_vez(lista: list) -> tuple[dict, list]:
                r = MarketAnalyzer(AnalyzerConfig(granularity=self.m["granularidad"])).analyze(
                    objetivo, lista, analysis_start=inicio, analysis_end=fin, now=fin, exchange_rates=tasas,
                    convert_to=self.m["convertir_a"] if tasas else None, source_status=estados)
                return r, r.pop("observations", None) or []   # la traza completa no viaja al panel ni al modelo

            r, trazas = una_vez(obs)
            corregidos = moneda_mal_puesta(trazas, (fx or {}).get("usd_cup"))
            if corregidos:                                     # se repite el análisis con la moneda corregida
                obs = [replace(o, currency="CUP") if o.observation_id in corregidos else o for o in obs]
                r, trazas = una_vez(obs)
            nombres.update({f.get("source_id"): f.get("source_name") for f in r.get("sources") or [] if f.get("source_name")})
            r["anuncios"], r["anuncios_resumen"] = anuncios_de(trazas, nombres, corregidos)
            return r

        todas = [o for c in capturas for o in c.observations]
        res = analizar(todas, [c.status_dict() for c in capturas])
        # Cada mercado (calle, tiendas online…) se analiza por separado: sus precios no se mezclan y los atípicos se
        # detectan dentro de su propio mercado.
        res["segmentos"] = {}
        for clave, seg in (self.m.get("segmentos") or {}).items():
            obs = [o for o in todas if de_fuentes(o.source_id, seg["fuentes"])]
            if obs:
                res["segmentos"][clave] = analizar(
                    obs, [c.status_dict() for c in capturas if de_fuentes(c.source_id, seg["fuentes"])])
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
