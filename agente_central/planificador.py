"""Mensajes proactivos: resumen diario (negocio + mercado) y alertas urgentes del negocio.

Las alertas se envían con plantilla fija (sin modelo), para que lleguen al instante y siempre igual:
anti-repetición de 24 h por `alert_key`, máximo diario y horas de silencio configurables por empresa.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, time, timedelta
from pathlib import Path

from .formato import propuestas_html
from .moneda import dinero_de_panel
from .interno import datos


def _hora(t: str) -> time:
    h, m = str(t).split(":")
    return time(int(h), int(m))


def en_silencio(ahora: datetime, silencio: list[str] | None) -> bool:
    if not silencio:
        return False
    desde, hasta = _hora(silencio[0]), _hora(silencio[1])
    t = ahora.time()
    return (t >= desde or t < hasta) if desde > hasta else (desde <= t < hasta)


class Avisador:
    def __init__(self, servicio, archivo_estado: Path):
        self.s = servicio
        self.archivo = archivo_estado
        tg = servicio.cfg["telegram"]
        self.max_dia = int(tg["max_alertas_dia"])
        self.silencio = tg.get("silencio")

    def _estado(self) -> dict:
        try:
            return json.loads(self.archivo.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"enviadas": {}}

    def _guardar(self, estado: dict) -> None:
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        self.archivo.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")

    async def alertas_pendientes(self) -> list[dict]:
        """Alertas urgentes nuevas que tocan enviar ahora (y las marca como enviadas)."""
        ahora = self.s.reloj.ahora()
        if en_silencio(ahora, self.silencio):
            return []
        d = datos(await self.s.interno.herramienta("get_alerts", {"min_priority": "urgent"})) or {}
        estado = self._estado()
        limite = ahora - timedelta(hours=24)
        enviadas = {k: v for k, v in estado["enviadas"].items() if datetime.fromisoformat(v) > limite}
        hoy = [v for v in enviadas.values() if datetime.fromisoformat(v).date() == ahora.date()]
        nuevas = []
        for a in d.get("alerts", []):
            if a.get("priority") != "urgent" or a["alert_key"] in enviadas:
                continue
            if len(hoy) + len(nuevas) >= self.max_dia:
                break
            nuevas.append(a)
            enviadas[a["alert_key"]] = ahora.isoformat()
        self._guardar({"enviadas": enviadas})
        return nuevas

    @staticmethod
    def texto_alerta(a: dict) -> str:
        return f"🚨 <b>{html.escape(a['title'])}</b>\n{html.escape(a.get('detail') or '')}"

    async def resumen_diario(self, url_panel: str | None) -> str:
        p = await self.s.panel()
        t = (p.get("ventas_mes") or {}).get("totales") or {}
        c = ((p.get("ventas_mes") or {}).get("comparacion") or {}).get("delta_pct") or {}
        r = p.get("resumen") or {}
        hoy = r.get("today") or {}
        usd = dinero_de_panel(p)
        lineas = [f"📊 <b>Resumen de {html.escape(self.s.cfg.nombre)}</b> · <i>{p['meta']['periodo']['hasta']}</i>"]
        td = p["meta"].get("tasa_dia") or {}
        if td.get("usd_cup"):
            dia = "/".join(reversed(str(td.get("fecha") or "")[:10].split("-")))
            lineas.append(f"💱 1 USD = {td['usd_cup']:,.0f} CUP".replace(",", "\u202f")
                          + f" ({html.escape(str(td.get('fuente') or ''))}" + (f", {dia})" if dia else ")"))
        if hoy:
            lineas.append(f"• Hoy: <b>{usd(hoy.get('net_usd'))}</b> en {hoy.get('tickets', 0)} ventas"
                          + (f" ({r['vs_expected_pct']:+.1f} % frente a lo esperado)".replace(".", ",") if r.get("vs_expected_pct") is not None else ""))
        if t:
            lineas.append(f"• Mes: <b>{usd(t.get('net_usd'), 0)}</b>"
                          + (f" ({c['net_usd']:+.1f} % frente al periodo anterior)".replace(".", ",") if c.get("net_usd") is not None else "")
                          + (f" · margen {str(t.get('gross_margin_pct')).replace('.', ',')} %" if t.get("gross_margin_pct") is not None else ""))
        lineas.append("\n💡 <b>Decisiones propuestas</b>\n" + propuestas_html(p.get("propuestas", []), limite=4, dinero=usd))
        if url_panel:
            lineas.append(f"\nPanel completo: {html.escape(url_panel)}")
        return "\n".join(lineas)
