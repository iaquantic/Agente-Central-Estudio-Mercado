from datetime import datetime

from fastapi.testclient import TestClient

from agente_central.formato import filtrar, html_telegram, propuestas_html, trocear
from agente_central.planificador import Avisador, en_silencio
from agente_central.servidor import crear_app
from tests.conftest import cargar, crear_servicio


def _servicio(tmp_path, **env):
    return crear_servicio(cargar(tmp_path, **env), tmp_path)


def test_panel_web_con_token(tmp_path):
    c = TestClient(crear_app(_servicio(tmp_path, PANEL_TOKEN="abc")))
    assert c.get("/salud").json()["empresa"] == "mercadoagentico"
    assert c.get("/panel").status_code == 401
    assert c.get("/panel?t=mal").status_code == 401
    r = c.get("/panel?t=abc")
    assert r.status_code == 200 and "MercadoAgentico" in r.text and r.headers["cache-control"] == "no-store"
    j = c.get("/api/panel", headers={"Authorization": "Bearer abc"}).json()
    assert j["version"] == "1.0" and j["propuestas"]
    assert (tmp_path / "panel.html").exists() and (tmp_path / "panel.json").exists()
    assert c.post("/api/panel/actualizar?t=abc").json()["propuestas"] == len(j["propuestas"])


def test_panel_web_sin_token_configurado(tmp_path):
    assert TestClient(crear_app(_servicio(tmp_path))).get("/api/panel").status_code == 200


def test_silencio_nocturno():
    t = lambda h: datetime(2026, 9, 30, h, 0)   # noqa: E731
    assert en_silencio(t(22), ["21:00", "08:00"]) and en_silencio(t(3), ["21:00", "08:00"])
    assert not en_silencio(t(9), ["21:00", "08:00"]) and not en_silencio(t(22), None)


async def test_alertas_urgentes_sin_repetir_y_con_maximo(tmp_path):
    s = _servicio(tmp_path)
    av = Avisador(s, tmp_path / "enviadas.json")
    primeras = await av.alertas_pendientes()
    assert len(primeras) == 3 and all(a["priority"] == "urgent" for a in primeras)   # 4 urgentes, máximo 3 al día
    assert await av.alertas_pendientes() == []
    assert "🚨 <b>Aceite de girasol 1 L agotado</b>" in av.texto_alerta(primeras[0])


async def test_resumen_diario(tmp_path):
    s = _servicio(tmp_path)
    texto = await Avisador(s, tmp_path / "e.json").resumen_diario("https://panel/x")
    assert "Resumen de MercadoAgentico" in texto and "Decisiones propuestas" in texto and "https://panel/x" in texto
    assert "100 706 USD" in texto


def test_formato_telegram():
    assert html_telegram("<b>ok</b> <script>x</script> & 3 < 4") == "<b>ok</b> &lt;script&gt;x&lt;/script&gt; &amp; 3 &lt; 4"
    partes = trocear("a" * 5000 + "\n" + "b" * 10)
    assert all(len(p) <= 4000 for p in partes) and "".join(partes).replace("\n", "") == "a" * 5000 + "b" * 10
    assert filtrar("clave sk-ant-abc123")[1] and not filtrar("Todo bien")[1]
    t = propuestas_html([{"prioridad": "alta", "nombre": "A & B", "titulo": "T", "detalle": "D", "impacto_usd": 1200}])
    assert "🔴 <b>A &amp; B</b>" in t and "1 200 USD" in t
