# Plan de publicación en internet

> **Estado:** dashboard publicado en **https://appshield-ai.streamlit.app** (Streamlit Community Cloud). La API no está publicada; para hacerlo, ver [API en internet](#api-en-internet-pendiente).

**Objetivo:** publicar el dashboard como demo pública del portafolio y la API protegida con API key, ambos desde la misma imagen Docker. Los datos son sintéticos: la demo no expone información real.

---

## Fase 1 — Endurecer el código para internet ✅

| # | Cambio | Motivo | Resultado verificado |
|---|---|---|---|
| 1.1 | Límite de tamaño del cuerpo en la API: 413 por encima de 1 KB, 411 sin `Content-Length` | FastAPI lee el cuerpo antes de comprobar la API key: sin límite, un cliente sin clave puede enviar JSON enormes y consumir memoria. | Pruebas y servidor real: 2 KB sin clave → 413 (antes de autenticar); envío *chunked* → 411. |
| 1.2 | `.streamlit/config.toml`: `toolbarMode = "viewer"`, `gatherUsageStats = false`, XSRF activo | Ocultar el botón "Deploy" y el menú de desarrollador en la demo pública. | Dashboard en el contenedor sin botón "Deploy". |
| 1.3 | Uvicorn con `--proxy-headers` | Detrás de un proxy de confianza, los logs deben registrar la IP real del cliente. | Activo en `docker-compose.yml`, donde no confía en nadie salvo 127.0.0.1 (un `X-Forwarded-For` falso se ignora). **En Cloud Run no se usa:** ver [la nota sobre la IP real](#la-ip-real-detrás-del-proxy). |
| 1.4 | `tensorflow-cpu` en la imagen | Sin GPU en el servidor: imagen más pequeña. | Mismas probabilidades y veredictos que con `tensorflow`; CI completo en verde. |

**Medidas de la imagen (Docker Compose local):**

| | Con `tensorflow` | Con `tensorflow-cpu` |
|---|---|---|
| Tamaño de la imagen | 3,74 GB | **2,67 GB** (−29 %) |
| RAM de la API en reposo | 333 MiB | **286 MiB** |
| RAM del dashboard con una sesión abierta | 354 MiB | **333 MiB** |
| Arranque hasta `healthy` | ~12 s | ~8 s |

Con estas cifras, **1 GiB por servicio basta** en la Fase 2, con margen para varias sesiones simultáneas en el dashboard.

## Fase 2 — Plataforma ✅ Streamlit Community Cloud

**Elegida: Streamlit Community Cloud** — gratuita, sin tarjeta, despliega directamente desde este repositorio de GitHub.

| Ajuste | Valor |
|---|---|
| Repositorio / rama | `MurginhaSebastian/AuthShield-AI` · `main` |
| Archivo principal | `app/dashboard.py` |
| Python | **3.12** (TensorFlow 2.18 no soporta 3.13) |
| Dependencias | `requirements.txt` de la raíz |
| Configuración | `.streamlit/config.toml` (se lee automáticamente) |

**Por qué no las otras opciones:**

| Plataforma | Resultado |
|---|---|
| Hugging Face Spaces | Rechazado con **402**: los Spaces Docker en hardware gratuito exigen suscripción PRO (9 USD/mes). Si se activa PRO, `deploy/huggingface/publish.py <usuario>/<space>` publica el dashboard (sube solo una lista explícita de archivos; nunca `.env`). |
| Google Cloud Run | Requiere facturación con tarjeta. Sigue siendo la opción para publicar también la API (ver abajo). |
| Render | El plan gratuito (512 MB, CPU compartida mínima) es demasiado justo para cargar TensorFlow. |

**Limitaciones del plan gratuito:** la app se suspende tras unos días sin visitas; quien entre después ve un botón para despertarla y espera a que arranque.

## Fase 3 — Despliegue continuo ✅ (lo hace la plataforma)

Streamlit Community Cloud vuelve a desplegar el dashboard en cada push a `main`. No hace falta un job de despliegue propio.

**Riesgo conocido:** la plataforma despliega aunque el CI de GitHub falle. Mitigación: trabajar en ramas y fusionar a `main` solo con el CI en verde (se puede forzar con una regla de protección de rama en GitHub).

## Fase 4 — Verificación pública ✅ (dashboard)

Verificado con Playwright (Edge) sobre la URL pública:

- Simulación de 1 000 eventos: 105/106 ataques detectados, igual que en local.
- Pestañas "Evento manual" (veredicto `NORMAL` por defecto) y "Modelos" (métricas y latencia p95) correctas.
- Sin errores de consola; se ve bien en escritorio y en móvil (390 px).

## API en internet (pendiente)

La API (`app/api.py`) solo corre en local o con Docker Compose. Para publicarla, el plan sigue siendo **Google Cloud Run**:

| Servicio | Comando | Puerto | Memoria | Instancias | Notas |
|---|---|---|---|---|---|
| `authshield-api` | `uvicorn app.api:app --host 0.0.0.0 --port 8000` (sin opciones de proxy) | 8000 | 1 GiB | 0–1 | `AUTHSHIELD_API_KEYS` desde Secret Manager |

- `max-instances=1` mantiene exacto el límite de peticiones (el contador vive en memoria de un proceso; ver README, 7.1).

### La IP real detrás del proxy

**No se usa `--forwarded-allow-ips="*"`.** Con esa opción, uvicorn toma la IP **más a la izquierda** de `X-Forwarded-For` (`uvicorn/middleware/proxy_headers.py`). Esa parte la escribe el cliente; el proxy de Google añade la IP real al **final**. Resultado: cualquiera podría falsificar su IP en los logs de uvicorn. La prueba local lo confirmó: con `*`, una petición con `X-Forwarded-For: 203.0.113.7` quedó registrada como `203.0.113.7`.

La IP real se obtiene del **log de peticiones de Cloud Run** (`httpRequest.remoteIp`), que escribe el propio proxy de Google y el cliente no puede alterar. La API no usa la IP para ninguna decisión (el límite de peticiones va por API key), así que no se pierde nada.
- Pasos del propietario: instalar `gcloud`, iniciar sesión, crear el proyecto, activar facturación y una alerta de presupuesto (p. ej. 5 USD).
- Despliegue continuo con un job `deploy` en el CI y **Workload Identity Federation** (sin claves JSON en el repositorio).
- Verificación: `curl` a la URL pública — 401 sin clave, 200 con clave, 429 al superar el límite, 413 con cuerpo grande, 411 sin `Content-Length`.
