# Agente Central · Estudio de Mercado (MVP)

Orquestador del sistema multiagente de estudio de mercado de **Quantic Data**. Recibe información de dos subagentes,
la cruza y la convierte en decisiones para el dueño del negocio:

| Agente | Repositorio | Qué aporta |
|---|---|---|
| **Agente Central** (este) | `Agente-Central-Estudio-Mercado` | Cruza negocio y mercado, propone decisiones, genera el **panel** y atiende al dueño por **Telegram**. |
| Agente Interno | `Agente-Controlador-de-Negocio` | Datos reales del negocio: ventas, stock, márgenes, alertas (API `POST /v1/consulta`). |
| Agente Externo | `Agente-Controlador-de-Mercado` | Mercado cubano: precios, oferta, tendencias y señales en fuentes públicas (librería Python). |

```
                 Dueño ── Telegram ──┐            ┌── Navegador (panel)
                                     ▼            ▼
                    ┌─────────────── AGENTE CENTRAL ───────────────┐
                    │ Orquestador (Claude + herramientas)          │
                    │ Cruce negocio × mercado (reglas deterministas)│
                    │ Panel Quantic · avisos programados           │
                    └──────┬─────────────────────────────┬─────────┘
            POST /v1/consulta (token)            librería controlador-mercado
                           ▼                             ▼
                  AGENTE INTERNO                  AGENTE EXTERNO
                  BD del negocio (Supabase)       Revolico, Cuballama, Cubamax… + tasa elTOQUE
```

Detalle en [docs/arquitectura.md](docs/arquitectura.md).

## Qué hace

**Panel** ([ejemplo: negocio de prueba con mercado real](docs/panel_demo.html)), con la identidad visual de Quantic y personalizable por empresa:
- Indicadores del mes: ventas netas y variación, margen bruto, ticket medio, ventas de hoy frente a lo esperado, alertas y dinero inmovilizado.
- **Decisiones propuestas** priorizadas, con evidencia e impacto estimado en USD (reponer, subir o bajar precio, liquidar, revisar coste).
- **Tu precio frente al mercado** por producto vigilado (mediana y rango central de los anuncios).
- **Tendencia de precios en el mercado** en USD equivalentes (los precios en CUP se deflactan con la tasa de cada semana).
  Se construye con las capturas que se guardan en cada análisis: aparece a partir de varias semanas de uso.
- Ventas de 12 meses, más y menos vendidos, más y menos rentables, categorías, tasa USD→CUP, inventario y alertas.
- Calidad de los datos, fuentes y límites del análisis.

**Bot de Telegram**, punto único de contacto del dueño:
- Texto libre. *"¿Cómo se está moviendo el aceite en el mercado?"* → Agente Externo. *"¿Cómo va mi negocio de pollo?"* →
  Agente Interno. *"¿Estoy caro con los ventiladores? ¿Qué hago?"* → cruce de ambos con propuesta.
- Comandos: `/panel` (envía el panel como archivo HTML, que se abre en el navegador del móvil), `/oportunidades`, `/negocio`, `/mercado <producto>`, `/producto <producto>`, `/ayuda`.
- Resumen diario (negocio + decisiones + panel en HTML adjunto) y alertas urgentes del negocio, con anti-repetición y horas de silencio.
- Es el **único bot** del sistema: el Agente Interno se despliega sin el suyo.

**Principios**
- Las cifras las calculan siempre los subagentes o reglas deterministas; el modelo nunca inventa números.
- Cada afirmación lleva su tipo de evidencia (📊 dato · 💡 inferencia/propuesta · ❔ no disponible) y la confianza del mercado.
- Solo lectura: ningún agente cambia precios, stock ni pedidos. El Agente Central propone; el dueño decide.

## Arranque rápido (demo, sin claves)

```bash
git clone https://github.com/iaquantic/Agente-Central-Estudio-Mercado
git clone https://github.com/iaquantic/Agente-Controlador-de-Mercado
cd Agente-Central-Estudio-Mercado
pip install -e ../Agente-Controlador-de-Mercado -r requirements-dev.txt

python -m agente_central comprobar                     # perfil, subagentes y productos vigilados
python -m agente_central panel --salida panel.html     # abre panel.html en el navegador
python -m pytest                                       # pruebas automáticas, sin red ni claves
```

En el modo demo, el **mercado es real**: el Agente Externo consulta Revolico y Cuballama en vivo (necesita conexión;
la primera generación del panel tarda unos minutos porque las webs se consultan con 5 s entre peticiones, y después
se reutiliza la caché de 12 h). El **negocio** es el de prueba del Agente Interno (*MercadoAgentico*): respuestas
capturadas de su base de datos ([demo/interno/fixtures.json](demo/interno/fixtures.json)), con la hora congelada en
2026-09-30 11:30, porque no hay todavía un negocio real conectado. Las pruebas automáticas usan su propio mercado
sintético (`tests/mercado_prueba.py`) para no depender de la red.

Con una clave de Claude (`ANTHROPIC_API_KEY`) y un bot de Telegram:

```bash
cp .env.example .env        # rellena TELEGRAM_BOT_TOKEN, TELEGRAM_USUARIOS, ANTHROPIC_API_KEY
python -m agente_central preguntar "¿Cómo se está moviendo el aceite en el mercado?" --traza
python -m agente_central servir          # bot + panel web en http://127.0.0.1:8090/panel + tareas programadas
```

## Dónde van las claves

Las claves **nunca** van en el repositorio, en el perfil YAML ni en un chat. Van en un archivo `.env` en la máquina
donde corre el Agente Central (el VPS), junto al código:

```bash
cp .env.example .env
nano .env          # o cualquier editor
```

| Variable | Qué es | Dónde se obtiene |
|---|---|---|
| `ANTHROPIC_API_KEY` | Clave de Claude (`sk-ant-…`) | console.anthropic.com → Settings → API keys |
| `TELEGRAM_BOT_TOKEN` | Token del bot (`123456789:AAH…`) | @BotFather → `/newbot` |
| `TELEGRAM_USUARIOS` | IDs numéricos autorizados, separados por comas | Cada persona escribe a @userinfobot |
| `ORQUESTADOR_TOKEN` | Secreto compartido con el Agente Interno (el mismo en ambos `.env`) | `python -c "import secrets;print(secrets.token_urlsafe(32))"` |

`.env` está en `.gitignore`. Protégelo con `chmod 600 .env`. Con Docker, `docker compose` lo lee solo (`env_file`).

## Para una empresa nueva

Todo lo que cambia de un cliente a otro está en **un archivo**: `config/empresa.yaml` (copia de
[config/empresa.plantilla.yaml](config/empresa.plantilla.yaml), que ya trae Revolico y Cuballama como fuentes de mercado). Marca, catálogo vigilado, fuentes de mercado, umbrales de las
reglas, horarios de los avisos y modelo. Los secretos van en `.env`. Paso a paso en
[docs/personalizacion.md](docs/personalizacion.md); despliegue en [docs/despliegue.md](docs/despliegue.md).

## Estructura

| Ruta | Contenido |
|---|---|
| `agente_central/config.py` | Perfil de empresa (YAML) + secretos (entorno), con validación |
| `agente_central/interno.py` | Cliente del contrato v1.0 del Agente Interno y cliente de demostración |
| `agente_central/externo.py` | Uso del Controlador de Mercado (modos `motor` y `agente`), caché de análisis |
| `agente_central/cruce.py` | Cruce negocio × mercado y reglas de propuestas ([docs/reglas_de_cruce.md](docs/reglas_de_cruce.md)) |
| `agente_central/panel.py`, `panel_html.py`, `plantillas/panel.html` | Datos del panel ([contrato](docs/contrato_panel.md)) y render con marca |
| `agente_central/orquestador.py`, `herramientas.py` | Claude con 6 herramientas de enrutado (7 en modo `agente`) |
| `agente_central/bot_telegram.py`, `planificador.py` | Bot, resumen diario y alertas urgentes |
| `agente_central/servidor.py` | Web: `/panel`, `/api/panel`, `/api/panel/actualizar`, `/api/preguntar`, `/salud` |
| `prompts/agente_central_system_prompt.md` | System prompt (plantilla por empresa) |
| `docs/contratos/` | Esquemas JSON del contrato con el Agente Interno |
| `demo/interno/` | Respuestas capturadas del Agente Interno de prueba (negocio *MercadoAgentico*) |
| `tests/` | Pruebas automáticas (el orquestador se prueba con un cliente de Claude simulado) |

## Estado del MVP
- ✅ Orquestación de los dos subagentes, cruce con 6 reglas, panel Quantic, bot con comandos y avisos, API web.
- ✅ Reproducible: perfil por empresa, mercado real (Revolico y Cuballama), Docker para los tres agentes, pruebas sin red.
- ⏳ Pendiente: prueba conversacional con Claude real y Telegram real, despliegue en VPS con el Agente Interno en modo `api`,
  y activar fuentes web de mercado con la autorización de cada sitio (ver el README del Controlador de Mercado).
