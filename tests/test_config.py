import pytest
import yaml

from agente_central.config import Config, ErrorConfig
from tests.conftest import RAIZ


def _perfil(tmp_path, cambios):
    datos = yaml.safe_load((RAIZ / "config" / "empresa.demo.yaml").read_text(encoding="utf-8"))
    for ruta, valor in cambios.items():
        d = datos
        *padres, ultimo = ruta.split(".")
        for p in padres:
            d = d[p]
        d[ultimo] = valor
    f = tmp_path / "empresa.yaml"
    f.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    return f


def test_perfil_demo_completo(cfg):
    assert cfg.nombre == "MercadoAgentico"
    assert len(cfg.vigilados) == 8
    assert cfg["interno"]["modo"] == "demo"
    assert cfg.ahora_fija.isoformat() == "2026-09-30T11:30:00-04:00"
    assert cfg["reglas"]["margen_minimo_pct"] == 10


def test_valores_por_defecto(tmp_path):
    f = tmp_path / "minimo.yaml"
    f.write_text("empresa: {id: x, nombre: Tienda X}\n", encoding="utf-8")
    c = Config.cargar(f, entorno={})
    assert c["marca"]["colores"]["primario"] == "#101063"     # formato Quantic por defecto
    assert c["interno"]["modo"] == "api" and c["mercado"]["modo"] == "motor"
    assert c.vigilados == {}


def test_secretos_del_entorno(tmp_path):
    c = Config.cargar(RAIZ / "config" / "empresa.demo.yaml", entorno={
        "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_USUARIOS": "111, 222", "ORQUESTADOR_TOKEN": "s3",
        "INTERNO_MODO": "api", "AHORA_FIJA": "", "PANEL_URL_PUBLICA": "https://panel.ejemplo/"})
    assert c.telegram_usuarios == (111, 222)
    assert c.token_interno == "s3"
    assert c["interno"]["modo"] == "api"
    assert c.ahora_fija is None
    assert c.panel_url_publica == "https://panel.ejemplo"


@pytest.mark.parametrize("cambios, mensaje", [
    ({"interno.modo": "sql"}, "interno.modo"),
    ({"mercado.modo": "magia"}, "mercado.modo"),
    ({"mercado.fuentes": [{"tipo": "web", "nombre": "facebook"}]}, "Fuente web desconocida"),
    ({"mercado.fuentes": [{"tipo": "archivo"}]}, "ruta"),
    ({"marca.colores": {"primario": "azul"}}, "Color"),
    ({"catalogo_vigilado": [{"sku": "A", "mercado": {"name": "x"}}, {"sku": "A", "mercado": {"name": "y"}}]}, "repetido"),
    ({"catalogo_vigilado": [{"sku": "A", "mercado": {}}]}, "mercado.name"),
    ({"empresa.nombre": ""}, "nombre"),
])
def test_perfiles_invalidos(tmp_path, cambios, mensaje):
    with pytest.raises(ErrorConfig, match=mensaje):
        Config.cargar(_perfil(tmp_path, cambios), entorno={})


def test_perfil_inexistente(tmp_path):
    with pytest.raises(ErrorConfig):
        Config.cargar(tmp_path / "no.yaml", entorno={})
