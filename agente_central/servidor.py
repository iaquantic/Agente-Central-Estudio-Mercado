"""Servidor web del Agente Central: panel HTML, datos del panel en JSON y preguntas por API.

Si `PANEL_TOKEN` está configurado, todas las rutas salvo /salud lo exigen (cabecera `Authorization: Bearer <token>`
o parámetro `?t=<token>`, que el bot incluye en el enlace que envía al dueño).
"""
from __future__ import annotations

import hmac

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=2, max_length=2000)
    conversacion: str | None = Field(default=None, max_length=80)


def crear_app(servicio) -> FastAPI:
    app = FastAPI(title="Agente Central · Estudio de mercado", version=__version__, docs_url=None, redoc_url=None)
    token = servicio.cfg.panel_token

    def autorizar(authorization: str | None, t: str | None) -> None:
        if not token:
            return
        dado = t or (authorization[7:] if authorization and authorization.startswith("Bearer ") else "")
        if not hmac.compare_digest(dado.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="Token no válido.")

    @app.get("/salud")
    async def salud():
        return {"estado": "ok", "empresa": servicio.cfg.empresa["id"], "version": __version__}

    @app.get("/panel", response_class=HTMLResponse)
    async def panel(t: str | None = Query(default=None), authorization: str | None = Header(default=None)):
        autorizar(authorization, t)
        return HTMLResponse(await servicio.panel_html(), headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    @app.get("/api/panel")
    async def panel_json(t: str | None = Query(default=None), authorization: str | None = Header(default=None)):
        autorizar(authorization, t)
        return JSONResponse(await servicio.panel())

    @app.post("/api/panel/actualizar")
    async def actualizar(t: str | None = Query(default=None), authorization: str | None = Header(default=None)):
        autorizar(authorization, t)
        p = await servicio.panel(forzar=True)
        return {"generado": p["meta"]["generado"], "propuestas": len(p["propuestas"]), "avisos": p["calidad"]["avisos"]}

    @app.post("/api/preguntar")
    async def preguntar(cuerpo: Pregunta, t: str | None = Query(default=None), authorization: str | None = Header(default=None)):
        autorizar(authorization, t)
        r = await servicio.orquestador.responder(f"api:{cuerpo.conversacion or 'unica'}", cuerpo.pregunta, canal="cli",
                                                 conservar=bool(cuerpo.conversacion))
        await servicio.registro.interaccion(canal="api", usuario_id="api", entrada=cuerpo.pregunta, respuesta=r.texto,
                                            estado=r.estado, herramientas=r.herramientas, latencia_ms=r.latencia_ms,
                                            tokens=r.tokens, error=r.error)
        return {"respuesta": r.texto, "estado": r.estado, "herramientas": [h["name"] for h in r.herramientas]}

    return app
