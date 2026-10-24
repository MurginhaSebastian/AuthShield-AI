import pytest
from fastapi.testclient import TestClient

from app.api import API_KEYS_ENV, app, load_api_keys

NORMAL = {"failed_logins_5m": 0, "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": 2.0}
KEY_A, KEY_B = "a" * 32, "b" * 40


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(API_KEYS_ENV, f"{KEY_A}, {KEY_B}")
        with TestClient(app) as c:  # el `with` ejecuta el lifespan y carga los modelos
            yield c


@pytest.mark.parametrize("value", ["", " , ", "corta", f"{KEY_A},corta"])
def test_claves_ausentes_o_cortas_impiden_arrancar(monkeypatch, value):
    monkeypatch.setenv(API_KEYS_ENV, value)
    with pytest.raises(RuntimeError):
        load_api_keys()


def test_sin_variable_de_claves_la_api_no_arranca(monkeypatch):
    monkeypatch.delenv(API_KEYS_ENV, raising=False)
    with pytest.raises(RuntimeError), TestClient(app):
        pass


def test_varias_claves_para_rotacion(monkeypatch):
    monkeypatch.setenv(API_KEYS_ENV, f" {KEY_A} ,{KEY_B},")
    assert load_api_keys() == [KEY_A, KEY_B]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_evento_normal(client):
    body = client.post("/evaluate", json=NORMAL).json()
    assert body["verdict"] == "NORMAL"
    assert 0 <= body["p_rf"] <= 1 and 0 <= body["p_nn"] <= 1
    assert body["latency_ms"] < 50


def test_fuerza_bruta(client):
    attack = NORMAL | {"failed_logins_5m": 40, "is_new_ip": 1, "requests_per_minute": 150.0}
    assert client.post("/evaluate", json=attack).json()["verdict"] in {"FUERZA BRUTA", "ALERTA CRÍTICA"}


@pytest.mark.parametrize(
    "body",
    [
        NORMAL | {"is_new_ip": 2},
        NORMAL | {"failed_logins_5m": -1},
        NORMAL | {"requests_per_minute": "rápido"},
        NORMAL | {"user": "admin"},
        {k: v for k, v in NORMAL.items() if k != "unusual_hour"},
    ],
)
def test_rechaza_eventos_invalidos(client, body):
    assert client.post("/evaluate", json=body).status_code == 422


@pytest.mark.parametrize(
    "raw",
    [
        '{"failed_logins_5m": 0, "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": Infinity}',
        '{"failed_logins_5m": 0, "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": 1e309}',
        '{"failed_logins_5m": 1' + "0" * 40 + ', "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": 1}',
    ],
)
def test_rechaza_valores_no_finitos(client, raw):
    response = client.post("/evaluate", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
