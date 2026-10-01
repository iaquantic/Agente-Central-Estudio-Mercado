"""Configuración del Agente Central.

Dos fuentes, separadas a propósito:
- **Perfil de empresa** (`config/empresa*.yaml`): todo lo que cambia de un cliente a otro (marca, catálogo vigilado,
  fuentes de mercado, umbrales). Se versiona y se puede compartir.
- **Variables de entorno** (`.env`): secretos y datos de despliegue. Nunca se registran sus valores.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(os.environ.get("AGENTE_CENTRAL_HOME") or Path(__file__).resolve().parent.parent)
CONFIG_DEMO = RAIZ / "config" / "empresa.demo.yaml"

DEFECTOS: dict[str, Any] = {
    "empresa": {"zona_horaria": "America/Havana", "moneda": "USD", "moneda_local": "CUP", "pais": "Cuba", "descripcion": ""},
    "marca": {"proveedor": "Quantic Data", "logo": "config/marca/quantic.svg", "logo_cliente": None,
              "colores": {"primario": "#101063", "acento": "#8070E7", "acento_fuerte": "#5C47E0", "suave": "#E2E1FF"},
              "tipografias": {"titulos": "Space Grotesk", "texto": "Archivo"}},
    "interno": {"modo": "api", "url": "http://127.0.0.1:8080/v1/consulta", "token_env": "ORQUESTADOR_TOKEN",
                "timeout_s": 30, "fixtures": "demo/interno/fixtures.json"},
    "mercado": {"modo": "motor", "fuentes": [], "provincias": [], "convertir_a": "USD", "granularidad": "week",
                "dias_periodo": 7, "semanas_historial": 12, "cache_horas": 12},
    "panel": {"meses_historial": 12, "top_n": 10, "refresco_minutos": 60, "titulo": "Panel de estudio de mercado"},
    "reglas": {"margen_minimo_pct": 10, "diferencia_precio_pct": 8, "variacion_tendencia_pct": 8,
               "cobertura_minima_dias": 7, "dias_horizonte": 30,
               "diferencia_maxima_fiable_pct": 50},
    "telegram": {"resumen_diario": "19:45", "alertas_urgentes": True, "revisar_alertas_cada_min": 30,
                 "silencio": ["21:00", "08:00"], "max_alertas_dia": 3, "limite_consultas_hora": 30},
    "modelo": {"nombre": "claude-opus-5-5", "esfuerzo": "medium"},
    "demo": {"ahora": None},
    "catalogo_vigilado": [],
}
MODOS_INTERNO = ("api", "demo")
MODOS_MERCADO = ("motor", "agente")
FUENTES_WEB = ("revolico", "cuballama", "cubamax", "cubatel")


class ErrorConfig(ValueError):
    pass


def _fusionar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _fusionar(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def ruta(p: str | None) -> Path | None:
    """Las rutas relativas del perfil se resuelven desde la raíz del proyecto."""
    if not p:
        return None
    q = Path(p)
    return q if q.is_absolute() else RAIZ / q


@dataclass
class Config:
    datos: dict
    origen: Path | None = None
    # Secretos y despliegue (entorno)
    telegram_token: str | None = None
    telegram_usuarios: tuple[int, ...] = ()
    token_interno: str | None = None
    panel_token: str | None = None
    panel_url_publica: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8090
    directorio_datos: Path = field(default_factory=lambda: RAIZ / "datos")

    # Accesos cómodos ---------------------------------------------------------------------------
    def __getitem__(self, clave: str) -> Any:
        return self.datos[clave]

    @property
    def empresa(self) -> dict:
        return self.datos["empresa"]

    @property
    def nombre(self) -> str:
        return self.datos["empresa"]["nombre"]

    @property
    def ahora_fija(self) -> datetime | None:
        a = (self.datos.get("demo") or {}).get("ahora")
        return datetime.fromisoformat(str(a)) if a else None

    @property
    def vigilados(self) -> dict[str, dict]:
        return {p["sku"]: p for p in self.datos["catalogo_vigilado"]}

    @staticmethod
    def cargar(archivo: str | Path | None = None, *, entorno: dict[str, str] | None = None) -> "Config":
        env = os.environ if entorno is None else entorno
        origen = Path(archivo or env.get("EMPRESA_CONFIG") or CONFIG_DEMO)
        if not origen.is_absolute():
            origen = (Path.cwd() / origen) if (Path.cwd() / origen).exists() else RAIZ / origen
        try:
            crudo = yaml.safe_load(origen.read_text(encoding="utf-8")) or {}
        except FileNotFoundError as e:
            raise ErrorConfig(f"No existe el perfil de empresa {origen}") from e
        datos = _fusionar(DEFECTOS, crudo)
        if env.get("INTERNO_MODO"):
            datos["interno"]["modo"] = env["INTERNO_MODO"]
        if env.get("INTERNO_URL"):
            datos["interno"]["url"] = env["INTERNO_URL"]
        if env.get("MODELO_CLAUDE"):
            datos["modelo"]["nombre"] = env["MODELO_CLAUDE"]
        if "AHORA_FIJA" in env:                                  # "" desactiva la hora congelada del perfil
            datos["demo"]["ahora"] = env["AHORA_FIJA"] or None
        ids = tuple(int(x) for x in (env.get("TELEGRAM_USUARIOS") or env.get("TELEGRAM_OWNER_ID") or "").replace(";", ",").split(",") if x.strip())
        cfg = Config(
            datos=datos, origen=origen,
            telegram_token=env.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_API") or None,   # se aceptan ambos nombres
            telegram_usuarios=ids,
            token_interno=env.get(datos["interno"].get("token_env") or "ORQUESTADOR_TOKEN") or None,
            panel_token=env.get("PANEL_TOKEN") or None,
            panel_url_publica=(env.get("PANEL_URL_PUBLICA") or "").rstrip("/") or None,
            api_host=env.get("API_HOST") or "127.0.0.1",
            api_port=int(env.get("API_PORT") or 8090),
            directorio_datos=Path(env.get("DIRECTORIO_DATOS") or RAIZ / "datos" / datos["empresa"].get("id", "empresa")),
        )
        cfg.validar()
        return cfg

    def validar(self) -> None:
        d = self.datos
        faltan = [c for c in ("id", "nombre") if not d["empresa"].get(c)]
        if faltan:
            raise ErrorConfig(f"Faltan campos en 'empresa': {', '.join(faltan)}")
        if d["interno"]["modo"] not in MODOS_INTERNO:
            raise ErrorConfig(f"interno.modo debe ser uno de {MODOS_INTERNO}")
        if d["mercado"]["modo"] not in MODOS_MERCADO:
            raise ErrorConfig(f"mercado.modo debe ser uno de {MODOS_MERCADO}")
        for f in d["mercado"]["fuentes"]:
            if f.get("tipo") == "web" and f.get("nombre") not in FUENTES_WEB:
                raise ErrorConfig(f"Fuente web desconocida: {f.get('nombre')} (válidas: {', '.join(FUENTES_WEB)})")
            if f.get("tipo") == "archivo" and not f.get("ruta"):
                raise ErrorConfig("Las fuentes de tipo 'archivo' necesitan 'ruta'")
            if f.get("tipo") not in ("web", "archivo"):
                raise ErrorConfig(f"Tipo de fuente no válido: {f.get('tipo')}")
        vistos = set()
        for p in d["catalogo_vigilado"]:
            if not p.get("sku") or not (p.get("mercado") or {}).get("name"):
                raise ErrorConfig("Cada producto vigilado necesita 'sku' y 'mercado.name'")
            if p["sku"] in vistos:
                raise ErrorConfig(f"SKU repetido en catalogo_vigilado: {p['sku']}")
            vistos.add(p["sku"])
        for color in d["marca"]["colores"].values():
            if not (isinstance(color, str) and color.startswith("#") and len(color) in (4, 7)):
                raise ErrorConfig(f"Color no válido en marca.colores: {color}")
