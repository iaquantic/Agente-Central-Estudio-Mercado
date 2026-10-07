"""Orquestador del Agente Central: bucle de herramientas con Claude.

- El modelo decide a qué subagente preguntar (o al cruce) según la intención del dueño.
- El historial de cada conversación solo se amplía (nunca se recorta ni se edita), para que los bloques de
  razonamiento sigan siendo válidos. Cuando la conversación se alarga o se enfría, empieza otra.
- La fecha y hora van en cada mensaje y no en el system prompt, para poder cachearlo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import anthropic

from .formato import filtrar
from .herramientas import definiciones
from .tiempo import Reloj

log = logging.getLogger(__name__)

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "agente_central_system_prompt.md"
MAX_ITERACIONES = 12
MAX_TURNOS = 12
INACTIVIDAD_S = 2 * 3600
MAX_RESULTADO = 60_000            # caracteres por resultado de herramienta
SIN_RESPUESTA = "Ahora mismo no puedo consultar los datos. Inténtalo en unos minutos."
ESTILOS = {
    "telegram": "- Canal: Telegram. Usa solo HTML de Telegram: <b> para lo importante, <i> para fechas o notas. Nada de "
                "Markdown ni tablas. Listas con \"•\". Máximo unas 12 líneas salvo que te pidan detalle.",
    "cli": "- Canal: consola. Texto plano, sin HTML ni Markdown. Listas con \"•\".",
}


class Ejecutor(Protocol):
    async def ejecutar(self, nombre: str, entrada: dict | None) -> tuple[dict, dict]: ...


@dataclass
class Respuesta:
    texto: str
    herramientas: list[dict] = field(default_factory=list)
    tokens: dict = field(default_factory=dict)
    estado: str = "ok"              # ok | error | rechazado | bloqueado
    error: str | None = None
    latencia_ms: int = 0


@dataclass
class _Conversacion:
    mensajes: list[dict] = field(default_factory=list)
    ultima: float = 0.0
    turnos: int = 0


FORMATO_IMPORTES = {
    "CUP": "Importes: primero en CUP y, entre paréntesis, su equivalente en USD («2 926 CUP (3,80 USD)»). Los negocios "
           "registran en USD: convierte con la tasa USD→CUP de hoy que traen las herramientas (`fx`) y dila al menos una vez.",
    "USD": "Importes: primero en USD y, entre paréntesis, su equivalente en CUP a la tasa de hoy que traen las herramientas "
           "(«3,80 USD (2 926 CUP)»).",
}


def cargar_prompt(cfg, canal: str) -> str:
    vigilados = "\n".join(f"- {p['sku']}: {json.dumps(p['mercado'], ensure_ascii=False)}" for p in cfg["catalogo_vigilado"]) \
        or "- (ninguno configurado)"
    e = cfg.empresa
    return (PROMPT.read_text(encoding="utf-8")
            .replace("{{EMPRESA}}", e["nombre"]).replace("{{DESCRIPCION}}", e.get("descripcion") or "negocio")
            .replace("{{PAIS}}", e.get("pais") or "").replace("{{ESTILO_CANAL}}", ESTILOS.get(canal, ESTILOS["cli"]))
            .replace("{{VIGILADOS}}", vigilados).replace("{{FORMATO_IMPORTES}}", FORMATO_IMPORTES[cfg["panel"]["moneda_principal"]]))


def _texto(contenido: list[Any]) -> str:
    return "\n".join(b.text for b in contenido if getattr(b, "type", None) == "text").strip()


class Orquestador:
    def marca(self) -> str:
        """Fecha y hora reales; en el modo demo, también hasta cuándo llegan los datos del negocio de prueba."""
        m = self.reloj_real.marca()
        if self.reloj.ahora_fija is not None:
            m = m[:-1] + (f". Los datos del negocio son de demostración y llegan hasta el {self.reloj.ahora():%Y-%m-%d %H:%M};"
                          " el mercado y la tasa son de hoy.]")
        return m

    def __init__(self, cfg, ejecutor: Ejecutor, reloj: Reloj, *, cliente: Any | None = None, fallbacks: bool = True,
                 reloj_real: Reloj | None = None):
        self.ejecutor = ejecutor
        self.reloj = reloj                     # el del negocio (congelado en el modo demo)
        self.reloj_real = reloj_real or reloj  # el de hoy (mercado, tasa)
        self.modelo = cfg["modelo"]["nombre"]
        self.esfuerzo = cfg["modelo"]["esfuerzo"]
        self.fallbacks = fallbacks
        self.tools = definiciones(cfg["mercado"]["modo"])
        self.cliente = cliente if cliente is not None else anthropic.AsyncAnthropic()
        self._sistemas = {c: cargar_prompt(cfg, c) for c in ESTILOS}
        self._conversaciones: dict[str, _Conversacion] = {}
        self._bloqueos: dict[str, asyncio.Lock] = {}

    def _conversacion(self, clave: str) -> _Conversacion:
        c = self._conversaciones.get(clave)
        if c is None or c.turnos >= MAX_TURNOS or time.monotonic() - c.ultima > INACTIVIDAD_S:
            c = self._conversaciones[clave] = _Conversacion()
        return c

    def olvidar(self, clave: str) -> None:
        self._conversaciones.pop(clave, None)

    async def _llamar(self, sistema: str, mensajes: list[dict]):
        kwargs: dict[str, Any] = dict(
            model=self.modelo, max_tokens=16000,
            system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
            tools=self.tools, messages=mensajes, output_config={"effort": self.esfuerzo})
        if self.fallbacks:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        return await self.cliente.beta.messages.create(**kwargs)

    async def responder(self, clave: str, texto: str, canal: str = "telegram", *, conservar: bool = True) -> Respuesta:
        """Responde a un mensaje. `clave` identifica la conversación (chat de Telegram, sesión de consola…)."""
        async with self._bloqueos.setdefault(clave, asyncio.Lock()):
            return await self._responder(clave, texto, canal, conservar)

    async def _responder(self, clave: str, texto: str, canal: str, conservar: bool) -> Respuesta:
        inicio = time.monotonic()
        conv = self._conversacion(clave) if conservar else _Conversacion()
        mensajes = conv.mensajes
        mensajes.append({"role": "user", "content": f"{self.marca()}\n{texto}"})
        resp = Respuesta(texto="")
        tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        try:
            for _ in range(MAX_ITERACIONES):
                r = await self._llamar(self._sistemas.get(canal, self._sistemas["cli"]), mensajes)
                u = r.usage
                tokens["input"] += u.input_tokens or 0
                tokens["output"] += u.output_tokens or 0
                tokens["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
                tokens["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
                mensajes.append({"role": "assistant", "content": r.content})
                if r.stop_reason == "refusal":
                    resp.texto, resp.estado = SIN_RESPUESTA, "rechazado"
                    break
                if r.stop_reason == "pause_turn":
                    continue
                if r.stop_reason == "tool_use":
                    llamadas = [b for b in r.content if getattr(b, "type", None) == "tool_use"]
                    resultados = await asyncio.gather(*(self.ejecutor.ejecutar(b.name, b.input) for b in llamadas))
                    bloques = []
                    for b, (res, traza) in zip(llamadas, resultados):
                        resp.herramientas.append(traza)
                        contenido = json.dumps(res, ensure_ascii=False, default=str)
                        if len(contenido) > MAX_RESULTADO:
                            contenido = json.dumps({"status": "partial", "aviso": "resultado recortado",
                                                    "contenido": contenido[:MAX_RESULTADO]}, ensure_ascii=False)
                        bloques.append({"type": "tool_result", "tool_use_id": b.id, "content": contenido,
                                        "is_error": res.get("status") == "error"})
                    mensajes.append({"role": "user", "content": bloques})
                    continue
                resp.texto = _texto(r.content) or SIN_RESPUESTA          # end_turn, max_tokens, stop_sequence
                break
            else:
                resp.texto, resp.estado, resp.error = SIN_RESPUESTA, "error", "demasiadas iteraciones"
        except anthropic.APIError as e:
            log.exception("Error de la API de Claude")
            resp.texto, resp.estado, resp.error = SIN_RESPUESTA, "error", type(e).__name__
        else:
            conv.turnos += 1
            conv.ultima = time.monotonic()
        if resp.estado != "ok" and conservar:
            self.olvidar(clave)               # no dejar un historial a medias
        resp.texto, bloqueado = filtrar(resp.texto)
        if bloqueado:
            resp.estado, resp.error = "bloqueado", "filtro de salida"
        resp.tokens = tokens
        resp.latencia_ms = int((time.monotonic() - inicio) * 1000)
        return resp
