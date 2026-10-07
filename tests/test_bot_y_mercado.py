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
    assert {"resumen", "alertas", "estudio"} <= {j.name for j in bot.app.job_queue.jobs()}


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


async def test_un_estudio_por_dia(tmp_path):
    """El panel se hace una vez al día (con mercado fresco) y se reutiliza, también tras reiniciar el servicio."""
    from datetime import datetime

    from agente_central.tiempo import Reloj

    cfg = cargar(tmp_path / "datos")
    zona = cfg.empresa["zona_horaria"]

    def servicio_el(dia: str) -> tuple[Servicio, list]:
        s = crear_servicio(cfg, tmp_path)
        s.reloj_estudio = s.constructor.reloj_estudio = Reloj(zona, datetime.fromisoformat(dia))
        llamadas = []
        construir = s.constructor.construir

        async def contar(**kw):
            llamadas.append(kw)
            return await construir(**kw)
        s.constructor.construir = contar
        return s, llamadas

    s, llamadas = servicio_el("2026-10-07T09:00:00-04:00")
    assert not s.panel_de_hoy()
    p = await s.panel()
    assert llamadas == [{"forzar_mercado": True}]
    assert p["meta"]["estudio"] == "2026-10-07" and p["meta"]["generado"].startswith("2026-10-07T09:00")
    assert p["meta"]["periodo"]["hasta"] == "2026-09-30"           # el negocio de prueba sigue en su fecha
    assert await s.panel() is p and await s.panel_html() and len(llamadas) == 1 and s.panel_de_hoy()

    s2, llamadas2 = servicio_el("2026-10-07T18:00:00-04:00")       # reinicio el mismo día: se reutiliza
    assert s2.panel_de_hoy() and (await s2.panel())["meta"]["generado"] == p["meta"]["generado"] and not llamadas2

    s3, llamadas3 = servicio_el("2026-10-08T07:30:00-04:00")       # día nuevo: estudio nuevo
    assert (await s3.panel())["meta"]["estudio"] == "2026-10-08" and llamadas3 == [{"forzar_mercado": True}]
    await s3.panel(forzar=True)                                    # actualizar a mano: mercado de la caché
    assert llamadas3[-1] == {"forzar_mercado": False}


async def test_panel_de_formato_anterior_se_rehace_con_el_mercado_de_la_cache(tmp_path):
    """Tras desplegar un cambio de presentación, el panel del día guardado con el formato viejo no se reutiliza."""
    import json as _json
    from datetime import datetime

    from agente_central.tiempo import Reloj

    cfg = cargar(tmp_path / "datos")
    s = crear_servicio(cfg, tmp_path)
    s.reloj_estudio = s.constructor.reloj_estudio = Reloj(cfg.empresa["zona_horaria"], datetime.fromisoformat("2026-10-07T16:30:00-04:00"))
    viejo = {"meta": {"estudio": "2026-10-07", "generado": "2026-10-07T09:00", "periodo": {"hasta": "2026-09-30"}}, "propuestas": []}
    (cfg.directorio_datos).mkdir(parents=True, exist_ok=True)
    (cfg.directorio_datos / "panel.json").write_text(_json.dumps(viejo))
    s._panel = s._cargar()
    llamadas = []
    construir = s.constructor.construir

    async def contar(**kw):
        llamadas.append(kw)
        return await construir(**kw)
    s.constructor.construir = contar
    assert not s.panel_de_hoy()
    p = await s.panel()
    assert llamadas == [{"forzar_mercado": False}] and p["meta"]["formato"] >= 2 and p["meta"]["tasa_dia"]["usd_cup"]
    assert s.panel_de_hoy() and await s.panel() is p and len(llamadas) == 1


async def test_negocio_en_cup_y_menu_de_comandos(servicio, monkeypatch):
    from telegram.ext import ExtBot

    from agente_central.bot_telegram import MENU

    bot = BotTelegram(servicio, "123456:ABCDEF", (42,))
    linea = (await bot._linea_negocio()).replace(" ", " ").replace(" ", " ")
    assert linea.startswith("Hoy hasta las 11:30: ") and " CUP (" in linea and "USD) en " in linea and "Mes: " in linea

    publicados = {}

    async def set_my_commands(self, comandos, **kw):
        publicados["comandos"] = [(c.command, c.description) for c in comandos]
        return True

    async def set_chat_menu_button(self, **kw):
        publicados["boton"] = kw["menu_button"].type
        return True
    monkeypatch.setattr(ExtBot, "set_my_commands", set_my_commands)
    monkeypatch.setattr(ExtBot, "set_chat_menu_button", set_chat_menu_button)
    await bot.configurar_menu()
    assert [c for c, _ in publicados["comandos"]] == ["panel", "oportunidades", "negocio", "mercado", "producto", "ayuda"]
    assert publicados["boton"] == "commands"
    registrados = {c for h in bot.app.handlers[0] for c in getattr(h, "commands", ())}
    assert all(c in registrados and len(d) <= 256 for c, d in MENU)


async def test_el_analisis_incluye_los_anuncios_recien_capturados(tmp_path):
    """Las capturas de la propia consulta se fechan al descargarse, después del inicio del análisis: deben contar."""
    import json as _json
    from datetime import datetime, timedelta

    from controlador_mercado import JsonFileSource, SourceRegistry

    from agente_central.tiempo import Reloj

    inicio = datetime.fromisoformat("2026-10-07T12:00:00-04:00")
    descarga = inicio + timedelta(minutes=3)            # la web se consulta con pausas: termina unos minutos después
    anuncios = [{"listing_id": f"a{i}", "url": f"https://ejemplo.invalid/{i}", "title": "Aceite de girasol 1 L",
                 "seller": f"v{i}", "province": "La Habana", "price": 4.0 + i / 10, "currency": "USD",
                 "captured_at": (descarga + timedelta(seconds=i)).isoformat()} for i in range(8)]
    ruta = tmp_path / "fuente"
    ruta.mkdir()
    (ruta / "web.json").write_text(_json.dumps({"source_id": "web", "source_name": "Web", "observations": anuncios}))
    cfg = cargar(tmp_path / "datos")
    mercado = ClienteMercado(cfg, Reloj(cfg.empresa["zona_horaria"], inicio), registro=SourceRegistry([JsonFileSource(ruta)]))
    a = await mercado.analizar({"name": "aceite de girasol", "quantity": 1, "unit": "L"})
    precios = a["price_statistics"]["by_currency"]["USD"]["presentation_price"]
    assert precios["n"] == 8 and 4.3 <= precios["price_median"] <= 4.4


async def test_si_algo_falla_el_bot_contesta(servicio, monkeypatch):
    from telegram.ext import ExtBot

    enviados = []

    async def send_message(self, chat_id, text, **kw):
        enviados.append((chat_id, text))
    monkeypatch.setattr(ExtBot, "send_message", send_message)
    bot = BotTelegram(servicio, "123456:ABCDEF", (42,))
    assert bot._error in bot.app.error_handlers
    await bot._error(NS(effective_chat=NS(id=42)), NS(error=RuntimeError("caída")))
    await bot._error(NS(effective_chat=NS(id=99)), NS(error=RuntimeError("caída")))      # desconocido: no se le contesta
    assert enviados == [(42, "⚠️ No pude completar la petición (RuntimeError). Ha quedado registrado; "
                             "inténtalo de nuevo en unos minutos.")]
