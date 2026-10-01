# Despliegue

El sistema corre en un VPS **fuera de Cuba** (la API de Claude no está disponible desde Cuba). El dueño lo usa desde Cuba
por Telegram y por el navegador.

## Con Docker (recomendado)

```bash
git clone https://github.com/iaquantic/Agente-Central-Estudio-Mercado
git clone https://github.com/iaquantic/Agente-Controlador-de-Negocio
git clone https://github.com/iaquantic/Agente-Controlador-de-Mercado
cd Agente-Central-Estudio-Mercado
cp .env.example .env                          # INTERNO_MODO=api, tokens y claves
cp config/empresa.plantilla.yaml config/empresa.yaml && $EDITOR config/empresa.yaml   # rellenar lo marcado CAMBIAR
#   en .env: EMPRESA_CONFIG=config/empresa.yaml
#   en ../Agente-Controlador-de-Negocio/.env: DB_URL_AGENTE, ORQUESTADOR_TOKEN (el mismo), ANTHROPIC_API_KEY…
docker compose --profile completo up -d --build
docker compose exec agente-central python -m agente_central comprobar
```

- El Agente Interno queda en la red interna de Docker (`http://agente-interno:8080`), sin puerto publicado.
- El dueño recibe el panel **como archivo HTML por Telegram** (`/panel` y con el resumen diario): no hace falta
  publicar ninguna web. El puerto `127.0.0.1:8090` solo sirve la API local (`/api/panel`, `/api/preguntar`). Si algún
  día se quiere un enlace, publícalo detrás de un proxy HTTPS con `PANEL_TOKEN` y `PANEL_URL_PUBLICA`.
- Datos persistentes en el volumen `datos-central`: último panel, caché de análisis de mercado, registro de
  interacciones (`registro.jsonl`), alertas ya enviadas y el histórico de capturas de Revolico y Cuballama
  (`CONTROLADOR_CACHE_DIR`). Ese histórico es lo que permite calcular tendencias: no borres el volumen.
- Las webs se consultan como mucho una vez cada `mercado.cache_horas` (12 h por defecto) por producto, con 5 s entre
  peticiones y respetando robots.txt. El refresco horario del panel reutiliza esos análisis.
- Cubamax necesita Chromium (`pip install controlador-mercado[navegador]` + `playwright install chromium` en la imagen);
  no está incluido por defecto para mantener la imagen ligera.

Solo el Agente Central en modo demo (negocio de prueba, mercado real; sin BD ni claves):

```bash
docker compose up -d --build agente-central      # http://127.0.0.1:8090/panel
```

## Sin Docker

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ../Agente-Controlador-de-Mercado -r requirements.txt
python -m agente_central servir
```
Como servicio de systemd: `ExecStart=/ruta/.venv/bin/python -m agente_central servir`, `WorkingDirectory` en el
repositorio, `EnvironmentFile` apuntando a `.env` y `Restart=always`.

## Operación

| Tarea | Cómo |
|---|---|
| Ver estado | `GET /salud`; `python -m agente_central comprobar` |
| Regenerar el panel ahora | `/panel` en Telegram o `POST /api/panel/actualizar?t=<PANEL_TOKEN>` |
| Revisar conversaciones | `datos/<empresa>/registro.jsonl` (entrada, herramientas usadas, agentes consultados, tokens, latencia) |
| Cambiar umbrales o productos | Editar `config/empresa.yaml` y reiniciar el servicio |
| Integrar con otras herramientas (n8n, Odoo…) | `POST /api/preguntar {"pregunta": "..."}` y `GET /api/panel` con `Authorization: Bearer <PANEL_TOKEN>` |

## Seguridad
- Solo los IDs de `TELEGRAM_USUARIOS` pueden usar el bot; los grupos se abandonan y los intentos se registran.
- Límite de consultas por hora y usuario (`telegram.limite_consultas_hora`).
- Filtro de salida: nunca se envían SQL, claves, tokens ni nombres internos de herramientas.
- Todo es de solo lectura: el Agente Interno usa un rol sin permisos de escritura y el Externo solo lee fuentes públicas
  autorizadas, respetando robots.txt y sin evadir bloqueos.
- Los textos de los anuncios se tratan como datos, nunca como instrucciones.
