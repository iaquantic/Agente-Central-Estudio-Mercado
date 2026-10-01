# Configurar el sistema para una empresa nueva

El código es el mismo para todos los clientes. Lo que cambia está en dos sitios:

| Archivo | Contenido | ¿Se versiona? |
|---|---|---|
| `config/empresa.yaml` | Identidad, marca, catálogo vigilado, fuentes de mercado, umbrales, horarios, modelo | Sí (sin secretos) |
| `.env` | Tokens de Telegram y del Agente Interno, clave de Claude, token del panel | **Nunca** |

## 1. Requisitos previos

1. **Agente Interno** desplegado para la empresa (repositorio `Agente-Controlador-de-Negocio`): su BD con el esquema
   `agente`, y su API activa con un `ORQUESTADOR_TOKEN`. Comprueba que responde a `POST /v1/consulta`.
2. **Bot de Telegram** nuevo (@BotFather) y los IDs numéricos de las personas autorizadas (@userinfobot).
3. **Clave de Claude** de la cuenta de Quantic o del cliente.
4. Autorización de los sitios de mercado que se vayan a consultar (ver condiciones de uso en el README del Controlador de Mercado).

## 2. Perfil de empresa

```bash
cp config/empresa.plantilla.yaml config/empresa.yaml
```
La plantilla ya viene en modo producción (`interno.modo: api`, `demo.ahora: null`) y con Revolico y Cuballama como fuentes.
Lo que hay que cambiar está marcado con `CAMBIAR`.

| Sección | Qué ajustar |
|---|---|
| `empresa` | `id` (corto, sin espacios: se usa en rutas de datos), `nombre`, `descripcion`, `zona_horaria`, monedas |
| `marca` | Por defecto, formato Quantic. Para co-marca, añade `logo_cliente` (SVG o PNG). Para marca blanca, cambia `proveedor`, `logo`, `colores` y `tipografias` (nombres de Google Fonts) |
| `interno` | `modo: api`, `url` del Agente Interno y nombre de la variable con su token (`token_env`) |
| `mercado` | `fuentes` (archivos autorizados o `web`: revolico, cuballama, cubamax, cubatel, con `opciones` del adaptador, p. ej. `default_province` de Cuballama), `provincias`, ventanas de análisis, `modo` |
| `catalogo_vigilado` | Los SKU que se siguen en el mercado y cómo buscarlos (ver abajo) |
| `reglas` | Umbrales de las propuestas ([reglas_de_cruce.md](reglas_de_cruce.md)) |
| `telegram` | Hora del resumen diario, alertas urgentes, silencio nocturno, máximo diario, límite de consultas |
| `modelo` | Modelo de Claude y esfuerzo |
| `demo.ahora` | **`null` en producción** (congela la hora del negocio; el mercado siempre usa la hora real) |

### Catálogo vigilado

Cada entrada une un SKU del negocio con su búsqueda en el mercado (formato `TargetProduct` del Controlador de Mercado):

```yaml
catalogo_vigilado:
  - sku: GRA-010
    mercado: {name: aceite de girasol, quantity: 1, unit: L, exclude_keywords: [motor]}
  - sku: GRA-014
    mercado: {name: huevos, pack_count: 30}
```

- `name`: nombre genérico, sin marca ni tamaño. `brand` solo si el cliente compite en una marca concreta.
- `quantity` + `unit` (ml, L, g, kg, lb, unidades) o `pack_count`: la presentación. Las estadísticas por anuncio solo usan
  la misma presentación; las demás entran en el precio por unidad estándar.
- `exclude_keywords`: términos que descartan anuncios confusos ("motor" para aceite, "recargable" para ventiladores de red).
- Empieza por 8–15 productos clave (los de más ventas, los prioritarios y los que dan problemas). Cada producto vigilado
  supone una consulta a cada fuente web en cada refresco.
- Los productos no vigilados también se pueden preguntar por Telegram: el orquestador construye la búsqueda.

Comprueba el resultado con:

```bash
EMPRESA_CONFIG=config/empresa.yaml python -m agente_central comprobar
```

Muestra cada SKU con su nombre en el negocio (✗ si no existe) y las fuentes de mercado configuradas.

### Afinar las búsquedas
Genera el panel y revisa en "Calidad de los datos y fuentes" la confianza de cada producto. Con confianza LOW o
pocos anuncios: amplía `name` (sinónimos en `keywords` no ayudan: son obligatorios), quita restricciones de presentación
o revisa `exclude_keywords`. El análisis completo de un producto, con la traza por anuncio, se obtiene con la CLI del
Controlador de Mercado (`controlador-mercado analizar --producto '{...}' --web revolico`).

## 3. Variables de entorno

```bash
cp .env.example .env
```
Rellena `EMPRESA_CONFIG`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USUARIOS`, `ANTHROPIC_API_KEY`, `INTERNO_MODO=api`,
`INTERNO_URL`, `ORQUESTADOR_TOKEN` y `PANEL_TOKEN`. Deja `PANEL_URL_PUBLICA` vacío: el bot envía el panel como archivo HTML.
Detalle de cada clave en el README ("Dónde van las claves").

## 4. Puesta en marcha

```bash
python -m agente_central comprobar
python -m agente_central panel --salida panel.html      # revisar el panel antes de dárselo al cliente
python -m agente_central servir                         # o docker compose (docs/despliegue.md)
```

Lista de verificación de entrega:
- [ ] `comprobar` sin ✗; `demo.ahora: null`.
- [ ] Panel revisado con el cliente: productos vigilados correctos, precios de mercado razonables, propuestas con sentido.
- [ ] Bot: `/start` desde un ID autorizado responde; desde otro ID responde "no estás autorizado".
- [ ] Una pregunta de mercado, una de negocio y una mixta responden con datos.
- [ ] Resumen diario recibido a la hora configurada.
- [ ] El bot propio del Agente Interno está desactivado (sin `TELEGRAM_BOT_TOKEN` en su `.env`) para no duplicar alertas.

## 5. Otra marca o idioma visual
El panel toma de `marca` los colores (`primario` para la banda superior, `acento_fuerte` para la serie "tu negocio"),
las tipografías y los logos. La serie del mercado (naranja) y los colores de estado son fijos para que el panel sea
legible y accesible con cualquier marca; si cambias `acento_fuerte`, elige un tono con contraste ≥ 3:1 sobre blanco.
