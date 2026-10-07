"""Tasa USD→CUP del día (elTOQUE) y formato de importes en la moneda principal con su equivalente.

Los subagentes trabajan en USD (moneda de referencia del negocio). Para el dueño, los importes se muestran en la
moneda principal del perfil (`panel.moneda_principal`, por defecto CUP) convertidos con la tasa informal de hoy, y
al lado su valor en la otra moneda: «54 300 CUP (72,50 USD)».
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import httpx

from .tiempo import Reloj

log = logging.getLogger(__name__)
API_ELTOQUE = "https://tasas.eltoque.com/v1/trmi"
REINTENTO_S = 15 * 60         # si elTOQUE falla, se reintenta como mucho cada 15 min
VIGENCIA_S = 3600             # la tasa se renueva cada hora (la web de elTOQUE la actualiza durante el día)

Dinero = Callable[..., str]


def _num(x: float, d: int) -> str:
    """Formato español: miles con espacio y coma decimal (1 234,50)."""
    return f"{x:,.{d}f}".replace(",", "\u202f").replace(".", ",")      # espacio fino que no se corta


def formateador(usd_cup: float | None, principal: str = "CUP") -> Dinero:
    """Devuelve dinero(usd, d=2) → texto. Sin tasa, solo USD."""
    def dinero(usd: float | None, d: int = 2) -> str:
        if usd is None:
            return "—"
        if not usd_cup:
            return f"{_num(usd, d)}\u00a0USD"
        cup = f"{_num(usd * usd_cup, 0)}\u00a0CUP"
        dolares = f"{_num(usd, d)}\u00a0USD"
        return f"{cup} ({dolares})" if principal == "CUP" else f"{dolares} ({cup})"
    return dinero


def fx_de(tasa_dia: dict | None) -> dict | None:
    """Tasa del día en el formato de los subagentes ({date, usd_cup, source})."""
    if not tasa_dia or not tasa_dia.get("usd_cup"):
        return None
    return {"date": tasa_dia.get("fecha"), "usd_cup": tasa_dia["usd_cup"], "source": tasa_dia.get("fuente")}


def dinero_de_panel(panel: dict) -> Dinero:
    meta = panel.get("meta") or {}
    return formateador((meta.get("tasa_dia") or {}).get("usd_cup"), meta.get("moneda_principal") or "CUP")


async def tasa_eltoque(reloj: Reloj, clave: str, cliente: httpx.AsyncClient | None = None) -> dict | None:
    """TRMI de las últimas 24 h (la más reciente publicada). None si no hay respuesta válida."""
    hasta = reloj.ahora()
    desde = hasta - timedelta(hours=24)
    fmt = "%Y-%m-%d %H:%M:%S"
    params = {"date_from": desde.strftime(fmt), "date_to": hasta.strftime(fmt)}
    propio = cliente is None
    cliente = cliente or httpx.AsyncClient(timeout=20)
    try:
        r = await cliente.get(API_ELTOQUE, params=params, headers={"Authorization": f"Bearer {clave}"})
        r.raise_for_status()
        d = r.json()
        usd = (d.get("tasas") or {}).get("USD")
        if not isinstance(usd, (int, float)) or usd <= 0:
            return None
        hora = f"{int(d.get('hour') or 0):02d}:{int(d.get('minutes') or 0):02d}"
        return {"usd_cup": float(usd), "fecha": str(d.get("date") or hasta.date().isoformat())[:10], "hora": hora,
                "fuente": "elTOQUE"}
    except (httpx.HTTPError, ValueError) as e:
        log.warning("No se pudo obtener la tasa de elTOQUE: %s", type(e).__name__)
        return None
    finally:
        if propio:
            await cliente.aclose()


class TasaDia:
    """Tasa USD→CUP de elTOQUE (ELTOQUE_API_KEY), guardada en disco y renovada cada hora; si no hay clave o la API
    no responde, la última del día y, si no, la del Agente Interno (con aviso)."""

    def __init__(self, reloj: Reloj, archivo: Path, clave: str | None = None, cliente: httpx.AsyncClient | None = None):
        self.reloj = reloj
        self.archivo = archivo
        self.clave = clave if clave is not None else os.environ.get("ELTOQUE_API_KEY")   # "" = sin elTOQUE
        self.cliente = cliente
        self._ultimo_fallo: datetime | None = None

    def _guardada(self) -> dict | None:
        """La guardada hoy (o None)."""
        try:
            t = json.loads(self.archivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return t if t.get("dia") == self.reloj.hoy().isoformat() else None

    def _vigente(self, t: dict) -> bool:
        try:
            return (self.reloj.ahora() - datetime.fromisoformat(t["obtenida"])).total_seconds() < VIGENCIA_S
        except (KeyError, TypeError, ValueError):
            return False

    async def obtener(self, respaldo: dict | None = None) -> dict | None:
        """`respaldo`: tasa del Agente Interno ({date, usd_cup, source}) para cuando elTOQUE no esté disponible."""
        guardada = self._guardada()
        if guardada and self._vigente(guardada):
            return guardada
        ahora = self.reloj.ahora()
        if self.clave and (self._ultimo_fallo is None or (ahora - self._ultimo_fallo).total_seconds() > REINTENTO_S):
            t = await tasa_eltoque(self.reloj, self.clave, self.cliente)
            if t:
                t.update(dia=self.reloj.hoy().isoformat(), obtenida=ahora.isoformat(timespec="seconds"))
                self.archivo.parent.mkdir(parents=True, exist_ok=True)
                self.archivo.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
                return t
            self._ultimo_fallo = ahora
        if guardada:                                       # elTOQUE no responde ahora: la última de hoy
            return guardada
        if respaldo and respaldo.get("usd_cup"):
            return {"usd_cup": float(respaldo["usd_cup"]), "fecha": str(respaldo.get("date") or "")[:10], "hora": None,
                    "fuente": f"{respaldo.get('source') or 'elTOQUE'} (vía Agente Interno)",
                    "aviso": ("Falta ELTOQUE_API_KEY: se usa la última tasa del Agente Interno." if not self.clave
                              else "elTOQUE no responde: se usa la última tasa del Agente Interno.")}
        return None
