import pytest
from fastapi.testclient import TestClient

from app.api import API_KEYS_ENV, RATE_LIMIT_ENV, RateLimiter, app, load_api_keys, load_rate_limit

NORMAL = {"failed_logins_5m": 0, "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": 2.0}
KEY_A, KEY_B = "a" * 32, "b" * 40
AUTH = {"X-API-Key": KEY_A}


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(API_KEYS_ENV, f"{KEY_A}, {KEY_B}")
        mp.setenv(RATE_LIMIT_ENV, "1000")  # alto: el resto de pruebas no debe tropezar con el límite
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
    body = client.post("/evaluate", json=NORMAL, headers=AUTH).json()
    assert body["verdict"] == "NORMAL"
    assert 0 <= body["p_rf"] <= 1 and 0 <= body["p_nn"] <= 1
    assert body["latency_ms"] < 50


def test_fuerza_bruta(client):
    attack = NORMAL | {"failed_logins_5m": 40, "is_new_ip": 1, "requests_per_minute": 150.0}
    assert client.post("/evaluate", json=attack, headers=AUTH).json()["verdict"] in {"FUERZA BRUTA", "ALERTA CRÍTICA"}


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
    assert client.post("/evaluate", json=body, headers=AUTH).status_code == 422


@pytest.mark.parametrize(
    "raw",
    [
        '{"failed_logins_5m": 0, "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": Infinity}',
        '{"failed_logins_5m": 0, "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": 1e309}',
        '{"failed_logins_5m": 1' + "0" * 40 + ', "is_new_ip": 0, "unusual_hour": 0, "requests_per_minute": 1}',
    ],
)
def test_rechaza_valores_no_finitos(client, raw):
    response = client.post("/evaluate", content=raw, headers=AUTH | {"Content-Type": "application/json"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": ""}, {"X-API-Key": "x" * 32}, {"X-API-Key": KEY_A[:-1]}, {"X-API-Key": "ñ".encode() * 32}],
)
def test_sin_clave_valida_responde_401(client, headers):
    response = client.post("/evaluate", json=NORMAL, headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "APIKey"


def test_las_dos_claves_configuradas_funcionan(client):
    for key in (KEY_A, KEY_B):
        assert client.post("/evaluate", json=NORMAL, headers={"X-API-Key": key}).status_code == 200


def test_sin_clave_responde_401_antes_de_validar_el_cuerpo(client):
    assert client.post("/evaluate", json={"basura": True}).status_code == 401


def test_health_no_requiere_clave(client):
    assert client.get("/health").status_code == 200


def test_ventana_deslizante():
    limiter = RateLimiter(3)
    assert [limiter.retry_after("k", t) for t in (0, 10, 20)] == [0, 0, 0]
    assert limiter.retry_after("k", 30) == 30  # el hueco se abre cuando caduca el de t=0
    assert limiter.retry_after("otra", 30) == 0  # cada clave tiene su propio contador
    assert limiter.retry_after("k", 60) == 0  # t=0 ya salió de la ventana


def test_429_con_retry_after_y_por_clave(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "rate_limiter", RateLimiter(2))
    b = {"X-API-Key": KEY_B}
    assert [client.post("/evaluate", json=NORMAL, headers=b).status_code for _ in range(2)] == [200, 200]
    blocked = client.post("/evaluate", json=NORMAL, headers=b)
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert client.post("/evaluate", json=NORMAL, headers=AUTH).status_code == 200


def test_clave_invalida_no_consume_limite(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "rate_limiter", RateLimiter(1))
    for _ in range(3):
        assert client.post("/evaluate", json=NORMAL, headers={"X-API-Key": "x" * 32}).status_code == 401
    assert client.post("/evaluate", json=NORMAL, headers=AUTH).status_code == 200


@pytest.mark.parametrize("value", ["0", "-5", "abc", "", "1.5"])
def test_limite_invalido_impide_arrancar(monkeypatch, value):
    monkeypatch.setenv(RATE_LIMIT_ENV, value)
    with pytest.raises(RuntimeError):
        load_rate_limit()


def test_limite_por_defecto(monkeypatch):
    monkeypatch.delenv(RATE_LIMIT_ENV, raising=False)
    assert load_rate_limit() == 60


def test_limite_exacto_con_peticiones_concurrentes():
    from concurrent.futures import ThreadPoolExecutor

    limiter = RateLimiter(10)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: limiter.retry_after("k", 0.0), range(200)))
    assert results.count(0) == 10
