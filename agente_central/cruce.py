"""Cruce negocio × mercado: el valor propio del Agente Central.

Toma la ficha y el historial de un producto (Agente Interno) y el análisis de mercado del mismo producto
(Agente Externo) y produce, con reglas deterministas y umbrales configurables por empresa:
- la posición de precio del negocio frente al mercado,
- la evolución del precio de mercado en USD equivalentes (los precios en CUP se deflactan con la tasa del día),
- propuestas de decisión priorizadas, con su evidencia y una estimación de impacto.

Las propuestas son INFERENCIAS: el Agente Central propone y el dueño decide. Ningún agente ejecuta acciones.
Las reglas están documentadas en docs/reglas_de_cruce.md.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime
from statistics import mean
from typing import Any, Callable

PRIORIDADES = ("alta", "media", "baja", "info")
ESTADOS_SIN_ROTACION = ("exceso", "sin_movimiento")
ESTADOS_FALTA = ("agotado", "riesgo_rotura", "stock_bajo")
SEÑALES_ESCASEZ = ("POSSIBLE_SHORTAGE", "SUPPLY_DECREASE")


def _r(x: float | None, d: int = 2) -> float | None:
    return None if x is None else round(float(x), d)


def _pct(a: float | None, b: float | None) -> float | None:
    """Variación porcentual de b respecto a a."""
    if a in (None, 0) or b is None:
        return None
    return round((b - a) / a * 100, 1)


def _bajar(prioridad: str) -> str:
    i = PRIORIDADES.index(prioridad)
    return PRIORIDADES[min(i + 1, len(PRIORIDADES) - 1)]


class SerieTasa:
    """Tasa USD→CUP por fecha (la última conocida en o antes de cada día)."""

    def __init__(self, serie: list[dict] | None, actual: float | None):
        puntos = sorted((str(p["date"]), float(p["usd_cup"])) for p in (serie or []) if p.get("usd_cup"))
        self.fechas = [f for f, _ in puntos]
        self.valores = [v for _, v in puntos]
        self.actual = actual

    def en(self, dia: str | date | datetime) -> float | None:
        d = str(dia)[:10]
        i = bisect_right(self.fechas, d)
        if i:
            return self.valores[i - 1]
        return self.valores[0] if self.valores else self.actual


# ------------------------------------------------------------------------------------- mercado
MIN_ANUNCIOS_PRESENTACION = 3


def _bloques(analisis: dict) -> tuple[dict, dict, dict, dict, float | None]:
    """Bloques USD/CUP por presentación y por unidad estándar, y la cantidad estándar del producto objetivo."""
    por_moneda = ((analisis.get("price_statistics") or {}).get("by_currency")) or {}
    pres = ((analisis.get("product") or {}).get("presentation") or {}).get("normalized_value") or {}
    dim, cant = pres.get("dimension"), pres.get("standard_quantity")

    def unidad(m: str) -> dict:
        return (((por_moneda.get(m) or {}).get("unit_price") or {}).get(dim) or {}) if dim else {}

    return ((por_moneda.get("USD") or {}).get("presentation_price") or {},
            (por_moneda.get("CUP") or {}).get("presentation_price") or {}, unidad("USD"), unidad("CUP"), cant)


def referencia_mercado(analisis: dict, tasa: float | None) -> dict[str, Any]:
    """Precio de referencia del mercado en USD para la presentación del negocio.

    Mediana por anuncio de la misma presentación, ponderada por nº de anuncios de cada moneda. Si hay menos de
    3 anuncios de esa presentación y más con otras presentaciones, se usa el precio por unidad estándar (USD/kg,
    USD/L…) multiplicado por la cantidad del producto: así una caja de 40 lb sirve de referencia para una de 10 lb.
    """
    usd_p, cup_p, usd_u, cup_u, cant = _bloques(analisis)
    n_pres = (usd_p.get("n") or 0) + ((cup_p.get("n") or 0) if tasa else 0)
    n_uni = (usd_u.get("n") or 0) + ((cup_u.get("n") or 0) if tasa else 0)
    por_unidad = (n_pres < MIN_ANUNCIOS_PRESENTACION and n_uni >= MIN_ANUNCIOS_PRESENTACION
                  and n_uni > n_pres and bool(cant))
    usd, cup, factor = (usd_u, cup_u, cant) if por_unidad else (usd_p, cup_p, 1.0)
    partes = []          # (n, mediana_usd, p25_usd, p75_usd)
    for bloque, div in ((usd, 1.0), (cup, tasa)):
        if bloque.get("n") and bloque.get("price_median") is not None and div:
            f = factor / div
            partes.append((bloque["n"], bloque["price_median"] * f,
                           bloque["p25"] * f if bloque.get("p25") is not None else None,
                           bloque["p75"] * f if bloque.get("p75") is not None else None))
    n = sum(p[0] for p in partes)
    if not n:
        return {"referencia_usd": None, "n": 0}

    def pond(i: int) -> float | None:
        v = [(p[0], p[i]) for p in partes if p[i] is not None]
        tot = sum(k for k, _ in v)
        return sum(k * x for k, x in v) / tot if tot else None

    ref, p25, p75 = pond(1), pond(2), pond(3)
    base = ("precio por unidad estándar de todas las presentaciones × cantidad del producto"
            if por_unidad else "mediana por anuncio de la misma presentación")
    return {                      # la banda siempre contiene la referencia (las ponderaciones pueden diferir)
        "referencia_usd": _r(ref), "p25_usd": _r(min(p25, ref)) if p25 is not None else None,
        "p75_usd": _r(max(p75, ref)) if p75 is not None else None, "n": n,
        "mediana_usd": _r(usd["price_median"] * factor) if usd.get("price_median") is not None else None,
        "n_usd": usd.get("n") or 0,
        "mediana_cup": _r(cup["price_median"] * factor, 0) if cup.get("price_median") is not None else None,
        "n_cup": cup.get("n") or 0,
        "mediana_cup_en_usd": _r(cup["price_median"] * factor / tasa) if cup.get("price_median") and tasa else None,
        "tasa_usd_cup": tasa, "por_unidad_estandar": por_unidad,
        "base": base + "; CUP convertidos con la tasa informal elTOQUE (estimación)",
    }


def serie_usd_equivalente(analisis: dict, tasas: SerieTasa) -> list[dict]:
    """Mediana semanal de mercado en USD equivalentes: USD nativos y CUP deflactados con la tasa de cada semana."""
    out = []
    for p in ((analisis.get("trends") or {}).get("periods")) or []:
        med, n = p.get("price_median") or {}, p.get("price_n") or {}
        fin = str(p.get("period_end") or p.get("period_start"))[:10]
        t = tasas.en(fin)
        partes = []
        if med.get("USD") is not None and n.get("USD"):
            partes.append((n["USD"], med["USD"]))
        if med.get("CUP") is not None and n.get("CUP") and t:
            partes.append((n["CUP"], med["CUP"] / t))
        tot = sum(k for k, _ in partes)
        out.append({"semana": str(p.get("period_start"))[:10], "usd": _r(sum(k * v for k, v in partes) / tot) if tot else None,
                    "anuncios": p.get("listing_count"), "completa": p.get("complete", True)})
    return out


def variacion_serie(serie: list[dict], puntos: int = 2) -> float | None:
    """Variación entre la media de los primeros y de los últimos `puntos` valores conocidos."""
    v = [s["usd"] for s in serie if s.get("usd") is not None]
    if len(v) < 2 * puntos:
        return None
    return _pct(mean(v[:puntos]), mean(v[-puntos:]))


def tendencia_precio(analisis: dict) -> dict | None:
    """Clasificación del Controlador de Mercado; se prefiere la serie en USD (sin efecto de la tasa)."""
    tr = ((analisis.get("trends") or {}).get("price_trend")) or {}
    for moneda in ("USD", "CUP"):
        t = tr.get(moneda)
        if t and t.get("classification") and (t.get("periods_with_data") or 0) >= 3:
            return {**t, "moneda": moneda}
    return None


# ------------------------------------------------------------------------------------- negocio
def metricas_internas(ficha: dict, semanas: list[dict], hoy: date) -> dict[str, Any]:
    completas = [s for s in semanas if (hoy - date.fromisoformat(str(s["period"])[:10])).days >= 7]
    ultimas, previas = completas[-4:], completas[-8:-4]
    tend = _pct(sum(s["units"] for s in previas), sum(s["units"] for s in ultimas)) if len(previas) == 4 else None
    con_stock = [s for s in completas[-8:] if (s.get("stock_end") or 0) > 0 or (s.get("units") or 0) > 0]
    vel_previa = round(mean(s["units"] for s in con_stock) / 7, 2) if con_stock else None
    precio, coste = ficha.get("price_usd"), ficha.get("avg_cost_usd")
    return {
        "precio_usd": precio, "coste_medio_usd": coste,
        "margen_unitario_usd": _r(precio - coste) if precio is not None and coste is not None else None,
        "margen_30d_pct": ficha.get("margin_30d_pct"),
        "stock": ficha.get("stock"), "estado_stock": ficha.get("status"), "cobertura_dias": ficha.get("coverage_days"),
        "dias_agotado": ficha.get("days_out_of_stock"), "plazo_reposicion_dias": ficha.get("lead_time_days"),
        "valor_stock_usd": ficha.get("stock_value_usd"),
        "velocidad_30d": ficha.get("velocity_30d"), "velocidad_con_stock": vel_previa,
        "ventas_30d": ficha.get("sales_30d"), "tendencia_unidades_pct": tend,
        "serie_semanal": [{"semana": str(s["period"])[:10], "unidades": s["units"], "ventas_usd": s.get("net_usd"),
                           "stock_final": s.get("stock_end")} for s in semanas],
    }


# ------------------------------------------------------------------------------------- reglas
def _propuesta(tipo: str, prioridad: str, titulo: str, detalle: str, *, impacto: float | None = None,
               base_impacto: str | None = None, evidencia: dict | None = None) -> dict:
    return {"tipo": tipo, "prioridad": prioridad, "titulo": titulo, "detalle": detalle,
            "impacto_usd": _r(impacto, 2) if impacto is not None else None, "base_impacto": base_impacto,
            "tipo_evidencia": "INFERENCIA", "evidencia": evidencia or {}}


ESTADO_TXT = {"agotado": "agotado", "riesgo_rotura": "en riesgo de rotura", "stock_bajo": "bajo",
              "exceso": "en exceso", "sin_movimiento": "sin movimiento", "normal": "normal", "inactivo": "inactivo"}


def _num(x: float, d: int) -> str:
    """Formato español: miles con espacio fino y coma decimal (1 234,50)."""
    return f"{x:,.{d}f}".replace(",", "\u202f").replace(".", ",")      # espacio fino que no se corta


def _usd(x: float | None, d: int = 2) -> str:
    """Formato por defecto (solo USD), cuando no se indica la moneda principal ni la tasa."""
    return "—" if x is None else _num(x, d) + " USD"


def _p(x: float | None, signo: bool = False) -> str:
    if x is None:
        return "—"
    return ("+" if signo and x > 0 else "") + _num(x, 1) + " %"


def reglas(interno: dict, mercado: dict, r: dict, dinero: Callable[..., str] = _usd) -> list[dict]:
    """`dinero(usd, d)` da formato a los importes de los textos (moneda principal y su equivalente)."""
    out: list[dict] = []
    ref = mercado.get("referencia_usd")
    precio, coste = interno.get("precio_usd"), interno.get("coste_medio_usd")
    estado = interno.get("estado_stock")
    margen = interno.get("margen_30d_pct")
    dif = _pct(ref, precio)                          # tu precio frente al mercado
    var = mercado.get("variacion_usd_pct")           # evolución del mercado en USD equivalentes
    señales = {s["type"] for s in mercado.get("señales", [])}
    sube = var is not None and var >= r["variacion_tendencia_pct"]
    baja = var is not None and var <= -r["variacion_tendencia_pct"]
    h = r["dias_horizonte"]
    vel = interno.get("velocidad_con_stock") or interno.get("velocidad_30d") or 0
    ev = {"precio_usd": precio, "referencia_mercado_usd": ref, "diferencia_pct": dif, "variacion_mercado_pct": var,
          "estado_stock": estado, "margen_30d_pct": margen}

    if ref is None:
        return [_propuesta("sin_mercado", "info", "Sin precio de mercado comparable",
                           "El Controlador de Mercado no encontró anuncios comparables con precio en el periodo.",
                           evidencia={"anuncios": mercado.get("anuncios")})]
    # Una diferencia enorme casi siempre significa que la búsqueda mezcla otros productos o presentaciones:
    # no se propone nada sobre esa base.
    if dif is not None and abs(dif) > r.get("diferencia_maxima_fiable_pct", 50):
        return [_propuesta("revisar_busqueda", "info", "Comparación con el mercado poco fiable",
                           f"Tu precio, {dinero(precio)}, y la referencia del mercado, {dinero(ref)}, difieren un {_p(abs(dif))}: "
                           f"seguramente la búsqueda mezcla otros productos o presentaciones ({mercado.get('n')} anuncios "
                           "comparables). Revisa la búsqueda de este producto en el perfil antes de decidir.",
                           evidencia=ev)]

    # 1. Falta de stock con el mercado escaso o al alza
    if estado in ESTADOS_FALTA:
        escaso = bool(señales & set(SEÑALES_ESCASEZ)) or sube
        perdida = vel * (precio - coste) * h if precio is not None and coste is not None else None
        precio_txt = ""
        if dif is not None and dif <= -r["diferencia_precio_pct"]:
            precio_txt = f" Al reponer, el mercado admite un precio cercano a {dinero(ref)}; ahora vendes a {dinero(precio)}."
        if escaso:
            out.append(_propuesta(
                "reponer", "alta", "Reponer ya: el mercado está escaso",
                f"Tu stock está {ESTADO_TXT.get(estado, estado)} y fuera la oferta se reduce o el precio sube "
                f"({_p(var, True)} en el periodo).{precio_txt}",
                impacto=perdida, base_impacto=f"margen que se deja de ganar en {h} días al ritmo de venta con stock ({_num(vel, 1)} u/día)",
                evidencia={**ev, "señales": sorted(señales & set(SEÑALES_ESCASEZ))}))
        else:
            out.append(_propuesta(
                "reponer", "media", "Reponer: hay oferta disponible en el mercado",
                f"Tu stock está {ESTADO_TXT.get(estado, estado)}; el mercado tiene {mercado.get('anuncios')} anuncios, "
                f"así que el problema es de suministro propio, no de escasez general.{precio_txt}",
                impacto=perdida, base_impacto=f"margen en riesgo en {h} días ({_num(vel, 1)} u/día)", evidencia=ev))

    # 2. Precio por debajo del mercado
    if dif is not None and dif <= -r["diferencia_precio_pct"] and estado not in ESTADOS_SIN_ROTACION and estado not in ESTADOS_FALTA:
        objetivo = round(ref * 0.97, 2)               # quedarse ligeramente por debajo de la mediana
        extra = (objetivo - precio) * vel * h if precio is not None else None
        comprimido = margen is not None and margen < r["margen_minimo_pct"]
        out.append(_propuesta(
            "subir_precio", "alta" if comprimido else "media",
            "Margen comprimido: el mercado ya subió" if comprimido and sube else "Hay margen para subir el precio",
            f"Vendes a {dinero(precio)}, un {_p(abs(dif))} por debajo de la mediana del mercado, que está en {dinero(ref)}"
            + (f"; tu margen es del {_p(margen)}" if margen is not None else "")
            + (f" y el mercado ha subido un {_p(var)}" if sube else "")
            + f". Un precio de {dinero(objetivo)} te mantendría por debajo del mercado.",
            impacto=extra, base_impacto=f"margen adicional en {h} días a {dinero(objetivo)} con las mismas ventas ({_num(vel, 1)} u/día)",
            evidencia={**ev, "precio_propuesto_usd": objetivo}))
    elif margen is not None and margen < r["margen_minimo_pct"] and dif is not None and dif > -r["diferencia_precio_pct"]:
        out.append(_propuesta(
            "revisar_coste", "alta", "Margen comprimido sin recorrido de precio",
            f"Tu margen es del {_p(margen)} y ya vendes en precio de mercado ({_p(dif, True)}). "
            "La mejora tiene que venir del coste: proveedor, volumen de compra o mezcla de productos.",
            evidencia=ev))

    # 3. Precio por encima del mercado
    if dif is not None and dif >= r["diferencia_precio_pct"]:
        no_rota = estado in ESTADOS_SIN_ROTACION or (interno.get("tendencia_unidades_pct") or 0) <= -15
        out.append(_propuesta(
            "bajar_precio", "alta" if no_rota else "media",
            "Precio por encima del mercado" + (" y el producto no rota" if no_rota else ""),
            f"Vendes a {dinero(precio)}, un {_p(dif)} por encima de la mediana del mercado, que está en {dinero(ref)}."
            + (f" Tienes {interno.get('stock')} u paradas, que valen {dinero(interno.get('valor_stock_usd'), 0)} a coste."
               if no_rota else (f" Además, el mercado baja un {_p(abs(var))}." if baja else " Vigila si las ventas empiezan a caer.")),
            impacto=interno.get("valor_stock_usd") if no_rota else None,
            base_impacto="valor a coste del stock que se liberaría" if no_rota else None, evidencia=ev))

    # 4. Exceso de stock con el mercado a la baja
    if estado in ESTADOS_SIN_ROTACION and baja:
        valor = interno.get("valor_stock_usd") or 0
        out.append(_propuesta(
            "liquidar", "alta", "Liquidar antes de que el mercado baje más",
            f"El precio de mercado cae un {_p(abs(var))} en el periodo y tienes {interno.get('stock')} u "
            f"({_num(interno['cobertura_dias'], 0) if interno.get('cobertura_dias') else '—'} días de cobertura). Una promoción ahora recupera liquidez antes de "
            "que el precio de referencia siga bajando.",
            impacto=valor * abs(var) / 100, base_impacto="pérdida de valor del stock si la caída se repite otro periodo",
            evidencia=ev))

    # 5. Mercado a la baja con el precio propio alineado: aviso de competitividad
    if baja and estado not in ESTADOS_SIN_ROTACION and not any(p["tipo"] == "bajar_precio" for p in out):
        out.append(_propuesta(
            "vigilar", "media", "El mercado está bajando",
            f"El precio de mercado cae un {_p(abs(var))} en el periodo"
            + (f" y ya estás un {_p(dif)} por encima." if dif is not None and dif > 0 else ".")
            + " Si la tendencia sigue, tu precio dejará de ser competitivo.",
            evidencia=ev))

    if not out:
        out.append(_propuesta("en_linea", "info", "Precio en línea con el mercado",
                              f"Diferencia con la mediana del mercado: {_p(dif, True)}. "
                              "No hay acción recomendada.", evidencia=ev))

    # La confianza del mercado modula la prioridad: con evidencia débil, nunca prioridad alta.
    if mercado.get("confianza") == "LOW":
        for p in out:
            if p["prioridad"] in ("alta", "media"):
                p["prioridad"] = _bajar(p["prioridad"])
            p["detalle"] += " (Evidencia de mercado de confianza baja: confirmar antes de actuar.)"
    return out


def cruzar(ficha: dict, semanas: list[dict], analisis: dict | None, *, fx: dict | None, serie_fx: list[dict] | None,
           reglas_cfg: dict, hoy: date, dinero: Callable[..., str] = _usd) -> dict[str, Any]:
    """Resultado completo del cruce para un producto."""
    interno = metricas_internas(ficha, semanas, hoy)
    base = {"sku": ficha.get("sku"), "nombre": ficha.get("name"), "categoria": ficha.get("category"), "interno": interno}
    if not analisis:
        return {**base, "estado": "sin_datos_mercado", "mercado": None, "posicion": None,
                "propuestas": [_propuesta("sin_mercado", "info", "Sin análisis de mercado",
                                          "No se pudo obtener el análisis del Controlador de Mercado.")]}
    tasa_actual = (fx or {}).get("usd_cup")
    tasas = SerieTasa(serie_fx, tasa_actual)
    ref = referencia_mercado(analisis, tasa_actual)
    serie = serie_usd_equivalente(analisis, tasas)
    sup = analisis.get("supply_statistics") or {}
    mercado = {
        **ref,
        "variacion_usd_pct": variacion_serie(serie),
        "tendencia_controlador": tendencia_precio(analisis),
        "serie_usd": serie,
        "anuncios": sup.get("listing_count"), "vendedores": sup.get("seller_count"),
        "nivel_oferta": sup.get("availability_level"),
        "señales": [{k: s.get(k) for k in ("type", "strength", "description")} for s in analisis.get("market_signals") or []],
        "confianza": analisis.get("confidence"),
        "analysis_id": analisis.get("analysis_id"),
        "capturado": (analisis.get("analysis_period") or {}).get("captured_at_max"),
        "fuentes": [s.get("source_name") or s.get("source_id") for s in analisis.get("sources") or [] if s.get("status") == "ok"],
        "limitaciones": analisis.get("limitations") or [],
    }
    dif = _pct(ref.get("referencia_usd"), interno.get("precio_usd"))
    umbral = reglas_cfg["diferencia_precio_pct"]
    posicion = None if dif is None else {
        "diferencia_pct": dif,
        "etiqueta": "por_debajo" if dif <= -umbral else "por_encima" if dif >= umbral else "en_linea"}
    propuestas = reglas(interno, mercado, reglas_cfg, dinero)
    propuestas.sort(key=lambda p: (PRIORIDADES.index(p["prioridad"]), -(p["impacto_usd"] or 0)))
    return {**base, "estado": "ok", "mercado": mercado, "posicion": posicion, "propuestas": propuestas}
