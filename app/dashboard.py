"""Dashboard de simulación y alertas de AuthShield-AI (Streamlit)."""

import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# `streamlit run` solo añade app/ al path; la raíz hace falta para importar src.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import FEATURES, load_artifacts, score, simulate_events  # noqa: E402

ICONS = {
    "ALERTA CRÍTICA": "🔴",
    "FUERZA BRUTA": "🔴",
    "ANOMALÍA DE IDENTIDAD": "🟠",
    "NORMAL": "🟢",
}
LABELS = {
    "failed_logins_5m": "Fallos en 5 min",
    "is_new_ip": "IP nueva",
    "unusual_hour": "Hora inusual",
    "requests_per_minute": "Peticiones por minuto",
}

st.set_page_config(page_title="AuthShield-AI", page_icon="🛡️", layout="wide")


@st.cache_resource
def get_artifacts() -> dict:
    return load_artifacts()


def unusual_features(event: pd.Series, normal_p95: dict) -> list[str]:
    """Features por encima del p95 del tráfico normal (las binarias cuentan si valen 1)."""
    return [LABELS[f] for f in FEATURES if event[f] > normal_p95.get(f, 0)]


st.title("🛡️ AuthShield-AI")
st.caption("Detección híbrida de ataques en logins: Random Forest (fuerza bruta) + red neuronal (identidad).")

try:
    artifacts = get_artifacts()
except FileNotFoundError as exc:
    st.error(f"Faltan artefactos de modelo: {exc}. Entrena primero con `python -m src.train`.")
    st.stop()

meta = artifacts["metadata"]
tab_sim, tab_manual, tab_models = st.tabs(["Simulación de tráfico", "Evento manual", "Modelos"])

with st.sidebar:
    st.header("Simulación")
    n_events = st.slider("Eventos", 100, 5000, 1000, step=100)
    bf_pct = st.slider("% fuerza bruta", 0, 30, 6)
    ato_pct = st.slider("% robo de identidad", 0, 30, 4)
    seed = st.number_input("Semilla", min_value=0, value=7, step=1)
    run = st.button("Simular tráfico", type="primary", width="stretch")

if run:
    events = simulate_events(n_events, bf_pct / 100, ato_pct / 100, int(seed))
    start = time.perf_counter()
    results = events.join(score(events, artifacts))
    st.session_state["results"] = results
    st.session_state["batch_ms"] = (time.perf_counter() - start) * 1000

with tab_sim:
    results = st.session_state.get("results")
    if results is None:
        st.info("Configura la simulación en el panel lateral y pulsa **Simular tráfico**.")
    else:
        alerts = results[results["verdict"] != "NORMAL"]
        attacks = results["label"] == 1
        detected = attacks & (results["verdict"] != "NORMAL")
        missed = int((attacks & ~detected).sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Eventos analizados", len(results))
        c2.metric("Alertas", len(alerts))
        c3.metric("Ataques detectados", f"{int(detected.sum())} / {int(attacks.sum())}")
        c4.metric("Ataques no detectados", missed)
        st.caption(
            f"Lote evaluado en {st.session_state['batch_ms']:.0f} ms "
            f"({st.session_state['batch_ms'] / len(results):.3f} ms por evento)."
        )

        left, right = st.columns([1, 2])
        with left:
            st.subheader("Veredictos")
            counts = results["verdict"].value_counts().reindex(ICONS, fill_value=0)
            # Columnas explícitas: con la Series de value_counts() el gráfico salía vacío.
            chart = pd.DataFrame({"Veredicto": list(counts.index), "Eventos": counts.to_numpy()})
            st.bar_chart(chart, x="Veredicto", y="Eventos", horizontal=True, sort=False)
        with right:
            st.subheader("Alertas")
            shown = alerts.assign(
                veredicto=alerts["verdict"].map(lambda v: f"{ICONS[v]} {v}"),
                real=alerts["attack_type"],
            )[["veredicto", "real", *FEATURES, "p_rf", "p_nn"]]
            st.dataframe(
                shown.rename(columns=LABELS),
                width="stretch",
                height=360,
                column_config={
                    "p_rf": st.column_config.ProgressColumn("P(fuerza bruta)", min_value=0, max_value=1),
                    "p_nn": st.column_config.ProgressColumn("P(identidad)", min_value=0, max_value=1),
                },
            )

with tab_manual:
    st.subheader("Evalúa un login")
    c1, c2 = st.columns(2)
    event = pd.DataFrame(
        [
            {
                "failed_logins_5m": c1.number_input(LABELS["failed_logins_5m"], 0, 500, 0),
                "requests_per_minute": c1.number_input(LABELS["requests_per_minute"], 0.0, 1000.0, 2.0),
                "is_new_ip": int(c2.toggle(LABELS["is_new_ip"])),
                "unusual_hour": int(c2.toggle(LABELS["unusual_hour"])),
            }
        ]
    )
    start = time.perf_counter()
    result = score(event, artifacts).iloc[0]
    elapsed_ms = (time.perf_counter() - start) * 1000

    verdict = result["verdict"]
    message = f"{ICONS[verdict]} **{verdict}**"
    {"NORMAL": st.success, "ANOMALÍA DE IDENTIDAD": st.warning}.get(verdict, st.error)(message)

    m1, m2, m3 = st.columns(3)
    m1.metric("P(fuerza bruta) · RF", f"{result['p_rf']:.2f}", help=f"Umbral {meta['thresholds']['rf']:.2f}")
    m2.metric("P(identidad) · NN", f"{result['p_nn']:.2f}", help=f"Umbral {meta['thresholds']['nn']:.2f}")
    m3.metric("Latencia", f"{elapsed_ms:.1f} ms")

    flagged = unusual_features(event.iloc[0], meta["normal_p95"])
    st.write("**Señales fuera de lo normal:** " + (", ".join(flagged) if flagged else "ninguna"))

with tab_models:
    st.subheader("Métricas sobre el conjunto de prueba")
    names = {
        "random_forest": "Random Forest · fuerza bruta",
        "neural_network": "Red neuronal · identidad",
        "baseline_rf_identity": "RF de referencia · identidad",
        "combined": "Veredicto combinado · todos los ataques",
    }
    columns = {"precision": "Precision", "recall": "Recall", "f1": "F1", "f2": "F2", "pr_auc": "PR-AUC"}
    table = pd.DataFrame(
        {names[k]: {columns[m]: meta["metrics"][k][m] for m in columns} for k in names}
    ).T
    st.dataframe(table, width="stretch")

    lat = meta["latency_ms"]
    st.metric("Latencia p95 por evento", f"{lat['p95']:.1f} ms", help="Requisito RNF-01: < 50 ms")

    st.subheader("Importancia de variables · Random Forest")
    importance = pd.Series(meta["feature_importance"]).rename(index=LABELS).sort_values(ascending=False)
    st.bar_chart(importance, horizontal=True, sort=False)
