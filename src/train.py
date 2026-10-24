"""Entrenamiento del Random Forest (fuerza bruta) con umbral calibrado y métricas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
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

from src.pipeline import DATA_PATH, MODELS_DIR, fit_scaler, transform, validate

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


def evaluate(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict:
    y_pred = (proba >= threshold).astype(int)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrena los modelos de AuthShield-AI.")
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.data.exists():
        raise SystemExit(f"No existe {args.data}. Genéralo con: python -m src.pipeline")

    df = pd.read_csv(args.data)
    train, val, test = split(df, args.seed)
    X_train, X_val, X_test = validate(train), validate(val), validate(test)
    scaler = fit_scaler(X_train)
    Xs_train, Xs_val, Xs_test = (transform(X, scaler) for X in (X_train, X_val, X_test))

    is_bf = {name: (part["attack_type"] == "brute_force").to_numpy(int)
             for name, part in (("train", train), ("val", val), ("test", test))}

    rf = train_rf(Xs_train, is_bf["train"], args.seed)
    threshold = best_threshold(is_bf["val"], rf.predict_proba(Xs_val)[:, 1])
    metrics = evaluate(is_bf["test"], rf.predict_proba(Xs_test)[:, 1], threshold)

    args.models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(rf, args.models_dir / "ml_auth_model.pkl")
    joblib.dump(scaler, args.models_dir / "scaler.pkl")
    print(json.dumps({"threshold": round(threshold, 4), "random_forest": metrics}, indent=2))


if __name__ == "__main__":
    main()
