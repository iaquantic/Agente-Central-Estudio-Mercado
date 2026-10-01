from datetime import date

from agente_central.cruce import (SerieTasa, cruzar, metricas_internas, referencia_mercado, reglas,
                                  serie_usd_equivalente, variacion_serie)

REGLAS = {"margen_minimo_pct": 10, "diferencia_precio_pct": 8, "variacion_tendencia_pct": 8,
          "cobertura_minima_dias": 7, "dias_horizonte": 30}


def _analisis(usd=None, cup=None, periodos=(), señales=(), confianza="HIGH", anuncios=12):
    by = {}
    if usd:
        by["USD"] = {"presentation_price": {"n": usd[0], "price_median": usd[1], "p25": usd[1] * 0.95, "p75": usd[1] * 1.05}}
    if cup:
        by["CUP"] = {"presentation_price": {"n": cup[0], "price_median": cup[1], "p25": cup[1] * 0.95, "p75": cup[1] * 1.05}}
    return {"analysis_id": "an_x", "price_statistics": {"by_currency": by},
            "trends": {"periods": list(periodos), "price_trend": {"USD": {"classification": "TENDENCIA", "direction": "ALZA", "periods_with_data": 6}}},
            "supply_statistics": {"listing_count": anuncios, "seller_count": 9, "availability_level": "MEDIA"},
            "market_signals": [{"type": s, "strength": "TENDENCIA", "description": s} for s in señales],
            "confidence": confianza, "sources": [{"source_name": "Fuente", "status": "ok"}], "limitations": []}


def _interno(**kw):
    base = {"precio_usd": 10.0, "coste_medio_usd": 8.0, "margen_30d_pct": 20.0, "estado_stock": "normal", "stock": 50,
            "cobertura_dias": 15, "valor_stock_usd": 400.0, "velocidad_30d": 2.0, "velocidad_con_stock": 2.0,
            "tendencia_unidades_pct": 0}
    return {**base, **kw}


def _mercado(ref, var=0.0, señales=(), confianza="HIGH"):
    return {"referencia_usd": ref, "variacion_usd_pct": var, "señales": [{"type": s} for s in señales],
            "confianza": confianza, "anuncios": 12}


def _tipos(props):
    return [(p["tipo"], p["prioridad"]) for p in props]


def test_referencia_pondera_monedas_con_la_tasa():
    a = _analisis(usd=(4, 10.0), cup=(6, 7000.0))
    r = referencia_mercado(a, 700.0)
    assert r["referencia_usd"] == 10.0 and r["n"] == 10 and r["mediana_cup_en_usd"] == 10.0
    r = referencia_mercado(_analisis(usd=(2, 12.0), cup=(2, 7000.0)), 700.0)
    assert r["referencia_usd"] == 11.0
    assert r["p25_usd"] <= r["referencia_usd"] <= r["p75_usd"]


def test_sin_tasa_no_se_convierte_cup():
    r = referencia_mercado(_analisis(cup=(6, 7000.0)), None)
    assert r["referencia_usd"] is None and r["n"] == 0


def test_serie_en_usd_deflacta_la_tasa():
    periodos = [
        {"period_start": "2026-09-01", "period_end": "2026-09-08", "price_median": {"CUP": 6000.0}, "price_n": {"CUP": 5}},
        {"period_start": "2026-09-08", "period_end": "2026-09-15", "price_median": {"CUP": 7000.0}, "price_n": {"CUP": 5}},
    ]
    tasas = SerieTasa([{"date": "2026-09-08", "usd_cup": 600}, {"date": "2026-09-15", "usd_cup": 700}], 700)
    s = serie_usd_equivalente({"trends": {"periods": periodos}}, tasas)
    assert [x["usd"] for x in s] == [10.0, 10.0]          # el CUP sube solo por la tasa: en USD, estable


def test_variacion_serie():
    serie = [{"usd": v} for v in (10, 10, 11, 12, 12)]
    assert variacion_serie(serie) == 20.0
    assert variacion_serie([{"usd": 1}, {"usd": None}]) is None


def test_metricas_internas_excluye_semana_en_curso():
    semanas = [{"period": f"2026-08-{d:02d}", "units": 14, "net_usd": 1, "stock_end": 5} for d in (3, 10, 17, 24)] + \
              [{"period": "2026-08-31", "units": 21, "net_usd": 1, "stock_end": 5}, {"period": "2026-09-07", "units": 21, "net_usd": 1, "stock_end": 5},
               {"period": "2026-09-14", "units": 21, "net_usd": 1, "stock_end": 5}, {"period": "2026-09-21", "units": 21, "net_usd": 1, "stock_end": 5},
               {"period": "2026-09-28", "units": 1, "net_usd": 1, "stock_end": 5}]
    m = metricas_internas({"price_usd": 5, "avg_cost_usd": 4}, semanas, date(2026, 9, 30))
    assert m["tendencia_unidades_pct"] == 50.0
    assert m["margen_unitario_usd"] == 1


def test_reponer_con_mercado_escaso_es_prioridad_alta():
    p = reglas(_interno(estado_stock="agotado", stock=0, precio_usd=3.8, coste_medio_usd=3.07, velocidad_con_stock=5),
               _mercado(5.0, var=23.6, señales=["POSSIBLE_SHORTAGE"]), REGLAS)
    assert _tipos(p)[0] == ("reponer", "alta")
    assert p[0]["impacto_usd"] == round(5 * (3.8 - 3.07) * 30)
    assert "5,00 USD" in p[0]["detalle"]                     # sugiere reponer más cerca del precio de mercado


def test_reponer_sin_escasez_es_prioridad_media():
    p = reglas(_interno(estado_stock="riesgo_rotura"), _mercado(10.2), REGLAS)
    assert _tipos(p) == [("reponer", "media")]


def test_precio_bajo_con_margen_comprimido():
    p = reglas(_interno(precio_usd=9.0, coste_medio_usd=8.63, margen_30d_pct=4.1, velocidad_con_stock=3),
               _mercado(10.73, var=12.0), REGLAS)
    assert _tipos(p) == [("subir_precio", "alta")]
    assert p[0]["evidencia"]["precio_propuesto_usd"] == round(10.73 * 0.97, 2)
    assert p[0]["titulo"] == "Margen comprimido: el mercado ya subió"


def test_margen_comprimido_sin_recorrido_de_precio():
    p = reglas(_interno(precio_usd=10.0, margen_30d_pct=3.0), _mercado(10.1), REGLAS)
    assert _tipos(p) == [("revisar_coste", "alta")]


def test_precio_alto_y_sin_rotacion():
    p = reglas(_interno(precio_usd=85, estado_stock="sin_movimiento", valor_stock_usd=1452), _mercado(68.0, var=-1.0), REGLAS)
    assert _tipos(p) == [("bajar_precio", "alta")]
    assert p[0]["impacto_usd"] == 1452


def test_exceso_con_mercado_a_la_baja_liquidar():
    p = reglas(_interno(precio_usd=45, estado_stock="exceso", valor_stock_usd=15000), _mercado(39.5, var=-19.6), REGLAS)
    assert ("liquidar", "alta") in _tipos(p) and ("bajar_precio", "alta") in _tipos(p)


def test_mercado_bajando_con_precio_alineado_avisa():
    p = reglas(_interno(precio_usd=20.0), _mercado(19.5, var=-12.0), REGLAS)
    assert _tipos(p) == [("vigilar", "media")]


def test_precio_en_linea():
    p = reglas(_interno(precio_usd=2.2), _mercado(2.29, var=2.0), REGLAS)
    assert _tipos(p) == [("en_linea", "info")]


def test_confianza_baja_rebaja_la_prioridad():
    p = reglas(_interno(precio_usd=9.0, margen_30d_pct=4.1), _mercado(10.73, var=12.0, confianza="LOW"), REGLAS)
    assert p[0]["prioridad"] == "media" and "confianza baja" in p[0]["detalle"]


def test_sin_precio_de_mercado():
    assert _tipos(reglas(_interno(), _mercado(None), REGLAS)) == [("sin_mercado", "info")]


def test_cruzar_sin_analisis():
    c = cruzar({"sku": "A", "name": "A", "price_usd": 1}, [], None, fx=None, serie_fx=None, reglas_cfg=REGLAS, hoy=date(2026, 9, 30))
    assert c["estado"] == "sin_datos_mercado" and c["propuestas"][0]["tipo"] == "sin_mercado"


def test_todas_las_propuestas_son_inferencias():
    p = reglas(_interno(estado_stock="agotado"), _mercado(12.0, var=10, señales=["SUPPLY_DECREASE"]), REGLAS)
    assert all(x["tipo_evidencia"] == "INFERENCIA" for x in p)


def test_referencia_por_unidad_estandar_si_falta_la_presentacion():
    a = {"product": {"presentation": {"normalized_value": {"dimension": "masa", "standard_quantity": 4.536, "standard_unit": "kg"}}},
         "price_statistics": {"by_currency": {"USD": {
             "presentation_price": {"n": 1, "price_median": 30.0},
             "unit_price": {"masa": {"n": 6, "price_median": 4.0, "p25": 3.8, "p75": 4.4}}}}}}
    r = referencia_mercado(a, 700.0)
    assert r["por_unidad_estandar"] and r["referencia_usd"] == round(4.0 * 4.536, 2) and r["n"] == 6
    a["price_statistics"]["by_currency"]["USD"]["presentation_price"]["n"] = 4
    assert not referencia_mercado(a, 700.0)["por_unidad_estandar"]


def test_diferencia_enorme_no_genera_propuestas():
    p = reglas(_interno(precio_usd=22.0), {**_mercado(112.95), "n": 3}, {**REGLAS, "diferencia_maxima_fiable_pct": 50})
    assert _tipos(p) == [("revisar_busqueda", "info")]
    assert "poco fiable" in p[0]["titulo"].lower()


def test_unidad_estandar_exige_minimo_de_anuncios():
    a = {"product": {"presentation": {"normalized_value": {"dimension": "masa", "standard_quantity": 4.536}}},
         "price_statistics": {"by_currency": {"USD": {"presentation_price": {"n": 0},
                                                      "unit_price": {"masa": {"n": 2, "price_median": 25.0}}}}}}
    assert referencia_mercado(a, 700.0)["referencia_usd"] is None
