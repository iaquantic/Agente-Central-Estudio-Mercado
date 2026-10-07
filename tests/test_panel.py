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
