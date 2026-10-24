"""Entrenamiento del Random Forest (fuerza bruta) y la red neuronal (identidad).

Calibra los umbrales, evalúa sobre el conjunto de prueba y guarda los artefactos en models/.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    fbeta_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight
from tensorflow import keras

from src.pipeline import (
    CONTINUOUS_FEATURES,
    DATA_PATH,
    FEATURES,
    MODELS_DIR,
    fit_scaler,
    load_artifacts,
    score,
    transform,
    validate,
)

MIN_RECALL = 0.95


def split(df: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """60/20/20 estratificado por tipo de ataque: entrenamiento, validación y prueba."""
    train, rest = train_test_split(df, test_size=0.4, stratify=df["attack_type"], random_state=seed)
    val, test = train_test_split(rest, test_size=0.5, stratify=rest["attack_type"], random_state=seed)
    return train, val, test


def best_threshold(y_true: np.ndarray, proba: np.ndarray, min_recall: float = MIN_RECALL) -> float:
    """Umbral que maximiza F2 sujeto a Recall >= min_recall (README, 5.4).

    Si ningún umbral alcanza ese Recall, se queda con el de mejor F2.
    """
    precision, recall, thresholds = precision_recall_curve(y_true, proba)
    precision, recall = precision[:-1], recall[:-1]  # el último punto no tiene umbral
    f2 = np.divide(
        5 * precision * recall,
        4 * precision + recall,
        out=np.zeros_like(precision),
        where=(4 * precision + recall) > 0,
    )
    feasible = recall >= min_recall
    if feasible.any():
        f2 = np.where(feasible, f2, -1)
    return float(thresholds[np.argmax(f2)])


def evaluate(y_true: np.ndarray, proba: np.ndarray, y_pred: np.ndarray) -> dict:
    """Métricas de la sección 5: duras con y_pred, PR-AUC con la probabilidad."""
    y_pred = y_pred.astype(int)
    return {
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred)), 4),
        "f1": round(float(f1_score(y_true, y_pred)), 4),
        "f2": round(float(fbeta_score(y_true, y_pred, beta=2)), 4),
        "pr_auc": round(float(average_precision_score(y_true, proba)), 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
    }


def train_rf(X: np.ndarray, y: np.ndarray, seed: int) -> RandomForestClassifier:
    rf = RandomForestClassifier(
        n_estimators=150,
        max_depth=12,
        min_samples_leaf=2,
        class_weight="balanced",
        n_jobs=-1,
        random_state=seed,
    )
    rf.fit(X, y)
    # Para un solo evento, repartir los árboles entre hilos cuesta más de lo que aporta.
    return rf.set_params(n_jobs=1)


def train_nn(
    X: np.ndarray, y: np.ndarray, X_val: np.ndarray, y_val: np.ndarray, seed: int
) -> keras.Model:
    keras.utils.set_random_seed(seed)
    model = keras.Sequential(
        [
            keras.Input(shape=(len(FEATURES),)),
            keras.layers.Dense(32, activation="relu"),
            keras.layers.Dropout(0.2),
            keras.layers.Dense(16, activation="relu"),
            keras.layers.Dense(1, activation="sigmoid"),
        ]
    )
    model.compile(optimizer="adam", loss="binary_crossentropy")
    weights = compute_class_weight("balanced", classes=np.array([0, 1]), y=y)
    model.fit(
        X,
        y,
        validation_data=(X_val, y_val),
        epochs=100,
        batch_size=256,
        class_weight=dict(enumerate(weights)),
        callbacks=[keras.callbacks.EarlyStopping(patience=8, restore_best_weights=True)],
        verbose=0,
    )
    return model


def nn_proba(model: keras.Model, X: np.ndarray) -> np.ndarray:
    return model.predict(X, batch_size=4096, verbose=0).ravel()


def measure_latency(events: pd.DataFrame, models_dir: Path, n: int = 1000) -> dict:
    """Latencia de un evento de punta a punta (validar, escalar, ambos modelos, regla)."""
    artifacts = load_artifacts(models_dir)
    rows = [events.iloc[[i % len(events)]] for i in range(n)]
    for row in rows[:20]:  # calentamiento: la primera llamada de Keras traza el grafo
        score(row, artifacts)
    times = []
    for row in rows:
        start = time.perf_counter()
        score(row, artifacts)
        times.append((time.perf_counter() - start) * 1000)
    return {f"p{q}": round(float(np.percentile(times, q)), 2) for q in (50, 95, 99)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrena los modelos de AuthShield-AI.")
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.data.exists():
        raise SystemExit(f"No existe {args.data}. Genéralo con: python -m src.pipeline")

    df = pd.read_csv(args.data)
    missing = {"normal", "brute_force", "account_takeover"} - set(df["attack_type"])
    if missing:
        raise SystemExit(f"El dataset no tiene eventos de tipo {sorted(missing)}: cada modelo necesita su clase.")
    train, val, test = split(df, args.seed)
    X_train, X_val, X_test = validate(train), validate(val), validate(test)
    scaler = fit_scaler(X_train)
    Xs_train, Xs_val, Xs_test = (transform(X, scaler) for X in (X_train, X_val, X_test))
    parts = {"train": train, "val": val, "test": test}

    def target(attack_type: str) -> dict[str, np.ndarray]:
        return {k: (p["attack_type"] == attack_type).to_numpy(int) for k, p in parts.items()}

    is_bf, is_ato = target("brute_force"), target("account_takeover")

    rf = train_rf(Xs_train, is_bf["train"], args.seed)
    nn = train_nn(Xs_train, is_ato["train"], Xs_val, is_ato["val"], args.seed)
    # Referencia: ¿aporta la red neuronal frente a un RF en la misma tarea? (README, 7.3)
    rf_ato = train_rf(Xs_train, is_ato["train"], args.seed)

    thresholds = {
        "rf": best_threshold(is_bf["val"], rf.predict_proba(Xs_val)[:, 1]),
        "nn": best_threshold(is_ato["val"], nn_proba(nn, Xs_val)),
    }
    t_rf_ato = best_threshold(is_ato["val"], rf_ato.predict_proba(Xs_val)[:, 1])
    p_rf, p_nn = rf.predict_proba(Xs_test)[:, 1], nn_proba(nn, Xs_test)
    p_rf_ato = rf_ato.predict_proba(Xs_test)[:, 1]
    rf_hit, nn_hit = p_rf >= thresholds["rf"], p_nn >= thresholds["nn"]

    metadata = {
        "thresholds": {k: round(v, 4) for k, v in thresholds.items()},
        "metrics": {
            "random_forest": evaluate(is_bf["test"], p_rf, rf_hit),
            "neural_network": evaluate(is_ato["test"], p_nn, nn_hit),
            "baseline_rf_identity": evaluate(is_ato["test"], p_rf_ato, p_rf_ato >= t_rf_ato),
            # El combinado no tiene probabilidad propia: su PR-AUC usa la mayor de las dos.
            "combined": evaluate(test["label"].to_numpy(), np.maximum(p_rf, p_nn), rf_hit | nn_hit),
        },
        "feature_importance": {
            f: round(float(v), 4) for f, v in zip(FEATURES, rf.feature_importances_)
        },
        "normal_p95": {
            f: round(float(X_train.loc[train["label"] == 0, f].quantile(0.95)), 2)
            for f in CONTINUOUS_FEATURES
        },
        "seed": args.seed,
        "n_events": len(df),
        "tensorflow": tf.__version__,
    }

    args.models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(rf, args.models_dir / "ml_auth_model.pkl")
    joblib.dump(scaler, args.models_dir / "scaler.pkl")
    nn.save(args.models_dir / "dl_auth_model.h5")
    (args.models_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    metadata["latency_ms"] = measure_latency(test, args.models_dir)
    (args.models_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({k: metadata[k] for k in ("thresholds", "latency_ms")}, indent=2))
    for name, m in metadata["metrics"].items():
        print(f"{name:22} P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} F2={m['f2']:.3f}")


if __name__ == "__main__":
    main()
