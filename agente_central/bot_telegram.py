"""Bot de Telegram del Agente Central: punto único de contacto del dueño con el sistema.

- Texto libre → orquestador (Claude decide si pregunta al Agente Interno, al Externo o cruza ambos).
- Comandos rápidos sin modelo: /panel, /oportunidades, /negocio.
- Comandos guiados con modelo: /mercado <producto>, /producto <producto>.
- Mensajes proactivos: resumen diario y alertas urgentes (planificador.py).
"""
from __future__ import annotations

import html
import io
import logging
import time as _time
from collections import defaultdict, deque

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .formato import html_telegram, propuestas_html, texto_plano, trocear
from .planificador import Avisador, _hora

log = logging.getLogger(__name__)

NO_AUTORIZADO = "Este asistente es privado. No estás autorizado."
LIMITE = "Has llegado al límite de consultas por hora. Vuelve a intentarlo en unos minutos."


def bienvenida(nombre: str) -> str:
    return (f"👋 Hola, soy el asistente de <b>{html.escape(nombre)}</b>. Conozco tu negocio y sigo el mercado.\n\n"
            "Pregúntame con tus palabras, por ejemplo:\n"
            "• ¿Cómo se está moviendo el aceite en el mercado?\n"
            "• ¿Cómo va mi negocio de pollo?\n"
            "• ¿Estoy caro con los ventiladores? ¿Qué hago?\n\n"
            "O usa /ayuda para ver los comandos.")


AYUDA = ("<b>Comandos</b>\n"
         "/panel · panel con gráficos (negocio + mercado)\n"
         "/oportunidades · decisiones propuestas por prioridad\n"
         "/negocio · cómo va el negocio hoy\n"
         "/mercado &lt;producto&gt; · cómo se mueve un producto en el mercado\n"
         "/producto &lt;producto&gt; · tu producto frente al mercado y qué hacer\n"
         "/ayuda · esta ayuda\n\n"
         "<i>Solo consulto y propongo: no cambio precios, stock ni pedidos.</i>")

BOTONES_INICIO = {"📈 Panel": "panel", "💡 Oportunidades": "oportunidades", "🏪 Mi negocio hoy": "negocio"}


def teclado(botones: dict[str, str] | None) -> InlineKeyboardMarkup | None:
    if not botones:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in botones.items()]])


class LimiteUso:
    def __init__(self, por_hora: int):
        self.por_hora = por_hora
        self._uso: dict[int, deque] = defaultdict(deque)

    def permitir(self, usuario: int) -> bool:
        ahora = _time.monotonic()
        q = self._uso[usuario]
        while q and ahora - q[0] > 3600:
            q.popleft()
        if len(q) >= self.por_hora:
            return False
        q.append(ahora)
        return True


class BotTelegram:
    def __init__(self, servicio, token: str, usuarios: tuple[int, ...]):
        if not usuarios:
            raise ValueError("Configura TELEGRAM_USUARIOS (IDs numéricos autorizados).")
        self.s = servicio
        self.usuarios = set(usuarios)
        tg = servicio.cfg["telegram"]
        self.limite = LimiteUso(int(tg["limite_consultas_hora"]))
        self.avisador = Avisador(servicio, servicio.cfg.directorio_datos / "alertas_enviadas.json")
        self.app = Application.builder().token(token).build()
        self._handlers()

    # Autorización -------------------------------------------------------------------------------
    async def _autorizado(self, update: Update) -> bool:
        usuario, chat = update.effective_user, update.effective_chat
        if chat and chat.type != ChatType.PRIVATE:
            await self.s.registro.acceso_denegado(usuario.id if usuario else None, chat.type, None)
            try:
                await chat.leave()
            except Exception:
                pass
            return False
        if not usuario or usuario.id not in self.usuarios:
            msg = update.effective_message
            await self.s.registro.acceso_denegado(usuario.id if usuario else None, chat.type if chat else None,
                                                  msg.text if msg else None)
            if msg:
                await msg.reply_text(NO_AUTORIZADO)
            return False
        if not self.limite.permitir(usuario.id):
            await update.effective_message.reply_text(LIMITE)
            return False
        return True

    # Envío --------------------------------------------------------------------------------------
    async def _enviar(self, chat_id: int, texto_html: str, botones: dict | None = None) -> None:
        partes = trocear(texto_html)
        for i, parte in enumerate(partes):
            markup = teclado(botones) if i == len(partes) - 1 else None
            try:
                await self.app.bot.send_message(chat_id, parte, parse_mode=ParseMode.HTML, reply_markup=markup,
                                                disable_web_page_preview=True)
            except BadRequest:
                await self.app.bot.send_message(chat_id, texto_plano(parte), reply_markup=markup)

    async def enviar_a_todos(self, texto_html: str, botones: dict | None = None) -> None:
        for u in self.usuarios:
            try:
                await self._enviar(u, texto_html, botones)
            except Exception:
                log.exception("No se pudo enviar el mensaje a %s", u)

    def url_panel(self) -> str | None:
        base = self.s.cfg.panel_url_publica
        if not base:
            return None
        t = self.s.cfg.panel_token
        return f"{base}/panel" + (f"?t={t}" if t else "")

    async def _consultar(self, update: Update, pregunta: str, *, entrada: str | None = None) -> None:
        chat_id = update.effective_chat.id
        await self.app.bot.send_chat_action(chat_id, ChatAction.TYPING)
        r = await self.s.orquestador.responder(f"tg:{chat_id}", pregunta, canal="telegram")
        await self._enviar(chat_id, html_telegram(r.texto))
        await self.s.registro.interaccion(canal="telegram", usuario_id=update.effective_user.id, entrada=entrada or pregunta,
                                          respuesta=r.texto, estado=r.estado, herramientas=r.herramientas,
                                          latencia_ms=r.latencia_ms, tokens=r.tokens, error=r.error)

    # Comandos -----------------------------------------------------------------------------------
    def _handlers(self) -> None:
        a = self.app
        for nombre, f in (("start", self.cmd_start), ("ayuda", self.cmd_ayuda), ("help", self.cmd_ayuda),
                          ("panel", self.cmd_panel), ("oportunidades", self.cmd_oportunidades),
                          ("negocio", self.cmd_negocio), ("mercado", self.cmd_mercado), ("producto", self.cmd_producto)):
            a.add_handler(CommandHandler(nombre, f))
        a.add_handler(CallbackQueryHandler(self.boton))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.texto))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._autorizado(update):
            await update.effective_message.reply_text(bienvenida(self.s.cfg.nombre), parse_mode=ParseMode.HTML,
                                                      reply_markup=teclado(BOTONES_INICIO))

    async def cmd_ayuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._autorizado(update):
            await update.effective_message.reply_text(AYUDA, parse_mode=ParseMode.HTML)

    async def _documento_panel(self, chat_id: int) -> None:
        """Envía el panel como archivo HTML autocontenido (se abre en el navegador del móvil, también sin conexión)."""
        await self.app.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        p = await self.s.panel()
        contenido = await self.s.panel_html()
        nombre = f"panel_{self.s.cfg.empresa['id']}_{p['meta']['periodo']['hasta']}.html"
        url = self.url_panel()
        pie = f"\n\nTambién en: {html.escape(url)}" if url else ""
        await self.app.bot.send_document(chat_id, InputFile(io.BytesIO(contenido.encode("utf-8")), filename=nombre),
                                         caption=f"📈 Panel de {html.escape(self.s.cfg.nombre)}. Ábrelo con el navegador.{pie}",
                                         parse_mode=ParseMode.HTML)

    async def _panel(self, chat_id: int) -> None:
        await self._documento_panel(chat_id)
        p = await self.s.panel()
        await self._enviar(chat_id, "💡 <b>Decisiones propuestas</b>\n\n" + propuestas_html(p["propuestas"], limite=3))

    async def cmd_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._autorizado(update):
            await self._panel(update.effective_chat.id)
            await self.s.registro.interaccion(canal="telegram", usuario_id=update.effective_user.id, entrada="/panel",
                                              respuesta="panel enviado", estado="ok")

    async def _oportunidades(self, chat_id: int) -> None:
        p = await self.s.panel()
        await self._enviar(chat_id, "💡 <b>Decisiones propuestas</b>\n\n" + propuestas_html(p["propuestas"], limite=6))

    async def cmd_oportunidades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._autorizado(update):
            await self._oportunidades(update.effective_chat.id)

    async def _negocio(self, chat_id: int) -> None:
        r = await self.s.interno.informe("estado_general")
        if r.get("status") == "error":
            await self._enviar(chat_id, "Ahora mismo no puedo consultar los datos del negocio. Inténtalo en unos minutos.")
            return
        lineas = [f"🏪 <b>{html.escape(self.s.cfg.nombre)} hoy</b>", html.escape(r.get("summary") or "")]
        urg = [a for a in r.get("alerts", []) if a.get("priority") == "urgent"][:4]
        if urg:
            lineas.append("\n<b>Urgente</b>\n" + "\n".join(f"• {html.escape(a['title'])}" for a in urg))
        await self._enviar(chat_id, "\n".join(lineas))

    async def cmd_negocio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._autorizado(update):
            await self._negocio(update.effective_chat.id)

    async def cmd_mercado(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._autorizado(update):
            return
        prod = " ".join(context.args or []).strip()[:80]
        if not prod:
            await update.effective_message.reply_text("¿Qué producto? Por ejemplo: /mercado aceite de girasol 1 L")
            return
        await self._consultar(update, f"¿Cómo se está moviendo «{prod}» en el mercado? Precios, tendencia y oferta.",
                              entrada=f"/mercado {prod}")

    async def cmd_producto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._autorizado(update):
            return
        prod = " ".join(context.args or []).strip()[:80]
        if not prod:
            await update.effective_message.reply_text("¿Qué producto? Por ejemplo: /producto pollo")
            return
        await self._consultar(update, f"¿Cómo va mi negocio de «{prod}» comparado con el mercado y qué debería hacer?",
                              entrada=f"/producto {prod}")

    async def boton(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        await q.answer()
        if not await self._autorizado(update):
            return
        chat_id = update.effective_chat.id
        acciones = {"panel": self._panel, "oportunidades": self._oportunidades, "negocio": self._negocio}
        if q.data in acciones:
            await acciones[q.data](chat_id)

    async def texto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._autorizado(update):
            await self._consultar(update, update.effective_message.text[:2000])

    # Tareas programadas --------------------------------------------------------------------------
    async def _job_resumen(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.enviar_a_todos(await self.avisador.resumen_diario(self.url_panel()), BOTONES_INICIO)
            for u in self.usuarios:                     # el panel del día, como archivo HTML
                await self._documento_panel(u)
        except Exception:
            log.exception("Fallo del resumen diario")

    async def _job_alertas(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            for a in await self.avisador.alertas_pendientes():
                await self.enviar_a_todos(self.avisador.texto_alerta(a))
        except Exception:
            log.exception("Fallo de la revisión de alertas")

    async def _job_panel(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.s.panel(forzar=True)
        except Exception:
            log.exception("Fallo al refrescar el panel")

    def programar(self) -> None:
        tg, jq, zona = self.s.cfg["telegram"], self.app.job_queue, self.s.reloj.zona
        if tg.get("resumen_diario"):
            jq.run_daily(self._job_resumen, time=_hora(tg["resumen_diario"]).replace(tzinfo=zona), name="resumen")
        if tg.get("alertas_urgentes"):
            jq.run_repeating(self._job_alertas, interval=int(tg["revisar_alertas_cada_min"]) * 60, first=60, name="alertas")
        jq.run_repeating(self._job_panel, interval=int(self.s.cfg["panel"]["refresco_minutos"]) * 60, first=5, name="panel")
