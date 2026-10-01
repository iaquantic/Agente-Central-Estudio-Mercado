# Despliegue en Easypanel

El Agente Central es **un solo servicio** (una app con Dockerfile). El bot de Telegram funciona por *polling*, así que
no necesita dominio ni puertos abiertos: el dueño recibe el panel como archivo HTML por Telegram.

Mientras el Agente Interno no esté conectado a un negocio real, el Central usa el negocio de prueba
(`interno.modo: demo`) y el mercado real de Revolico y Cuballama.

## 1. Crear el servicio

1. En Easypanel: **Create Project** (por ejemplo `quantic-mercado`) → **+ Service** → **App**.
   Nombre: `agente-central`.
2. **Source** → GitHub:
   - Owner: `iaquantic`
   - Repository: `Agente-Central-Estudio-Mercado`
   - Branch: `claude/nifty-ramanujan-u1p1sx` (o `main` cuando se fusione)
   - Build path: `/`
3. **Build** → **Dockerfile** (ruta `Dockerfile`). La imagen instala el Agente Externo desde su repositorio público.

## 2. Variables de entorno (pestaña Environment)

```env
EMPRESA_CONFIG=config/empresa.demo.yaml
TELEGRAM_BOT_API=<token de @BotFather>
TELEGRAM_USUARIOS=2002725655
CLAUDE_ACTIVO=0
# Cuando haya créditos de Anthropic:
# ANTHROPIC_API_KEY=<clave sk-ant-…>
# CLAUDE_ACTIVO=1
```

- `TELEGRAM_USUARIOS`: IDs autorizados separados por comas (cada persona lo obtiene escribiendo a @userinfobot).
- `CLAUDE_ACTIVO=0`: ninguna llamada a Claude. Funcionan `/panel`, `/oportunidades`, `/negocio`, el resumen diario y
  las alertas; el texto libre responde con los comandos disponibles.
- No hace falta definir `DIRECTORIO_DATOS` ni `CONTROLADOR_CACHE_DIR`: la imagen ya los apunta a `/app/datos`.

## 3. Volumen persistente (pestaña Mounts)

**Add Volume** → nombre `datos`, ruta de montaje **`/app/datos`**.

Ahí se guardan el último panel, la caché de análisis, el registro de uso, las alertas ya enviadas y el **histórico de
capturas de Revolico y Cuballama**, que es lo que permite calcular tendencias. Usa un volumen (no un *bind mount* a una
carpeta del host): el contenedor corre con un usuario sin privilegios y necesita poder escribir ahí.

## 4. Desplegar

1. **Deploy**. La primera construcción tarda unos minutos.
2. En **Logs** debe aparecer `Bot de Telegram en marcha (1 usuarios autorizados)`.
3. Escribe `/start` y luego `/panel` al bot. El primer `/panel` tarda unos minutos (consulta las webs con pausas de 5 s);
   después se reutiliza la caché de 12 h.

**Importante:** un mismo token de bot solo puede tenerlo en marcha un proceso a la vez. Si el bot está corriendo en
otro sitio (por ejemplo, en una sesión de pruebas), detenlo antes; si no, Telegram devuelve un error de conflicto.

## 5. Comprobaciones y operación

| Qué | Cómo (Easypanel → servicio → Console) |
|---|---|
| Estado general | `python -m agente_central comprobar` |
| Regenerar el panel ignorando la caché | `python -m agente_central panel --forzar --salida /tmp/p.html` |
| Ver el uso del bot | `tail -n 20 /app/datos/empresa/registro.jsonl` |
| Salud | El contenedor tiene `HEALTHCHECK` sobre `/salud` (puerto interno 8090) |

Actualizar a una versión nueva: **Deploy** de nuevo (o activa el despliegue automático en cada *push* de la rama).

## 6. Cuando haya un negocio real

1. Desplegar el Agente Interno (`Agente-Controlador-de-Negocio`) como otra App del mismo proyecto, **sin**
   `TELEGRAM_BOT_TOKEN` (el bot es solo el del Central) y con `API_HOST=0.0.0.0`, `ORQUESTADOR_TOKEN` y la conexión a su BD.
2. Crear el perfil del cliente desde `config/empresa.plantilla.yaml` (en el repositorio o en un archivo montado) y en el
   Central poner `EMPRESA_CONFIG=<ese perfil>`, `INTERNO_URL=http://<nombre-del-servicio-interno>:8080/v1/consulta` y el
   mismo `ORQUESTADOR_TOKEN`.
