# Plan de publicación en internet

> **Estado:** dashboard publicado en **https://appshield-ai.streamlit.app** (Streamlit Community Cloud) y API publicada en **https://authshield-api-624940884502.us-central1.run.app** (Google Cloud Run; ver [API en internet](#api-en-internet--google-cloud-run)).

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
| Google Cloud Run | Requiere facturación con tarjeta. Se usa solo para la API (ver abajo). |
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

## API en internet ✅ Google Cloud Run

| Servicio | Comando | Puerto | Memoria | Instancias | Notas |
|---|---|---|---|---|---|
| `authshield-api` | `uvicorn app.api:app --host 0.0.0.0 --port 8000` (sin opciones de proxy) | 8000 | 1 GiB | 0–1 | `AUTHSHIELD_API_KEYS` desde Secret Manager |

- `max-instances=1` mantiene exacto el límite de peticiones (el contador vive en memoria de un proceso; ver README, 7.1).
- Proyecto `authshield-ai`, región `us-central1`.

**Infraestructura** (creada una vez con `gcloud`):

| Recurso | Para qué | Permisos |
|---|---|---|
| Artifact Registry `authshield` | Imágenes de la API, etiquetadas con el commit | Regla de limpieza: conserva las 2 versiones más recientes |
| Secret Manager `authshield-api-keys` | La API key; nunca está en el repositorio ni en la imagen | — |
| Cuenta `authshield-api-runtime` | Con la que corre la API | Solo lee ese secreto |
| Cuenta `github-deployer` | Con la que despliega el CI | `run.admin`, escribir en el registro, actuar como la cuenta runtime |
| Workload Identity Federation (pool `github`) | GitHub se identifica ante Google sin claves JSON | Solo el repositorio `MurginhaSebastian/AuthShield-AI` y la rama `main` |

Las variables del repositorio (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`) son identificadores, no secretos.

### Control de costes

La cuenta tiene facturación activa: Google **sí puede cobrar**. Uso esperado de una demo de portafolio: dentro de la capa gratuita de Cloud Run (2 M peticiones, 180 000 vCPU-s y 360 000 GiB-s al mes, por cuenta de facturación). Lo único que se cobra es el almacenamiento de la imagen por encima de los 0,5 GB gratis del registro: céntimos al mes.

| Protección | Qué hace | Límite |
|---|---|---|
| `max-instances=1`, `min-instances=0` | Como mucho una instancia, y ninguna sin tráfico | CPU y memoria: ~2,3 USD/día en el peor caso |
| Tope de gasto (*spend cap*) S/.7 en Cloud Run | Al alcanzarlo, Google **pausa** la API (responde 5xx) hasta el mes siguiente o hasta subir el tope | No es instantáneo: los costes pueden tardar más de 24 h en registrarse, y ese exceso se cobra |
| Alerta de presupuesto S/.5 (toda la cuenta) | Correo al 50/90/100/150 % | Solo avisa, no detiene nada |

**Riesgo restante:** el límite de peticiones solo cuenta las que llevan clave. Una inundación de peticiones sin clave (401) se factura por petición hasta que el tope actúa.

**Apagarlo todo:** `gcloud run services delete authshield-api --region us-central1` (solo la API) o `gcloud projects delete authshield-ai` (todo el proyecto).

### La IP real detrás del proxy

**No se usa `--forwarded-allow-ips="*"`.** Con esa opción, uvicorn toma la IP **más a la izquierda** de `X-Forwarded-For` (`uvicorn/middleware/proxy_headers.py`). Esa parte la escribe el cliente; el proxy de Google añade la IP real al **final**. Resultado: cualquiera podría falsificar su IP en los logs de uvicorn. La prueba local lo confirmó: con `*`, una petición con `X-Forwarded-For: 203.0.113.7` quedó registrada como `203.0.113.7`.

La IP real se obtiene del **log de peticiones de Cloud Run** (`httpRequest.remoteIp`), que escribe el propio proxy de Google y el cliente no puede alterar. La API no usa la IP para ninguna decisión (el límite de peticiones va por API key), así que no se pierde nada.

### Verificación pública ✅

Con `curl` contra la URL pública (26-oct-2026), con la clave leída de Secret Manager a una variable, sin imprimirla:

| Prueba | Resultado |
|---|---|
| `/health` | 200 `{"status":"ok"}` |
| `/evaluate` sin clave / con clave falsa | 401 / 401 |
| `/evaluate` con clave (0, sí, sí, 8) | 200 `ANOMALÍA DE IDENTIDAD` (p_nn 0,9961) |
| 61 peticiones seguidas con clave | 60 × 200 y la 61.ª → 429 |
| Cuerpo de 2 KB sin clave | 413 |
| Envío *chunked* | El proxy de Google lo convierte antes de llegar a la API: con cuerpo pequeño → 401 (no 411); con 2 KB → 413. El límite de tamaño se mantiene. |
| IP real con `X-Forwarded-For: 203.0.113.7` falso | `httpRequest.remoteIp` en el log de Cloud Run = IP pública real del cliente |
| Configuración del servicio | máx. 1 instancia, 1 CPU, 1 GiB, cuenta `authshield-api-runtime`, secreto montado como variable |
| Imagen en el registro | 559 MB comprimida (2,67 GB descomprimida) |

### Despliegue continuo

Job `deploy` en `.github/workflows/ci-cd.yml`: solo tras un merge a `main` y con los demás jobs en verde. Construye la imagen, la sube al registro, despliega y hace una prueba de humo (`/health` → 200, `/evaluate` sin clave → 401).
