"""Línea de comandos del Agente Central.

    python -m agente_central comprobar                 # valida el perfil y la conexión con los subagentes
    python -m agente_central panel --salida panel.html  # genera el panel (sin modelo: no necesita clave de Claude)
    python -m agente_central preguntar "¿Cómo se mueve el aceite en el mercado?"
    python -m agente_central servir                     # bot de Telegram + web del panel + tareas programadas

Opción común: --config config/empresa.yaml (o variable EMPRESA_CONFIG). Por defecto, el perfil de demostración.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import Config, ErrorConfig


def _servicio(args):
    from .servicio import Servicio
    return Servicio(Config.cargar(args.config))


async def cmd_comprobar(args) -> int:
    from .interno import datos
    s = _servicio(args)
    cfg = s.cfg
    print(f"Perfil: {cfg.origen}\nEmpresa: {cfg.nombre} ({cfg.empresa['id']}) · zona {cfg.empresa['zona_horaria']}")
    print(f"Agente Interno: modo {cfg['interno']['modo']}" + (f" · {cfg['interno']['url']}" if cfg["interno"]["modo"] == "api" else ""))
    ok = True
    r = await s.interno.herramienta("get_data_quality", {})
    if datos(r) is None:
        ok = False
        print(f"  ✗ sin respuesta: {(r.get('error') or {}).get('message') or r.get('summary')}")
    else:
        fx = (r.get("data_quality") or {}).get("fx") or {}
        print(f"  ✓ responde · último dato hace {datos(r).get('freshness_minutes')} min · tasa {fx.get('usd_cup')} ({fx.get('source')})")
    print(f"Agente Externo: modo {cfg['mercado']['modo']}")
    for f in s.mercado.fuentes():
        print(f"  · fuente {f.get('source_id')}: {f.get('source_name') or ''}")
    for sku, p in cfg.vigilados.items():
        f = await s.interno.herramienta("get_product", {"sku": sku})
        nombre = (datos(f) or {}).get("name")
        print(f"  {'✓' if nombre else '✗'} {sku}: {nombre or 'no existe en el negocio'} → mercado «{p['mercado']['name']}»")
        ok = ok and bool(nombre)
    print(f"Telegram: {'configurado' if cfg.telegram_token else 'sin token'} · usuarios autorizados: {len(cfg.telegram_usuarios)}")
    print(f"Claude: {'ANTHROPIC_API_KEY presente' if os.environ.get('ANTHROPIC_API_KEY') else 'sin ANTHROPIC_API_KEY (puede haber perfil de ant)'} · modelo {cfg['modelo']['nombre']}")
    await s.cerrar()
    return 0 if ok else 1


async def cmd_panel(args) -> int:
    from .panel_html import renderizar
    s = _servicio(args)
    p = await s.panel(forzar_mercado=args.forzar)
    salida = Path(args.salida)
    if args.json:
        salida.write_text(json.dumps(p, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    else:
        salida.write_text(renderizar(p, s.cfg["marca"]), encoding="utf-8")
    print(f"Panel guardado en {salida} · {len(p['propuestas'])} propuestas · {len(p['productos'])} productos vigilados")
    for a in p["calidad"]["avisos"]:
        print(f"  aviso: {a}", file=sys.stderr)
    await s.cerrar()
    return 0


async def cmd_preguntar(args) -> int:
    s = _servicio(args)
    r = await s.orquestador.responder("cli", " ".join(args.pregunta), canal="cli", conservar=False)
    print(r.texto)
    if args.traza:
        print("\n— herramientas: " + ", ".join(f"{h['name']}→{h['destino']}" for h in r.herramientas), file=sys.stderr)
        print(f"— tokens: {r.tokens} · {r.latencia_ms} ms · estado {r.estado}", file=sys.stderr)
    await s.cerrar()
    return 0 if r.estado == "ok" else 2


async def cmd_servir(args) -> int:
    import uvicorn

    from .bot_telegram import BotTelegram
    from .servidor import crear_app
    s = _servicio(args)
    cfg = s.cfg
    if cfg.ahora_fija:
        logging.warning("MODO DEMO: la hora está congelada en %s", s.reloj.ahora().isoformat())
    servidor = uvicorn.Server(uvicorn.Config(crear_app(s), host=cfg.api_host, port=cfg.api_port, log_level="warning"))
    tarea_web = asyncio.create_task(servidor.serve())
    logging.info("Panel en http://%s:%s/panel", cfg.api_host, cfg.api_port)
    bot = None
    if cfg.telegram_token:
        bot = BotTelegram(s, cfg.telegram_token, cfg.telegram_usuarios)
        bot.programar()
        await bot.app.initialize()
        await bot.app.start()
        await bot.app.updater.start_polling(drop_pending_updates=True)
        logging.info("Bot de Telegram en marcha (%d usuarios autorizados)", len(cfg.telegram_usuarios))
    else:
        logging.warning("Sin TELEGRAM_BOT_TOKEN (o TELEGRAM_BOT_API): solo se sirve el panel web")
    try:
        await tarea_web
    finally:
        if bot:
            await bot.app.updater.stop()
            await bot.app.stop()
            await bot.app.shutdown()
        await s.cerrar()
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)           # no registrar URLs con el token del bot
    ap = argparse.ArgumentParser(prog="agente_central", description="Agente Central de estudio de mercado")
    ap.add_argument("--config", help="Perfil de empresa (YAML). Por defecto, EMPRESA_CONFIG o el de demostración.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("comprobar", help="Valida el perfil y la conexión con los subagentes.")
    p = sub.add_parser("panel", help="Genera el panel.")
    p.add_argument("--salida", default="panel.html")
    p.add_argument("--json", action="store_true", help="Guardar los datos del panel en JSON en lugar de HTML.")
    p.add_argument("--forzar", action="store_true", help="Ignorar la caché de análisis de mercado.")
    q = sub.add_parser("preguntar", help="Pregunta al Agente Central (usa Claude).")
    q.add_argument("pregunta", nargs="+")
    q.add_argument("--traza", action="store_true", help="Mostrar a qué agentes se consultó.")
    sub.add_parser("servir", help="Bot de Telegram + panel web + tareas programadas.")
    args = ap.parse_args(argv)
    try:
        return asyncio.run({"comprobar": cmd_comprobar, "panel": cmd_panel, "preguntar": cmd_preguntar,
                            "servir": cmd_servir}[args.cmd](args))
    except ErrorConfig as e:
        print(f"Error en el perfil de empresa: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
