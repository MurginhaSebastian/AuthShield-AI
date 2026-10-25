# Plan de publicación en internet

> **Estado:** Fase 1 completada. Fases 2–4 pendientes: la plataforma aún no está elegida (ver [Decisiones pendientes](#decisiones-pendientes)). Se usará la URL de la plataforma, sin dominio propio.

**Objetivo:** publicar el dashboard como demo pública del portafolio y la API protegida con API key, ambos desde la misma imagen Docker. Los datos son sintéticos: la demo no expone información real.

---

## Fase 1 — Endurecer el código para internet ✅

| # | Cambio | Motivo | Resultado verificado |
|---|---|---|---|
| 1.1 | Límite de tamaño del cuerpo en la API: 413 por encima de 1 KB, 411 sin `Content-Length` | FastAPI lee el cuerpo antes de comprobar la API key: sin límite, un cliente sin clave puede enviar JSON enormes y consumir memoria. | Pruebas y servidor real: 2 KB sin clave → 413 (antes de autenticar); envío *chunked* → 411. |
| 1.2 | `.streamlit/config.toml`: `toolbarMode = "viewer"`, `gatherUsageStats = false`, XSRF activo | Ocultar el botón "Deploy" y el menú de desarrollador en la demo pública. | Dashboard en el contenedor sin botón "Deploy". |
| 1.3 | Uvicorn con `--proxy-headers` | Detrás del balanceador, los logs deben registrar la IP real del cliente. | Activo en `docker-compose.yml`; ver nota de `--forwarded-allow-ips` en la Fase 2. |
| 1.4 | `tensorflow-cpu` en la imagen | Sin GPU en el servidor: imagen más pequeña. | Mismas probabilidades y veredictos que con `tensorflow`; CI completo en verde. |

**Medidas de la imagen (Docker Compose local):**

| | Con `tensorflow` | Con `tensorflow-cpu` |
|---|---|---|
| Tamaño de la imagen | 3,74 GB | **2,67 GB** (−29 %) |
| RAM de la API en reposo | 333 MiB | **286 MiB** |
| RAM del dashboard con una sesión abierta | 354 MiB | **333 MiB** |
| Arranque hasta `healthy` | ~12 s | ~8 s |

Con estas cifras, **1 GiB por servicio basta** en la Fase 2, con margen para varias sesiones simultáneas en el dashboard.

## Fase 2 — Plataforma

**Recomendación: Google Cloud Run.** Despliega la imagen tal cual, HTTPS automático, secretos en Secret Manager, escala a cero y su capa gratuita cubre tráfico de portafolio. Permite fijar `max-instances=1`, lo que mantiene exacto el límite de peticiones (el contador vive en memoria de un proceso; ver README, 7.1).

| Servicio | Comando | Puerto | Memoria | Instancias | Notas |
|---|---|---|---|---|---|
| `authshield-ui` | por defecto (Streamlit) | 8501 | 1 GiB | 0–1 | Afinidad de sesión (websockets de Streamlit) |
| `authshield-api` | `uvicorn app.api:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"` | 8000 | 1 GiB | 0–1 | `AUTHSHIELD_API_KEYS` desde Secret Manager |

**Sobre `--forwarded-allow-ips="*"`:** uvicorn solo cree las cabeceras `X-Forwarded-For` de las IP indicadas (por defecto, 127.0.0.1). En la plataforma el proxy tiene otra IP, así que hay que confiar en todas. Solo es seguro porque el contenedor no es accesible directamente desde internet, sino siempre a través del proxy de la plataforma (así funciona Cloud Run). Si el contenedor se expusiera sin proxy, cualquiera podría falsificar su IP en los logs.

**Pasos manuales del propietario (una vez):** crear el proyecto en Google Cloud, activar facturación y una alerta de presupuesto (p. ej. 5 USD).

**Compromiso del escalado a cero:** el primer acceso tras un rato sin tráfico tarda ~10–20 s en cargar TensorFlow. Si molesta en la demo, `min-instances=1` en el dashboard (con coste mensual).

**Alternativas evaluadas:**

| Plataforma | A favor | En contra |
|---|---|---|
| Hugging Face Spaces | Gratis, sin tarjeta, habitual en portafolios de ML | Un Space expone una sola app: solo el dashboard, la API quedaría sin publicar |
| Render | Configuración muy simple | El plan gratuito (512 MB) no alcanza para TensorFlow; ~7–25 USD/mes por servicio |

## Fase 3 — Despliegue continuo desde GitHub

- Nuevo job `deploy` en `.github/workflows/ci-cd.yml`: solo en `main` y solo si secretos, pruebas y build pasan.
- Construye la imagen, la sube a Artifact Registry y actualiza ambos servicios.
- Autenticación con **Workload Identity Federation**: GitHub se identifica ante Google sin claves JSON guardadas en el repositorio.

## Fase 4 — Verificación pública y portafolio

- `curl` contra las URLs públicas: 401 sin clave, 200 con clave, 429 al superar el límite, 413 con cuerpo grande.
- Dashboard recorrido en navegador (Playwright) sobre la URL pública.
- Monitor de disponibilidad sobre `/health` (p. ej. UptimeRobot, gratuito).
- README: enlace "Demo en vivo" y badge del CI.

## Decisiones pendientes

1. **Plataforma:** Cloud Run (recomendada), Hugging Face Spaces o Render.
2. **Dominio propio** o URLs de la plataforma.
3. **Dashboard abierto o con login** (`st.login` con OIDC). Abierto es razonable para una demo con datos sintéticos.
