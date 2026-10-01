import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from agente_central.config import Config  # noqa: E402
from agente_central.servicio import Servicio  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return Config.cargar(RAIZ / "config" / "empresa.demo.yaml", entorno={"DIRECTORIO_DATOS": str(tmp_path / "datos")})


@pytest.fixture
def servicio(cfg):
    return Servicio(cfg)


@pytest.fixture(scope="session")
def panel_demo(tmp_path_factory):
    import asyncio
    c = Config.cargar(RAIZ / "config" / "empresa.demo.yaml",
                      entorno={"DIRECTORIO_DATOS": str(tmp_path_factory.mktemp("datos"))})
    return asyncio.run(Servicio(c).panel())
