"""Filtro de salida y formato para Telegram (HTML restringido a <b> e <i>)."""
from __future__ import annotations

import html
import re

RESPUESTA_NEUTRA = "Ahora mismo no puedo darte esa información. Pregúntame de otra forma o inténtalo en unos minutos."

_PATRONES = [
    re.compile(r"\b(select|insert|update|delete|drop|alter)\b[\s\S]{0,200}?\b(from|into|table|set)\b", re.I),
    re.compile(r"postgres(ql)?://", re.I),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),                     # token de bot de Telegram
    re.compile(r"\b(traceback|stack trace|httpx\.|anthropic\.)", re.I),
    re.compile(r"\b(get_business_summary|get_sales_summary|get_top_products|get_inventory_status|get_margin_analysis|"
               r"herramienta_negocio|informe_negocio|analizar_mercado|comparar_producto|resumen_panel)\b"),
    re.compile(r"<(mision|enrutado|reglas_de_evidencia|limites|estilo|productos_vigilados)>", re.I),
]


def es_seguro(texto: str) -> bool:
    return not any(p.search(texto or "") for p in _PATRONES)


def filtrar(texto: str) -> tuple[str, bool]:
    """Devuelve (texto a enviar, bloqueado)."""
    return (texto, False) if es_seguro(texto) else (RESPUESTA_NEUTRA, True)


_ETIQUETA = re.compile(r"&lt;(/?)(b|i)&gt;")


def html_telegram(texto: str) -> str:
    """Escapa todo y vuelve a permitir solo <b> e <i>."""
    return _ETIQUETA.sub(r"<\1\2>", html.escape(texto or "", quote=False))


def texto_plano(texto: str) -> str:
    return re.sub(r"</?(b|i)>", "", texto or "")


def trocear(texto: str, limite: int = 4000) -> list[str]:
    """Telegram admite 4096 caracteres por mensaje: corta por párrafos."""
    if len(texto) <= limite:
        return [texto]
    partes, actual = [], ""
    for parrafo in texto.split("\n"):
        while len(parrafo) > limite:                                    # párrafo gigante: corte duro
            partes.append(parrafo[:limite])
            parrafo = parrafo[limite:]
        if len(actual) + len(parrafo) + 1 > limite and actual:
            partes.append(actual)
            actual = ""
        actual = f"{actual}\n{parrafo}" if actual else parrafo
    if actual:
        partes.append(actual)
    return partes


def usd(v: float | None, d: int = 2) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return f"{s} USD"


EMOJI_PRIORIDAD = {"alta": "🔴", "media": "🟠", "baja": "🟡", "info": "⚪"}


def propuestas_html(propuestas: list[dict], limite: int = 5, dinero=None) -> str:
    """Lista de propuestas en HTML de Telegram (sin modelo: para el resumen diario y /oportunidades)."""
    if not propuestas:
        return "No hay decisiones pendientes para los productos vigilados."
    lineas = []
    for p in propuestas[:limite]:
        imp = f" · <i>≈ {(dinero or usd)(p['impacto_usd'], 0)}</i>" if p.get("impacto_usd") else ""
        lineas.append(f"{EMOJI_PRIORIDAD.get(p['prioridad'], '•')} <b>{html.escape(p['nombre'])}</b>: "
                      f"{html.escape(p['titulo'])}{imp}\n{html.escape(p['detalle'])}")
    if len(propuestas) > limite:
        lineas.append(f"… y {len(propuestas) - limite} más en el panel.")
    return "\n\n".join(lineas)
