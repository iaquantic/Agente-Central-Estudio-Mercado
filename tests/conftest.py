import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from controlador_mercado import JsonFileSource, SourceRegistry  # noqa: E402

from agente_central.config import Config  # noqa: E402
from agente_central.externo import ClienteMercado  # noqa: E402
from agente_central.servicio import Servicio  # noqa: E402
from agente_central.tiempo import Reloj  # noqa: E402
from tests.mercado_prueba import escribir  # noqa: E402


def crear_servicio(cfg: Config, directorio: Path) -> Servicio:
    """Servicio sin red: negocio de prueba (fixtures) y mercado sintético con la hora congelada del perfil."""
    reloj = Reloj(cfg.empresa["zona_horaria"], cfg.ahora_fija)
    registro = SourceRegistry([JsonFileSource(escribir(directorio / "mercado_prueba"))])
    mercado = ClienteMercado(cfg, reloj, registro=registro, directorio_cache=directorio / "cache")
    return Servicio(cfg, mercado=mercado, reloj=reloj)


def cargar(directorio: Path, **env) -> Config:
    return Config.cargar(RAIZ / "config" / "empresa.demo.yaml", entorno={"DIRECTORIO_DATOS": str(directorio), **env})


@pytest.fixture
def cfg(tmp_path):
    return cargar(tmp_path / "datos")


@pytest.fixture
def servicio(cfg, tmp_path):
    return crear_servicio(cfg, tmp_path)


@pytest.fixture(scope="session")
def panel_demo(tmp_path_factory):
    import asyncio
    d = tmp_path_factory.mktemp("datos")
    return asyncio.run(crear_servicio(cargar(d), d).panel())
