"""Orquestador con un cliente de Claude simulado: enrutado, bucle de herramientas e historial."""
import json
from types import SimpleNamespace as NS

import anthropic
import httpx

from agente_central.herramientas import Ejecutor


def _uso():
    return NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _tool(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def _txt(t):
    return NS(type="text", text=t)


class FalsoClaude:
    def __init__(self, guion):
        self.guion = list(guion)
        self.llamadas = []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kw):
        self.llamadas.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        paso = self.guion.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return paso


def _resp(contenido, stop):
    return NS(content=contenido, stop_reason=stop, usage=_uso())


def _servicio(servicio, guion):
    falso = FalsoClaude(guion)
    servicio._cliente_claude = falso
    return servicio, falso


async def test_pregunta_de_mercado_va_al_agente_externo(servicio):
    s, falso = _servicio(servicio, [
        _resp([_tool("t1", "analizar_mercado", {"sku": "GRA-010"})], "tool_use"),
        _resp([_txt("El aceite sube un <b>23,6 %</b> y escasea.")], "end_turn"),
    ])
    r = await s.orquestador.responder("tg:1", "¿Cómo se mueve el aceite en el mercado?")
    assert r.estado == "ok" and "23,6" in r.texto
    assert [(h["name"], h["destino"], h["status"]) for h in r.herramientas] == [("analizar_mercado", "externo", "ok")]
    resultado = json.loads(falso.llamadas[1]["messages"][-1]["content"][0]["content"])
    assert resultado["confianza"] in ("HIGH", "MEDIUM", "LOW") and "USD" in resultado["precios_por_moneda"]
    kw = falso.llamadas[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "GRA-010" in kw["system"][0]["text"] and "MercadoAgentico" in kw["system"][0]["text"]
    assert "[Ahora: miércoles 2026-09-30 11:30" in kw["messages"][0]["content"]


async def test_herramientas_en_paralelo_y_negocio_al_interno(servicio):
    s, falso = _servicio(servicio, [
        _resp([_tool("a", "herramienta_negocio", {"nombre": "get_product", "parametros": {"sku": "CAR-001"}}),
               _tool("b", "comparar_producto", {"sku": "CAR-001"})], "tool_use"),
        _resp([_txt("Vendes el pollo un 13,5 % por encima del mercado.")], "end_turn"),
    ])
    r = await s.orquestador.responder("tg:1", "¿Cómo va mi negocio de pollo?")
    assert [h["destino"] for h in r.herramientas] == ["interno", "central"]
    bloques = falso.llamadas[1]["messages"][-1]["content"]
    assert len(bloques) == 2 and not any(b["is_error"] for b in bloques)        # un solo mensaje con ambos resultados
    cruce = json.loads(bloques[1]["content"])
    assert cruce["posicion"]["etiqueta"] == "por_encima" and cruce["propuestas"]


async def test_historial_solo_se_amplia(servicio):
    s, falso = _servicio(servicio, [_resp([_txt("Hola.")], "end_turn"), _resp([_txt("Sigo aquí.")], "end_turn")])
    await s.orquestador.responder("tg:9", "hola")
    await s.orquestador.responder("tg:9", "¿sigues?")
    primero, segundo = falso.llamadas[0]["messages"], falso.llamadas[1]["messages"]
    assert segundo[:len(primero)] == primero and len(segundo) == 3


async def test_parametros_invalidos_vuelven_como_error(servicio):
    s, falso = _servicio(servicio, [
        _resp([_tool("x", "analizar_mercado", {"sku": "NO-VIGILADO"}), _tool("y", "informe_negocio", {"informe": "todo"})], "tool_use"),
        _resp([_txt("No tengo ese producto vigilado.")], "end_turn"),
    ])
    r = await s.orquestador.responder("tg:1", "¿y el jabón?")
    bloques = falso.llamadas[1]["messages"][-1]["content"]
    assert all(b["is_error"] for b in bloques)
    assert [h["status"] for h in r.herramientas] == ["error", "error"]


async def test_rechazo_y_error_de_api(servicio):
    s, _ = _servicio(servicio, [_resp([], "refusal")])
    r = await s.orquestador.responder("tg:1", "x")
    assert r.estado == "rechazado" and "no puedo" in r.texto
    peticion = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    s._orquestador = None
    s, _ = _servicio(s, [anthropic.APIConnectionError(request=peticion)])
    r = await s.orquestador.responder("tg:1", "x")
    assert r.estado == "error" and "tg:1" not in s.orquestador._conversaciones


async def test_filtro_de_salida(servicio):
    s, _ = _servicio(servicio, [_resp([_txt("Uso get_inventory_status con postgresql://u:p@h/db")], "end_turn")])
    r = await s.orquestador.responder("tg:1", "x")
    assert r.estado == "bloqueado" and "postgresql" not in r.texto


async def test_ejecutor_resumen_panel_y_producto_no_vigilado(servicio):
    e = Ejecutor(servicio)
    res, traza = await e.ejecutar("resumen_panel", {})
    assert res["status"] == "ok" and res["propuestas"][0]["prioridad"] == "alta" and traza["destino"] == "central"
    res, _ = await e.ejecutar("comparar_producto", {"sku": "BEB-001"})
    assert res["status"] == "error"
    res, _ = await e.ejecutar("comparar_producto", {"sku": "GRA-012", "producto_mercado": {"name": "leche en polvo", "quantity": 1, "unit": "kg"}})
    assert res["status"] == "ok" and res["interno"]["estado_stock"] == "riesgo_rotura"
    res, _ = await e.ejecutar("borrar_todo", {})
    assert res["status"] == "error"
