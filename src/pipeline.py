"""Simulación de eventos de autenticación, validación y escalado de features."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

FEATURES = ["failed_logins_5m", "is_new_ip", "unusual_hour", "requests_per_minute"]
BINARY_FEATURES = ["is_new_ip", "unusual_hour"]
CONTINUOUS_FEATURES = ["failed_logins_5m", "requests_per_minute"]
ATTACK_TYPES = ("normal", "brute_force", "account_takeover")

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "auth_logs.csv"
MODELS_DIR = ROOT / "models"


def _poisson(rng: np.random.Generator, lam: float):
    return lambda n: rng.poisson(lam, n)


def _uniform_int(rng: np.random.Generator, low: int, high: int):
    return lambda n: rng.integers(low, high + 1, n)


def _lognormal(rng: np.random.Generator, median: float, sigma: float):
    return lambda n: rng.lognormal(np.log(median), sigma, n)


def _profiles(rng: np.random.Generator) -> dict[str, list[tuple]]:
    """Perfiles de comportamiento por clase.

    Cada perfil es (peso dentro de su clase, fallos en 5 min, P(IP nueva),
    P(hora inusual), peticiones por minuto). Los perfiles legítimos "de viaje",
    "olvidó la contraseña" e "integraciones" se solapan a propósito con los
    ataques: sin ese solapamiento los modelos solo reaprenderían las reglas del
    simulador y las métricas no dirían nada (README, 7.3).
    """
    return {
        "normal": [
            (0.80, _poisson(rng, 0.2), 0.03, 0.08, _lognormal(rng, 2, 0.5)),  # uso diario
            (0.10, _uniform_int(rng, 1, 4), 0.05, 0.10, _lognormal(rng, 3, 0.5)),  # olvidó la contraseña
            (0.05, _poisson(rng, 0.3), 1.00, 0.30, _lognormal(rng, 2, 0.5)),  # de viaje
            (0.05, _poisson(rng, 0.2), 0.05, 0.30, _lognormal(rng, 15, 0.4)),  # integraciones / NAT compartido
        ],
        "brute_force": [
            (0.70, _poisson(rng, 25), 0.85, 0.40, _lognormal(rng, 120, 0.6)),  # ataque rápido
            (0.30, _uniform_int(rng, 3, 9), 0.70, 0.40, _lognormal(rng, 6, 0.4)),  # bajo y lento
        ],
        "account_takeover": [
            # Tras entrar, el atacante recorre la cuenta a ritmo de script: más peticiones/min.
            (0.85, _poisson(rng, 0.2), 0.97, 0.90, _lognormal(rng, 8, 0.5)),  # credenciales robadas
            (0.15, _uniform_int(rng, 1, 2), 0.95, 0.75, _lognormal(rng, 8, 0.5)),  # prueba antes de acertar
        ],
    }


def simulate_events(
    n_events: int = 20_000,
    brute_force_ratio: float = 0.06,
    takeover_ratio: float = 0.04,
    seed: int = 42,
) -> pd.DataFrame:
    """Genera eventos de login etiquetados, mezclados al azar.

    Devuelve las 4 features más `attack_type` y `label` (0 normal, 1 ataque).
    """
    if n_events <= 0:
        raise ValueError("n_events debe ser mayor que 0")
    if brute_force_ratio < 0 or takeover_ratio < 0 or brute_force_ratio + takeover_ratio >= 1:
        raise ValueError("Las proporciones de ataque deben ser >= 0 y sumar menos de 1")

    rng = np.random.default_rng(seed)
    counts = rng.multinomial(
        n_events, [1 - brute_force_ratio - takeover_ratio, brute_force_ratio, takeover_ratio]
    )

    segments = []
    profiles = _profiles(rng)
    for attack_type, n_class in zip(ATTACK_TYPES, counts):
        weights = [p[0] for p in profiles[attack_type]]
        for (_, failed, p_new_ip, p_unusual, rpm), n in zip(
            profiles[attack_type], rng.multinomial(n_class, weights)
        ):
            segments.append(
                pd.DataFrame(
                    {
                        "failed_logins_5m": failed(n),
                        "is_new_ip": rng.binomial(1, p_new_ip, n),
                        "unusual_hour": rng.binomial(1, p_unusual, n),
                        "requests_per_minute": rpm(n).round(2),
                        "attack_type": attack_type,
                    }
                )
            )

    df = pd.concat(segments, ignore_index=True)
    df["label"] = (df["attack_type"] != "normal").astype(int)
    return df.sample(frac=1, random_state=seed).reset_index(drop=True)


def validate(events: pd.DataFrame) -> pd.DataFrame:
    """Comprueba el esquema y devuelve solo las features, como float32.

    Un evento inválido se rechaza con ValueError: nunca se puntúa en silencio.
    """
    missing = [c for c in FEATURES if c not in events.columns]
    if missing:
        raise ValueError(f"Faltan columnas: {missing}")

    X = events[FEATURES].apply(pd.to_numeric, errors="coerce")
    if X.isna().any().any():
        raise ValueError("Hay valores vacíos o no numéricos en las features")
    if (X < 0).any().any():
        raise ValueError("Las features no pueden ser negativas")
    not_binary = [c for c in BINARY_FEATURES if not X[c].isin([0, 1]).all()]
    if not_binary:
        raise ValueError(f"{not_binary} solo admiten 0 o 1")
    return X.astype("float32")


def _log_continuous(X: pd.DataFrame) -> np.ndarray:
    # Fallos y peticiones/min tienen colas largas: log1p las acerca a una normal.
    return np.log1p(X[CONTINUOUS_FEATURES].to_numpy())


def fit_scaler(X: pd.DataFrame) -> StandardScaler:
    """Ajusta el escalado con las features ya validadas del conjunto de entrenamiento."""
    return StandardScaler().fit(_log_continuous(X))


def transform(X: pd.DataFrame, scaler: StandardScaler) -> np.ndarray:
    """Vector listo para los modelos: binarias intactas, continuas en log + estandarizadas."""
    out = X[FEATURES].to_numpy(dtype="float32", copy=True)
    cols = [FEATURES.index(c) for c in CONTINUOUS_FEATURES]
    out[:, cols] = scaler.transform(_log_continuous(X))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera el dataset sintético de logins.")
    parser.add_argument("--events", type=int, default=20_000)
    parser.add_argument("--brute-force-ratio", type=float, default=0.06)
    parser.add_argument("--takeover-ratio", type=float, default=0.04)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=DATA_PATH)
    args = parser.parse_args()

    df = simulate_events(args.events, args.brute_force_ratio, args.takeover_ratio, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"{len(df)} eventos guardados en {args.out}")
    print(df["attack_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
