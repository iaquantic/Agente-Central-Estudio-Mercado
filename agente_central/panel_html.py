"""Render del panel en HTML con la identidad de marca del perfil (por defecto, formato Quantic).

El HTML es autocontenido (estilos, script y datos incrustados): se puede servir desde el Agente Central, enviar por
Telegram como archivo o abrir sin conexión. Solo las tipografías se cargan de Google Fonts, con alternativas locales.
"""
from __future__ import annotations

import base64
import html
import json
import mimetypes
from pathlib import Path
from urllib.parse import quote_plus

from .config import ruta

PLANTILLA = Path(__file__).resolve().parent / "plantillas" / "panel.html"


def _aclarar(hex_color: str, t: float) -> str:
    """Mezcla un color con blanco (t=0 → igual, t=1 → blanco). Para la variante del modo oscuro."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{round(c + (255 - c) * t):02X}" for c in (r, g, b))


def _logo(archivo: str | None, alt: str) -> str:
    p = ruta(archivo)
    if not p or not p.exists():
        return ""
    if p.suffix.lower() == ".svg":
        svg = p.read_text(encoding="utf-8")
        return svg[svg.find("<svg"):] if "<svg" in svg else ""
    tipo = mimetypes.guess_type(p.name)[0] or "image/png"
    datos = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:{tipo};base64,{datos}" alt="{html.escape(alt)}">'


def renderizar(panel: dict, marca: dict) -> str:
    colores, fuentes = marca["colores"], marca["tipografias"]
    panel = {**panel, "meta": {**panel["meta"], "proveedor": marca.get("proveedor") or panel["meta"].get("proveedor")}}
    datos = json.dumps(panel, ensure_ascii=False, default=str).replace("</", "<\\/")
    logo_cliente = _logo(marca.get("logo_cliente"), panel["meta"]["empresa"]["nombre"])
    sustituciones = {
        "{{TITULO}}": html.escape(f"{panel['meta']['empresa']['nombre']} · {panel['meta']['titulo']}"),
        "{{COLOR_PRIMARIO}}": colores["primario"],
        "{{COLOR_ACENTO}}": colores["acento"],
        "{{COLOR_ACENTO_FUERTE}}": colores["acento_fuerte"],
        "{{COLOR_SUAVE}}": colores["suave"],
        "{{SERIE_NEGOCIO}}": colores["acento_fuerte"],
        "{{SERIE_NEGOCIO_OSCURO}}": _aclarar(colores["acento_fuerte"], 0.3),
        "{{FUENTE_TITULOS}}": fuentes["titulos"],
        "{{FUENTE_TEXTO}}": fuentes["texto"],
        "{{FUENTE_TITULOS_URL}}": quote_plus(fuentes["titulos"]),
        "{{FUENTE_TEXTO_URL}}": quote_plus(fuentes["texto"]),
        "{{LOGO}}": _logo(marca.get("logo"), marca.get("proveedor", "")),
        "{{LOGO_CLIENTE}}": f'<div class="logo">{logo_cliente}</div>' if logo_cliente else "",
        "{{DATOS}}": datos,
    }
    out = PLANTILLA.read_text(encoding="utf-8")
    for k, v in sustituciones.items():
        out = out.replace(k, v)
    return out
