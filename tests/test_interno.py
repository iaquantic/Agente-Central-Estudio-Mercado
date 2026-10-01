import json
from datetime import datetime

import httpx
import pytest
from jsonschema import Draft202012Validator

from agente_central.interno import ClienteInterno, ClienteInternoDemo, datos
from agente_central.tiempo import Reloj
from tests.conftest import RAIZ

PETICION = Draft202012Validator(json.loads((RAIZ / "docs/contratos/orquestador_request.schema.json").read_text()))
RESPUESTA = Draft202012Validator(json.loads((RAIZ / "docs/contratos/orquestador_response.schema.json").read_text()))
RELOJ = Reloj("America/Havana", datetime.fromisoformat("2026-09-30T11:30:00-04:00"))


def _cliente(manejador):
    return ClienteInterno("http://interno/v1/consulta", "secreto", RELOJ, transporte=httpx.MockTransport(manejador))


async def test_peticiones_cumplen_el_contrato():
    vistas = []

    def manejador(req: httpx.Request):
        cuerpo = json.loads(req.content)
        vistas.append((req.headers["authorization"], cuerpo))
        return httpx.Response(200, json={"request_id": cuerpo["request_id"], "version": "1.0", "status": "ok",
                                         "tool_result": {"x": 1}, "summary": "", "generated_at": "2026-09-30T11:30:00-04:00"})

    c = _cliente(manejador)
    r = await c.herramienta("get_product", {"sku": "GRA-010"})
    await c.informe("inventario")
    await c.pregunta("¿Qué vendí ayer?")
    assert datos(r) == {"x": 1}
    for auth, cuerpo in vistas:
        assert auth == "Bearer secreto"
        assert not list(PETICION.iter_errors(cuerpo)), cuerpo
    assert vistas[0][1]["tool"] == {"name": "get_product", "params": {"sku": "GRA-010"}}


async def test_errores_de_red_no_lanzan():
    def caido(req):
        raise httpx.ConnectError("sin red")

    r = await _cliente(caido).informe("alertas")
    assert r["status"] == "error" and r["error"]["code"] == "unavailable"


async def test_timeout():
    def lento(req):
        raise httpx.ReadTimeout("lento")

    assert (await _cliente(lento).informe("alertas"))["error"]["code"] == "timeout"


async def test_respuesta_de_otra_peticion_se_rechaza():
    r = await _cliente(lambda req: httpx.Response(200, json={"request_id": "otro", "status": "ok"})).informe("alertas")
    assert r["status"] == "error" and r["error"]["code"] == "internal"


async def test_nombres_validados_localmente():
    c = _cliente(lambda req: pytest.fail("no debería llamar"))
    assert (await c.herramienta("drop_table"))["error"]["code"] == "invalid_request"
    assert (await c.informe("todo"))["error"]["code"] == "invalid_request"


def test_token_obligatorio():
    with pytest.raises(ValueError):
        ClienteInterno("http://x", None, RELOJ)


# ------------------------------------------------------------------------------------------- demo
@pytest.fixture
def demo():
    return ClienteInternoDemo(RAIZ / "demo/interno/fixtures.json", RELOJ)


async def test_demo_respeta_el_contrato(demo):
    respuestas = [await demo.herramienta("get_alerts", {}), await demo.herramienta("get_product", {"sku": "GRA-010"}),
                  await demo.herramienta("get_sales_summary", {"from": "2020-01-01", "to": "2020-01-31"}),
                  await demo.pregunta("hola")] + [await demo.informe(n) for n in
                                                    ("estado_general", "ventas", "inventario", "rentabilidad", "alertas", "calidad_datos")]
    for r in respuestas:
        assert not list(RESPUESTA.iter_errors(r)), r.get("summary")


async def test_demo_ficha_historial_y_busqueda(demo):
    assert datos(await demo.herramienta("get_product", {"sku": "gra-010"}))["status"] == "agotado"
    h = datos(await demo.herramienta("get_product_history", {"sku": "CLI-001", "from": "2026-07-06", "to": "2026-09-30"}))
    assert len(h["series"]) == 13 and h["series"][0]["units"] == 36
    m = datos(await demo.herramienta("find_products", {"query": "aceite girasol"}))
    assert m["matches"][0]["sku"] == "GRA-010"
    assert (await demo.herramienta("get_product", {"sku": "NO-EXISTE"}))["status"] == "no_data"
