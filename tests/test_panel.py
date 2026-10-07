import json
import re

from agente_central.panel_html import renderizar


def _prop(panel, sku):
    return [(p["tipo"], p["prioridad"]) for x in panel["productos"] if x["sku"] == sku for p in x["propuestas"]]


def test_panel_demo_completo(panel_demo):
    p = panel_demo
    assert p["calidad"]["avisos"] == ["Falta ELTOQUE_API_KEY: se usa la última tasa del Agente Interno."]
    assert p["meta"]["moneda_principal"] == "CUP" and p["meta"]["tasa_dia"]["usd_cup"] == 741.74
    assert all("CUP (" in x["detalle"] for x in p["propuestas"] if "Vendes a" in x["detalle"])
    assert len(p["ventas_mensuales"]) == 12 and len(p["mas_vendidos"]) == 10 and len(p["menos_rentables"]) == 10
    assert p["tasa"]["actual"]["usd_cup"] == 741.74
    assert {x["sku"] for x in p["productos"]} == {"GRA-010", "GRA-012", "GRA-013", "GRA-014", "GRA-001", "CAR-001", "CLI-001", "ELE-003"}
    assert all(x["mercado"]["referencia_usd"] for x in p["productos"])


def test_historias_de_la_demo(panel_demo):
    p = panel_demo
    assert ("reponer", "alta") in _prop(p, "GRA-010")            # agotado con el mercado escaso
    assert ("subir_precio", "alta") in _prop(p, "GRA-014")       # margen 4 % y el mercado subió
    assert ("liquidar", "alta") in _prop(p, "CLI-001")           # exceso y precio de mercado a la baja
    assert ("bajar_precio", "alta") in _prop(p, "ELE-003")       # 25 % por encima y sin ventas
    assert _prop(p, "GRA-001") == [("en_linea", "info")]
    assert p["propuestas"][0]["prioridad"] == "alta"
    assert all(q["prioridad"] != "info" for q in p["propuestas"])


def test_html_autocontenido_y_con_marca(panel_demo, cfg):
    h = renderizar(panel_demo, cfg["marca"])
    assert "{{" not in h
    assert "#101063" in h and "Space Grotesk" in h and "<svg" in h
    datos = re.search(r'<script id="datos-panel" type="application/json">(.*?)</script>', h, re.S).group(1)
    assert json.loads(datos.replace("<\\/", "</"))["meta"]["empresa"]["nombre"] == "MercadoAgentico"


def test_html_escapa_cierre_de_script(panel_demo, cfg):
    p = json.loads(json.dumps(panel_demo, default=str))
    p["meta"]["empresa"]["descripcion"] = "</script><script>alert(1)</script>"
    h = renderizar(p, cfg["marca"])
    assert "</script><script>alert(1)" not in h


def test_marca_personalizable(panel_demo, cfg):
    marca = {**cfg["marca"], "colores": {"primario": "#003300", "acento": "#00AA55", "acento_fuerte": "#008844", "suave": "#DDFFEE"},
             "tipografias": {"titulos": "Montserrat", "texto": "Inter"}, "proveedor": "Otra Consultora"}
    h = renderizar(panel_demo, marca)
    assert "--marca-primario: #003300" in h and "family=Montserrat" in h and "Otra Consultora" in h


def test_anuncios_de_referencia(panel_demo, cfg):
    """Cada precio de mercado lleva los anuncios en los que se basa, con enlace y su uso en la referencia."""
    for x in panel_demo["productos"]:
        m = x["mercado"]
        usados = [a for a in m["anuncios"] if a["usado"]]
        assert usados and all(a["url"].startswith("https://") and a["equivalente_usd"] for a in usados)
        assert len(usados) <= m["n"] and m["anuncios_resumen"]["validos"] >= len(m["anuncios"])
        assert m["anuncios"].index(usados[-1]) == len(usados) - 1          # primero los usados, por precio
    aceite = next(x for x in panel_demo["productos"] if x["sku"] == "GRA-010")["mercado"]
    assert aceite["presentacion_objetivo"] == "1 L"
    # La garrafa de 5 L se lleva a la presentación del negocio (1 L) por su precio por unidad, y no entra en la mediana.
    garrafa = next(a for a in aceite["anuncios"] if a["presentacion"] == "5 litros")
    assert not garrafa["usado"] and round(garrafa["equivalente_usd"], 2) == round(17500 / 5 / aceite["tasa_usd_cup"], 2)
    pollo = next(x for x in panel_demo["productos"] if x["sku"] == "CAR-001")["mercado"]
    anomalo = next(a for a in pollo["anuncios"] if a["precio"] == 95000)
    assert anomalo["atipico"] == "ALTO" and not anomalo["usado"]
    h = renderizar(panel_demo, cfg["marca"])
    assert 'id="anuncios"' in h and "Anuncios de referencia" in h
