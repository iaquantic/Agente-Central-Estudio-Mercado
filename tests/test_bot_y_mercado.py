from types import SimpleNamespace as NS

import pytest

from agente_central.bot_telegram import BotTelegram, LimiteUso
from agente_central.externo import ClienteMercado, limpiar_producto, tasas_desde_fx
from agente_central.servicio import Servicio
from tests.conftest import cargar, crear_servicio


def test_bot_requiere_usuarios(servicio):
    with pytest.raises(ValueError):
        BotTelegram(servicio, "123456:ABCDEF", ())


def test_bot_construye_y_programa(tmp_path):
    s = crear_servicio(cargar(tmp_path, PANEL_URL_PUBLICA="https://p.ejemplo", PANEL_TOKEN="tk"), tmp_path)
    bot = BotTelegram(s, "123456:ABCDEF", (42,))
    assert bot.url_panel() == "https://p.ejemplo/panel?t=tk"
    bot.programar()
    assert {j.name for j in bot.app.job_queue.jobs()} == {"resumen", "alertas", "panel"}


async def test_bot_rechaza_desconocidos_y_grupos(servicio):
    bot = BotTelegram(servicio, "123456:ABCDEF", (42,))
    respuestas = []

    async def reply_text(t, **kw):
        respuestas.append(t)

    def upd(uid, tipo="private"):
        return NS(effective_user=NS(id=uid), effective_chat=NS(type=tipo, leave=None),
                  effective_message=NS(text="hola", reply_text=reply_text))

    assert not await bot._autorizado(upd(7))
    assert respuestas == ["Este asistente es privado. No estás autorizado."]
    assert await bot._autorizado(upd(42))


def test_limite_de_uso():
    lim = LimiteUso(2)
    assert lim.permitir(1) and lim.permitir(1) and not lim.permitir(1) and lim.permitir(2)


def test_producto_y_tasa():
    assert limpiar_producto({"name": "arroz", "brand": "", "quantity": 1, "otro": 3}) == {"name": "arroz", "quantity": 1}
    with pytest.raises(ValueError):
        limpiar_producto({"brand": "X"})
    t = tasas_desde_fx({"date": "2026-09-30", "usd_cup": 741.74, "source": "elTOQUE"})
    assert t[0].rate == 741.74 and t[0].from_currency == "USD" and "elTOQUE" in t[0].source
    assert tasas_desde_fx(None) == []


async def test_cache_de_mercado_en_disco(servicio, tmp_path):
    cfg, reloj, registro = servicio.cfg, servicio.reloj, servicio.mercado.registro
    m = ClienteMercado(cfg, reloj, registro=registro, directorio_cache=tmp_path / "c")
    fx = {"date": "2026-09-30", "usd_cup": 741.74, "source": "elTOQUE"}
    a = await m.analizar({"name": "arroz", "quantity": 1, "unit": "kg"}, fx=fx)
    assert "observations" not in a and a["confidence"] in ("HIGH", "MEDIUM", "LOW")
    assert len(list((tmp_path / "c").glob("*.json"))) == 1
    m2 = ClienteMercado(cfg, reloj, registro=registro, directorio_cache=tmp_path / "c")
    m2.registro.fetch_all = lambda *a, **k: pytest.fail("debería usar la caché")
    assert (await m2.analizar({"name": "arroz", "quantity": 1, "unit": "kg"}, fx=fx))["analysis_id"] == a["analysis_id"]


async def test_analisis_incompleto_no_se_guarda_en_disco(cfg, tmp_path):
    from controlador_mercado.sources import SourceAdapter, SourceRegistry

    class Vacia(SourceAdapter):
        source_id, source_name = "vacia", "Vacía"

        def fetch(self, target, since, until):
            return []

    m = ClienteMercado(cfg, Servicio(cfg).reloj, registro=SourceRegistry([Vacia()]), directorio_cache=tmp_path / "c")
    a = await m.analizar({"name": "arroz"}, fx=None)
    assert a["sources"][0]["status"] == "vacia"
    assert list((tmp_path / "c").glob("*.json")) == []          # no se fija un resultado vacío durante 12 h


async def test_sin_claude_no_se_crea_el_orquestador_y_el_bot_lo_dice(tmp_path):
    s = crear_servicio(cargar(tmp_path, CLAUDE_ACTIVO="0", ANTHROPIC_API_KEY="sk-ant-x"), tmp_path)
    assert s.orquestador is None
    assert crear_servicio(cargar(tmp_path), tmp_path).orquestador is None          # sin clave, tampoco
    bot = BotTelegram(s, "123456:ABCDEF", (42,))
    enviados = []

    async def enviar(chat_id, texto, botones=None):
        enviados.append(texto)

    bot._enviar = enviar
    upd = NS(effective_chat=NS(id=42), effective_user=NS(id=42))
    await bot._consultar(upd, "¿cómo va el aceite?")
    assert "desactivadas" in enviados[0] and "/panel" in enviados[0]
