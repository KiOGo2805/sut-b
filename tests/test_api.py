import os
import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer
import psycopg

from delivery.api import app
from delivery.database import connect


# ==========================================
# ІЗОЛЬОВАНЕ СЕРЕДОВИЩЕ (TESTCONTAINERS)
# ==========================================

@pytest.fixture(scope="session")
def postgres_api_container():
    """Запускає PostgreSQL у контейнері для тестів API та застосовує міграції."""
    with PostgresContainer("postgres:17", driver=None) as postgres:
        with psycopg.connect(
                host=postgres.get_container_host_ip(),
                port=postgres.get_exposed_port(5432),
                user=postgres.username,
                password=postgres.password,
                dbname=postgres.dbname,
                autocommit=True
        ) as conn:
            migration_path = os.path.join(os.path.dirname(__file__), "..", "migrations", "V1__create_tables.sql")
            with open(migration_path, "r", encoding="utf-8") as f:
                conn.execute(f.read())
        yield postgres


@pytest.fixture(autouse=True)
def db_env(postgres_api_container, monkeypatch):
    """Підміняє змінні середовища та очищає БД перед кожним тестом."""
    monkeypatch.setenv("DB_HOST", postgres_api_container.get_container_host_ip())
    monkeypatch.setenv("DB_PORT", str(postgres_api_container.get_exposed_port(5432)))
    monkeypatch.setenv("DB_NAME", postgres_api_container.dbname)
    monkeypatch.setenv("DB_USER", postgres_api_container.username)
    monkeypatch.setenv("DB_PASSWORD", postgres_api_container.password)
    monkeypatch.setenv("SHIPPING_POLICY_URL", "")

    with connect() as conn:
        conn.execute("TRUNCATE shipments, parcels, customers RESTART IDENTITY;")


@pytest.fixture
def client():
    """Створює тестовий HTTP-клієнт для FastAPI."""
    return TestClient(app)


@pytest.fixture
def base_data(client):
    """Допоміжна фікстура для створення клієнта і посилки (потрібні для відправлень)."""
    c_res = client.post("/customers", json={"code": "CUST-API", "regular": True})
    p_res = client.post("/parcels", json={"code": "PARC-API", "weight_grams": 1000, "declared_value": 5000})
    return c_res.json()["id"], p_res.json()["id"]


# ==========================================
# ТЕСТОВІ СЦЕНАРІЇ ЗГІДНО ІЗ ЗАВДАННЯМ (LAB 6)
# ==========================================

def test_1_post_create_resource(client, base_data):
    """1. POST — створення ресурсу: статус 201, отримання id, перевірка основних полів."""
    c_id, p_id = base_data
    payload = {
        "reference": "REF-POST-01",
        "customer_id": c_id,
        "parcel_id": p_id,
        "distance_km": 100
    }
    response = client.post("/shipments", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["reference"] == "REF-POST-01"
    assert data["status"] == "Registered"
    assert response.headers["Location"] == f"/shipments/{data['id']}"


def test_2_get_by_id(client, base_data):
    """2. GET за id: статус 200, дані відповідають створеному ресурсу."""
    c_id, p_id = base_data
    post_res = client.post("/shipments", json={
        "reference": "REF-GET-02", "customer_id": c_id, "parcel_id": p_id, "distance_km": 100
    })
    created_id = post_res.json()["id"]

    response = client.get(f"/shipments/{created_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created_id
    assert data["reference"] == "REF-GET-02"
    assert data["status"] == "Registered"


def test_3_get_non_existent_resource(client):
    """3. GET неіснуючого ресурсу: статус 404, перевірка полів code і message."""
    response = client.get("/shipments/999999")

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"
    assert "не знайдено" in data["message"].lower()


def test_4_action_endpoint(client, base_data):
    """4. Action endpoint: статус 200, коректний новий стан ресурсу."""
    c_id, p_id = base_data
    post_res = client.post("/shipments", json={
        "reference": "REF-ACT-04", "customer_id": c_id, "parcel_id": p_id, "distance_km": 100
    })
    s_id = post_res.json()["id"]

    action_res = client.post(f"/shipments/{s_id}/dispatch")
    assert action_res.status_code == 200
    assert action_res.json()["status"] == "Dispatched"


def test_5_and_6_forbidden_action_and_immutability(client, base_data):
    """
    5. Повторна або заборонена дія: статус 409, перевірка error response.
    6. Незмінність після відхилення: наступний GET підтверджує, що ресурс не змінився.
    """
    c_id, p_id = base_data
    post_res = client.post("/shipments", json={
        "reference": "REF-FORBIDDEN-05", "customer_id": c_id, "parcel_id": p_id, "distance_km": 100
    })
    s_id = post_res.json()["id"]

    client.post(f"/shipments/{s_id}/cancel")

    # Спроба відправити вже скасоване (Cancelled -> Dispatched заборонено)
    bad_res = client.post(f"/shipments/{s_id}/dispatch")
    assert bad_res.status_code == 409
    assert bad_res.json()["code"] == "SHIPMENT_CONFLICT"
    assert "заборонений" in bad_res.json()["message"].lower()

    get_res = client.get(f"/shipments/{s_id}")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "Cancelled"


# 7. Персональне бізнес-правило (параметризація)
# Вага 1000г, Відстань 100км: Base = 5000 + 1000 + 1000 = 7000 коп.
# Промокод (15%): Знижка 1050 коп. Тотал = 5950 коп.
@pytest.mark.parametrize("is_coupon_valid, expected_adjustment, expected_total", [
    (True, -1050, 5950),  # З підтвердженим промокодом
    (False, 0, 7000)  # Без промокоду
])
def test_7_personal_rule_discount(client, base_data, is_coupon_valid, expected_adjustment, expected_total):
    """7. Персональне бізнес-правило: параметризована перевірка знижки за промокодом."""
    c_id, p_id = base_data
    payload = {
        "reference": f"REF-DISCOUNT-{is_coupon_valid}",
        "customer_id": c_id,
        "parcel_id": p_id,
        "distance_km": 100,
        "coupon_valid": is_coupon_valid
    }

    response = client.post("/shipments", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["base_fee"] == 7000
    assert data["adjustment"] == expected_adjustment
    assert data["total"] == expected_total


def test_8_workflow_lifecycle(client, base_data):
    """8. Workflow: POST -> GET -> action -> GET. Id ресурсу передається динамічно."""
    c_id, p_id = base_data

    # Крок 1: POST (створення)
    post_res = client.post("/shipments", json={
        "reference": "REF-WORKFLOW-08", "customer_id": c_id, "parcel_id": p_id, "distance_km": 50
    })
    assert post_res.status_code == 201

    dynamic_id = post_res.json()["id"]

    # Крок 2: GET (перевірка)
    get_res_1 = client.get(f"/shipments/{dynamic_id}")
    assert get_res_1.status_code == 200
    assert get_res_1.json()["status"] == "Registered"

    # Крок 3: ACTION (відправлення)
    act_res = client.post(f"/shipments/{dynamic_id}/dispatch")
    assert act_res.status_code == 200

    # Крок 4: GET (перевірка кінцевого стану)
    get_res_2 = client.get(f"/shipments/{dynamic_id}")
    assert get_res_2.status_code == 200
    assert get_res_2.json()["status"] == "Dispatched"