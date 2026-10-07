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
from .moneda import TasaDia, fx_de
from .moneda import formateador
from .panel import FORMATO_PANEL, ConstructorPanel, huella_perfil, periodos
from .panel_html import renderizar
from .registro import Registro
from .tiempo import Reloj

log = logging.getLogger(__name__)
TASA_CACHE_S = 3600


class Servicio:
    def __init__(self, cfg: Config, *, interno=None, mercado: ClienteMercado | None = None, reloj: Reloj | None = None,
                 cliente_claude=None, reloj_estudio: Reloj | None = None):
        self.cfg = cfg
        self.reloj = reloj or Reloj(cfg.empresa["zona_horaria"], cfg.ahora_fija)
        # El estudio de mercado y su fecha van siempre en tiempo real (los anuncios son de hoy), aunque el negocio de
        # demostración tenga la hora congelada (demo.ahora solo afecta al Agente Interno y al periodo del negocio).
        self.reloj_estudio = reloj_estudio or Reloj(cfg.empresa["zona_horaria"])
        self.interno = interno or crear_cliente_interno(cfg, self.reloj)
        self.mercado = mercado or ClienteMercado(cfg, self.reloj_estudio, directorio_cache=cfg.directorio_datos / "mercado")
        self.tasa_dia = TasaDia(self.reloj_estudio, cfg.directorio_datos / "tasa_dia.json", cfg.eltoque_api_key or "")
        self.constructor = ConstructorPanel(cfg, self.interno, self.mercado, self.reloj, self.reloj_estudio, self.tasa_dia)
        self.registro = Registro(cfg.directorio_datos / "registro.jsonl")
        self._panel: dict | None = self._cargar()
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
        """Tasa de hoy (elTOQUE, o la del Agente Interno si no hay clave) y serie de 2 meses del Agente Interno (caché 1 h)."""
        if self._tasa and time.monotonic() - self._tasa[0] < TASA_CACHE_S:
            return self._tasa[1], self._tasa[2]
        p = periodos(self.reloj.hoy())
        res = await self.interno.herramienta("get_exchange_rate", {"from": p["tasa_desde"].isoformat(), "to": p["hasta"].isoformat()})
        d = datos(res) or {}
        fx = d.get("today") or tasa(res)
        fx = fx_de(await self.tasa_dia.obtener(fx)) or fx       # la de hoy (elTOQUE) si está disponible
        self._tasa = (time.monotonic(), fx, d.get("series") or [])
        return fx, self._tasa[2]

    def panel_de_hoy(self) -> bool:
        """¿Ya está hecho el estudio de hoy? (para avisar al dueño de que el primero del día tarda unos minutos)."""
        return (bool(self._panel) and self._panel["meta"].get("estudio") == self.reloj_estudio.hoy().isoformat()
                and self._panel["meta"].get("formato") == FORMATO_PANEL
                and self._panel["meta"].get("huella") == huella_perfil(self.cfg))     # el perfil no ha cambiado

    async def dinero(self):
        """Formato de importes con la tasa de hoy: «2 926 CUP (3,80 USD)» (o al revés si la moneda principal es USD)."""
        fx, _ = await self.tasa()
        return formateador((fx or {}).get("usd_cup"), self.cfg["panel"]["moneda_principal"])

    async def panel(self, *, forzar: bool = False, forzar_mercado: bool = False) -> dict:
        """El estudio del día: se hace una sola vez al día (el primero que se pida o a `panel.hora_estudio`) y después
        se reutiliza, también tras reiniciar el servicio (se guarda en panel.json).

        Un estudio nuevo consulta siempre las webs de mercado. `forzar` rehace el panel en el mismo día con datos
        frescos del negocio (los análisis de mercado salen de su caché); `forzar_mercado` también rehace el mercado."""
        async with self._panel_lock:
            nuevo_dia = not (self._panel and self._panel["meta"].get("estudio") == self.reloj_estudio.hoy().isoformat())
            if forzar or forzar_mercado or not self.panel_de_hoy():     # día nuevo o panel de un formato anterior
                inicio = time.monotonic()
                log.info("Estudio del día: empieza (mercado %s)", "con consultas nuevas a las webs" if forzar_mercado or nuevo_dia
                         else "de la caché del día")
                limite = float(self.cfg["panel"]["max_minutos_estudio"]) * 60
                try:
                    self._panel = await asyncio.wait_for(self.constructor.construir(forzar_mercado=forzar_mercado or nuevo_dia),
                                                         timeout=limite)
                except asyncio.TimeoutError:
                    log.error("Estudio del día: no terminó en %d min (¿una fuente web no responde?)", limite / 60)
                    raise TimeoutError(f"el estudio no terminó en {limite / 60:.0f} minutos") from None
                self._guardar(self._panel)
                log.info("Estudio del día: listo en %d s (%d productos, %d propuestas)", time.monotonic() - inicio,
                         len(self._panel["productos"]), len(self._panel["propuestas"]))
                await self.registro.evento("panel", {"ms": int((time.monotonic() - inicio) * 1000),
                                                     "propuestas": len(self._panel["propuestas"]),
                                                     "avisos": self._panel["calidad"]["avisos"]})
            return self._panel

    def _cargar(self) -> dict | None:
        try:
            return json.loads((self.cfg.directorio_datos / "panel.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

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

