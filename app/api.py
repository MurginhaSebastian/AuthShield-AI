"""Microservicio de evaluación de riesgo de AuthShield-AI (FastAPI)."""

import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

# Igual que el dashboard: permite arrancar desde cualquier directorio.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import load_artifacts, score  # noqa: E402


class LoginEvent(BaseModel):
    """Evento de autenticación ya resumido en las 4 features (README, 4)."""

    model_config = ConfigDict(extra="forbid")

    failed_logins_5m: int = Field(ge=0, description="Intentos fallidos en los últimos 5 minutos")
    is_new_ip: Literal[0, 1] = Field(description="1 si la IP nunca se vio para este usuario")
    unusual_hour: Literal[0, 1] = Field(description="1 si ocurre fuera del horario habitual")
    requests_per_minute: float = Field(ge=0, description="Peticiones de autenticación por minuto")


class Verdict(BaseModel):
    p_rf: float = Field(description="Probabilidad de fuerza bruta (Random Forest)")
    p_nn: float = Field(description="Probabilidad de anomalía de identidad (red neuronal)")
    verdict: Literal["ALERTA CRÍTICA", "FUERZA BRUTA", "ANOMALÍA DE IDENTIDAD", "NORMAL"]
    latency_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sin artefactos el servicio no arranca: mejor fallar aquí que en la primera petición.
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/evaluate", response_model=Verdict)
def evaluate(event: LoginEvent, request: Request) -> Verdict:
    start = time.perf_counter()
    result = score(pd.DataFrame([event.model_dump()]), request.app.state.artifacts).iloc[0]
    return Verdict(
        p_rf=round(float(result["p_rf"]), 4),
        p_nn=round(float(result["p_nn"]), 4),
        verdict=result["verdict"],
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
    )
