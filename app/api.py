"""Microservicio de evaluación de riesgo de AuthShield-AI (FastAPI)."""

import hmac
import math
import os
import sys
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

# Igual que el dashboard: permite arrancar desde cualquier directorio.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import load_artifacts, score  # noqa: E402

API_KEYS_ENV = "AUTHSHIELD_API_KEYS"
MIN_KEY_LENGTH = 32
RATE_LIMIT_ENV = "AUTHSHIELD_RATE_LIMIT_PER_MIN"
DEFAULT_RATE_LIMIT = 60
WINDOW_S = 60.0
MAX_BODY_BYTES = 1024  # un evento válido ocupa ~100 bytes


def load_api_keys() -> list[str]:
    """Claves válidas desde AUTHSHIELD_API_KEYS, separadas por comas (varias permiten rotarlas).

    Sin claves la API no arranca: no existe un modo sin autenticación que se pueda olvidar encendido.
    """
    keys = [k.strip() for k in os.environ.get(API_KEYS_ENV, "").split(",") if k.strip()]
    if not keys:
        raise RuntimeError(f"Define {API_KEYS_ENV}: la API no arranca sin autenticación.")
    if any(len(k) < MIN_KEY_LENGTH for k in keys):
        raise RuntimeError(f"Cada API key debe tener al menos {MIN_KEY_LENGTH} caracteres.")
    return keys


def load_rate_limit() -> int:
    """Peticiones por minuto y por API key (AUTHSHIELD_RATE_LIMIT_PER_MIN, por defecto 60)."""
    raw = os.environ.get(RATE_LIMIT_ENV, str(DEFAULT_RATE_LIMIT))
    if not raw.strip().isdigit() or int(raw) < 1:
        raise RuntimeError(f"{RATE_LIMIT_ENV} debe ser un entero mayor que 0.")
    return int(raw)


class RateLimiter:
    """Ventana deslizante de 60 s por API key.

    Solo cuenta claves ya autenticadas: inventar claves no hace crecer la memoria.
    """

    # shortcut: el contador vive en este proceso; con varios workers o réplicas cada uno cuenta
    # por su lado y el límite real se multiplica. Al escalar, moverlo a Redis o al gateway.

    def __init__(self, limit: int):
        self.limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # FastAPI atiende los endpoints síncronos en varios hilos

    def retry_after(self, key: str, now: float) -> int:
        """0 si la petición entra (y la registra); si no, segundos hasta que quede hueco."""
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= WINDOW_S:
                hits.popleft()
            if len(hits) < self.limit:
                hits.append(now)
                return 0
            return max(1, math.ceil(WINDOW_S - (now - hits[0])))


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(request: Request, api_key: str | None = Security(api_key_header)) -> str:
    """401 si falta la clave o no coincide con ninguna configurada."""
    # compare_digest tarda lo mismo acierte o falle: no deja adivinar la clave midiendo tiempos.
    # Se compara en bytes porque con texto no ASCII lanzaría TypeError (un 500).
    if not api_key or not any(
        hmac.compare_digest(api_key.encode(), key.encode()) for key in request.app.state.api_keys
    ):
        raise HTTPException(
            status_code=401, detail="API key inválida o ausente", headers={"WWW-Authenticate": "APIKey"}
        )
    return api_key


def require_rate_limit(request: Request, api_key: str = Security(require_api_key)) -> None:
    """429 con Retry-After si esta clave superó su límite por minuto."""
    wait = request.app.state.rate_limiter.retry_after(api_key, time.monotonic())
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Límite de peticiones superado",
            headers={"Retry-After": str(wait)},
        )


class LoginEvent(BaseModel):
    """Evento de autenticación ya resumido en las 4 features (README, 4)."""

    model_config = ConfigDict(extra="forbid")

    failed_logins_5m: int = Field(ge=0, description="Intentos fallidos en los últimos 5 minutos")
    is_new_ip: Literal[0, 1] = Field(description="1 si la IP nunca se vio para este usuario")
    unusual_hour: Literal[0, 1] = Field(description="1 si ocurre fuera del horario habitual")
    requests_per_minute: float = Field(
        ge=0, allow_inf_nan=False, description="Peticiones de autenticación por minuto"
    )


class Verdict(BaseModel):
    p_rf: float = Field(description="Probabilidad de fuerza bruta (Random Forest)")
    p_nn: float = Field(description="Probabilidad de anomalía de identidad (red neuronal)")
    verdict: Literal["ALERTA CRÍTICA", "FUERZA BRUTA", "ANOMALÍA DE IDENTIDAD", "NORMAL"]
    latency_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sin claves o sin artefactos el servicio no arranca: mejor fallar aquí que en la primera petición.
    app.state.api_keys = load_api_keys()
    app.state.rate_limiter = RateLimiter(load_rate_limit())
    app.state.artifacts = load_artifacts()
    # Calentamiento: Keras traza el grafo en la primera llamada (~60 ms) y no debe pagarlo un usuario.
    warmup = LoginEvent(failed_logins_5m=0, is_new_ip=0, unusual_hour=0, requests_per_minute=1.0)
    score(pd.DataFrame([warmup.model_dump()]), app.state.artifacts)
    yield


app = FastAPI(
    title="AuthShield-AI",
    description="Evalúa eventos de login con un Random Forest (fuerza bruta) y una red neuronal (identidad).",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """411/413 antes de autenticar: FastAPI lee el cuerpo entero antes de comprobar la API key."""
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length is None:
            return JSONResponse(status_code=411, content={"detail": "Falta Content-Length"})
        if not length.isdigit() or int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413, content={"detail": f"Cuerpo mayor que {MAX_BODY_BYTES} bytes"}
            )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Sin eco del valor recibido: Infinity/NaN no se pueden serializar y tumbarían la respuesta.
    errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/evaluate", response_model=Verdict, dependencies=[Security(require_rate_limit)])
def evaluate(event: LoginEvent, request: Request) -> Verdict:
    start = time.perf_counter()
    try:
        result = score(pd.DataFrame([event.model_dump()]), request.app.state.artifacts).iloc[0]
    except ValueError as exc:  # segunda barrera: validate() del pipeline
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Verdict(
        p_rf=round(float(result["p_rf"]), 4),
        p_nn=round(float(result["p_nn"]), 4),
        verdict=result["verdict"],
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
    )
