"""Moneda principal: tasa del día de elTOQUE (con respaldo del Agente Interno) y formato CUP (USD)."""
import json
from datetime import datetime

import httpx

from agente_central.moneda import TasaDia, dinero_de_panel, formateador
from agente_central.tiempo import Reloj

AHORA = datetime.fromisoformat("2026-10-07T10:00:00-04:00")


def test_formato_cup_con_equivalente_usd():
    d = lambda *a: formateador(760, "CUP")(*a).replace("\u202f", " ").replace("\u00a0", " ")
    assert d(72.5) == "55 100 CUP (72,50 USD)"
    assert d(1234.4, 0) == "938 144 CUP (1 234 USD)"
    n = lambda t: t.replace("\u202f", " ").replace("\u00a0", " ")
    assert n(formateador(760, "USD")(2)) == "2,00 USD (1 520 CUP)"
    assert n(formateador(None)(3.5)) == "3,50 USD" and d(None) == "—"
    assert n(dinero_de_panel({"meta": {"tasa_dia": {"usd_cup": 500}}})(1)) == "500 CUP (1,00 USD)"
    assert "\u202f" in formateador(760)(72.5) and "\u00a0CUP" in formateador(760)(72.5)   # no se parte al cortar líneas


def _cliente(respuestas: list, pedidas: list):
    def responder(req: httpx.Request):
        pedidas.append(req)
        r = respuestas.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)
    return httpx.AsyncClient(transport=httpx.MockTransport(responder))


async def test_tasa_del_dia_de_eltoque_una_vez_al_dia(tmp_path):
    pedidas = []
    cli = _cliente([{"date": "2026-10-07", "hour": 9, "minutes": 5, "tasas": {"USD": 770.0, "ECU": 860}}], pedidas)
    t = TasaDia(Reloj("America/Havana", AHORA), tmp_path / "tasa.json", "clave", cli)
    r = await t.obtener({"date": "2026-09-30", "usd_cup": 741.74})
    assert r["usd_cup"] == 770.0 and r["fecha"] == "2026-10-07" and r["hora"] == "09:05" and r["fuente"] == "elTOQUE"
    assert pedidas[0].headers["Authorization"] == "Bearer clave"
    assert pedidas[0].url.params["date_to"] == "2026-10-07 10:00:00"
    # Mismo día (también otro proceso): se reutiliza la guardada sin llamar a la API.
    otra = TasaDia(Reloj("America/Havana", AHORA), tmp_path / "tasa.json", "clave", _cliente([], pedidas))
    assert (await otra.obtener())["usd_cup"] == 770.0 and len(pedidas) == 1
    assert json.loads((tmp_path / "tasa.json").read_text())["dia"] == "2026-10-07"


async def test_sin_eltoque_se_usa_la_del_agente_interno(tmp_path):
    pedidas = []
    t = TasaDia(Reloj("America/Havana", AHORA), tmp_path / "t.json", "clave", _cliente([httpx.Response(503)], pedidas))
    r = await t.obtener({"date": "2026-09-30", "usd_cup": 741.74, "source": "elTOQUE"})
    assert r["usd_cup"] == 741.74 and "Agente Interno" in r["fuente"] and "elTOQUE no responde" in r["aviso"]
    await t.obtener({"date": "2026-09-30", "usd_cup": 741.74})       # no reintenta antes de una hora
    assert len(pedidas) == 1 and not (tmp_path / "t.json").exists()
    sin_clave = await TasaDia(Reloj("America/Havana", AHORA), tmp_path / "t.json", "").obtener({"date": "2026-09-30", "usd_cup": 741.74})
    assert "ELTOQUE_API_KEY" in sin_clave["aviso"]


async def test_la_tasa_se_renueva_cada_hora(tmp_path):
    from datetime import timedelta

    pedidas = []
    respuestas = [{"date": "2026-10-07", "hour": 9, "minutes": 5, "tasas": {"USD": 770.0}},
                  {"date": "2026-10-07", "hour": 11, "minutes": 2, "tasas": {"USD": 775.0}}, httpx.Response(503)]
    cli = _cliente(respuestas, pedidas)
    t = TasaDia(Reloj("America/Havana", AHORA), tmp_path / "t.json", "clave", cli)
    assert (await t.obtener())["usd_cup"] == 770.0
    t.reloj = Reloj("America/Havana", AHORA + timedelta(minutes=30))
    assert (await t.obtener())["usd_cup"] == 770.0 and len(pedidas) == 1          # aún vigente
    t.reloj = Reloj("America/Havana", AHORA + timedelta(minutes=65))
    assert (await t.obtener())["usd_cup"] == 775.0 and len(pedidas) == 2          # renovada
    t.reloj = Reloj("America/Havana", AHORA + timedelta(minutes=130))
    assert (await t.obtener())["usd_cup"] == 775.0 and len(pedidas) == 3          # elTOQUE falla: la última de hoy
