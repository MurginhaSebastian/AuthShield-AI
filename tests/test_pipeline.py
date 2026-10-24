import numpy as np
import pandas as pd
import pytest

from src.pipeline import (
    CONTINUOUS_FEATURES,
    FEATURES,
    fit_scaler,
    simulate_events,
    transform,
    validate,
    verdicts,
)
from src.train import best_threshold


@pytest.fixture(scope="module")
def events() -> pd.DataFrame:
    return simulate_events(5000, brute_force_ratio=0.1, takeover_ratio=0.1, seed=1)


def test_simulacion_reproducible_y_etiquetada(events):
    assert events.equals(simulate_events(5000, brute_force_ratio=0.1, takeover_ratio=0.1, seed=1))
    assert list(events.columns) == [*FEATURES, "attack_type", "label"]
    assert (events["label"] == (events["attack_type"] != "normal")).all()
    shares = events["attack_type"].value_counts(normalize=True)
    assert shares["brute_force"] == pytest.approx(0.1, abs=0.02)
    assert shares["account_takeover"] == pytest.approx(0.1, abs=0.02)


def test_simulacion_rechaza_proporciones_imposibles():
    with pytest.raises(ValueError):
        simulate_events(100, brute_force_ratio=0.6, takeover_ratio=0.5)


@pytest.mark.parametrize(
    "change",
    [
        {"is_new_ip": 2},
        {"unusual_hour": 0.5},
        {"failed_logins_5m": -1},
        {"requests_per_minute": "rápido"},
        {"requests_per_minute": np.nan},
    ],
)
def test_validate_rechaza_valores_invalidos(events, change):
    with pytest.raises(ValueError):
        validate(events.head(3).assign(**change))


def test_validate_rechaza_columnas_faltantes(events):
    with pytest.raises(ValueError, match="unusual_hour"):
        validate(events.drop(columns="unusual_hour"))


def test_transform_escala_continuas_y_respeta_binarias(events):
    X = validate(events)
    out = transform(X, fit_scaler(X))
    cont = [FEATURES.index(c) for c in CONTINUOUS_FEATURES]
    assert out.shape == (len(events), len(FEATURES))
    assert np.allclose(out[:, cont].mean(axis=0), 0, atol=1e-5)
    assert np.array_equal(out[:, FEATURES.index("is_new_ip")], X["is_new_ip"].to_numpy())


def test_regla_de_decision():
    rf_hit = np.array([True, True, False, False])
    nn_hit = np.array([True, False, True, False])
    assert verdicts(rf_hit, nn_hit) == ["ALERTA CRÍTICA", "FUERZA BRUTA", "ANOMALÍA DE IDENTIDAD", "NORMAL"]


def test_umbral_respeta_recall_minimo():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 2000)
    proba = np.clip(y * 0.3 + rng.normal(0.35, 0.2, 2000), 0, 1)
    t = best_threshold(y, proba, min_recall=0.95)
    assert ((proba >= t) & (y == 1)).sum() / y.sum() >= 0.95
