"""Servicio: conecta configuración, subagentes, panel y orquestador. Lo comparten el bot, la web y la consola."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from .config import Config
from .externo import ClienteMercado
from .interno import crear_cliente_interno, datos, tasa
from .panel import ConstructorPanel, periodos
from .panel_html import renderizar
from .registro import Registro
from .tiempo import Reloj

log = logging.getLogger(__name__)
TASA_CACHE_S = 3600


class Servicio:
    def __init__(self, cfg: Config, *, interno=None, mercado: ClienteMercado | None = None, reloj: Reloj | None = None,
                 cliente_claude=None):
        self.cfg = cfg
        self.reloj = reloj or Reloj(cfg.empresa["zona_horaria"], cfg.ahora_fija)
        self.interno = interno or crear_cliente_interno(cfg, self.reloj)
        # El mercado se mide siempre en tiempo real (los anuncios son de hoy), aunque el negocio de demostración
        # tenga la hora congelada (demo.ahora solo afecta al Agente Interno y al periodo del panel).
        self.mercado = mercado or ClienteMercado(cfg, Reloj(cfg.empresa["zona_horaria"]),
                                                 directorio_cache=cfg.directorio_datos / "mercado")
        self.constructor = ConstructorPanel(cfg, self.interno, self.mercado, self.reloj)
        self.registro = Registro(cfg.directorio_datos / "registro.jsonl")
        self._panel: dict | None = None
        self._panel_t = 0.0
        self._panel_lock = asyncio.Lock()
        self._tasa: tuple[float, dict | None, list] | None = None
        self._cliente_claude = cliente_claude
        self._orquestador = None

    @property
    def orquestador(self):
        """Se crea al primer uso: el panel y la web funcionan sin clave de Claude.

        Devuelve None si Claude está desactivado (sin ANTHROPIC_API_KEY o CLAUDE_ACTIVO=0): así es imposible
        hacer llamadas a la API por accidente. Con un cliente inyectado (pruebas) siempre está activo."""
        if self._orquestador is None and not self.cfg.claude_activo and self._cliente_claude is None:
            return None
        if self._orquestador is None:
            from .herramientas import Ejecutor
            from .orquestador import Orquestador
            self._orquestador = Orquestador(self.cfg, Ejecutor(self), self.reloj, cliente=self._cliente_claude)
        return self._orquestador

    async def tasa(self) -> tuple[dict | None, list]:
        """Tasa del día y serie de 2 meses según el Agente Interno (caché 1 h)."""
        if self._tasa and time.monotonic() - self._tasa[0] < TASA_CACHE_S:
            return self._tasa[1], self._tasa[2]
        p = periodos(self.reloj.hoy())
        res = await self.interno.herramienta("get_exchange_rate", {"from": p["tasa_desde"].isoformat(), "to": p["hasta"].isoformat()})
        d = datos(res) or {}
        fx = d.get("today") or tasa(res)
        self._tasa = (time.monotonic(), fx, d.get("series") or [])
        return fx, self._tasa[2]

    async def panel(self, *, forzar: bool = False, forzar_mercado: bool = False) -> dict:
        """Último panel; se regenera si ha caducado (refresco_minutos) o si se fuerza.

        `forzar` rehace el panel con datos frescos del negocio; los análisis de mercado salen de su caché
        (`mercado.cache_horas`) para no consultar las webs en cada refresco. `forzar_mercado` ignora esa caché."""
        caducidad = float(self.cfg["panel"]["refresco_minutos"]) * 60
        async with self._panel_lock:
            if forzar or forzar_mercado or self._panel is None or time.monotonic() - self._panel_t > caducidad:
                inicio = time.monotonic()
                self._panel = await self.constructor.construir(forzar_mercado=forzar_mercado)
                self._panel_t = time.monotonic()
                self._guardar(self._panel)
                await self.registro.evento("panel", {"ms": int((time.monotonic() - inicio) * 1000),
                                                     "propuestas": len(self._panel["propuestas"]),
                                                     "avisos": self._panel["calidad"]["avisos"]})
            return self._panel

    def _guardar(self, panel: dict) -> None:
        d = self.cfg.directorio_datos
        d.mkdir(parents=True, exist_ok=True)
        (d / "panel.json").write_text(json.dumps(panel, ensure_ascii=False, default=str), encoding="utf-8")
        (d / "panel.html").write_text(renderizar(panel, self.cfg["marca"]), encoding="utf-8")

    async def panel_html(self, *, forzar: bool = False) -> str:
        return renderizar(await self.panel(forzar=forzar), self.cfg["marca"])

    def ruta_panel_html(self) -> Path:
        return self.cfg.directorio_datos / "panel.html"

    async def cerrar(self) -> None:
        await self.interno.cerrar()

