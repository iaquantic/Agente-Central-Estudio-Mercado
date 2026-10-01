"""Reloj con hora congelable (modo demo) y zona horaria de la empresa."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


class Reloj:
    def __init__(self, zona: str = "America/Havana", ahora_fija: datetime | None = None):
        self.zona = ZoneInfo(zona)
        self.ahora_fija = ahora_fija.astimezone(self.zona) if ahora_fija else None

    def ahora(self) -> datetime:
        return self.ahora_fija or datetime.now(self.zona)

    def hoy(self) -> date:
        return self.ahora().date()

    def marca(self) -> str:
        """Fecha y hora para incluir en cada mensaje al modelo (no en el system prompt, para poder cachearlo)."""
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        a = self.ahora()
        return f"[Ahora: {dias[a.weekday()]} {a:%Y-%m-%d %H:%M} ({self.zona.key})]"
