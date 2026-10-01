"""Registro de interacciones y eventos en JSONL (una línea por evento). Nunca guarda secretos."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


class Registro:
    def __init__(self, archivo: Path):
        self.archivo = archivo
        self._lock = asyncio.Lock()

    async def _escribir(self, fila: dict) -> None:
        fila = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fila}
        async with self._lock:
            self.archivo.parent.mkdir(parents=True, exist_ok=True)
            with self.archivo.open("a", encoding="utf-8") as f:
                f.write(json.dumps(fila, ensure_ascii=False, default=str) + "\n")

    async def interaccion(self, *, canal: str, usuario_id, entrada: str | None, respuesta: str | None, estado: str,
                          herramientas: list[dict] | None = None, latencia_ms: int | None = None,
                          tokens: dict | None = None, error: str | None = None) -> None:
        await self._escribir({"tipo": "interaccion", "canal": canal, "usuario": usuario_id, "entrada": (entrada or "")[:2000],
                              "respuesta": (respuesta or "")[:4000], "estado": estado, "herramientas": herramientas or [],
                              "latencia_ms": latencia_ms, "tokens": tokens, "error": error})

    async def acceso_denegado(self, usuario_id, tipo_chat, texto: str | None) -> None:
        await self._escribir({"tipo": "acceso_denegado", "usuario": usuario_id, "chat": tipo_chat, "entrada": (texto or "")[:200]})

    async def evento(self, nombre: str, datos: dict) -> None:
        await self._escribir({"tipo": nombre, **datos})
