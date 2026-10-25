import os
import json
import time

import pytest
import urllib.request
import psycopg
from testcontainers.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from delivery.database import connect
from delivery.models import Customer, Parcel, ShipmentRequest, ShipmentStatus
from delivery.repositories import PostgresCustomerRepository, PostgresParcelRepository, PostgresShipmentRepository
from delivery.service import ShippingService, ShipmentRejectedError
from delivery.http_shipping_policy import HttpShippingPolicy, ShippingPolicyError


# ==========================================
# 1. НАЛАШТУВАННЯ ТЕСТОВОГО СЕРЕДОВИЩА (FIXTURES)
# ==========================================

@pytest.fixture(scope="session")
def postgres_container():
    """Запускає PostgreSQL у контейнері та автоматично застосовує міграції."""
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


@pytest.fixture(scope="session")
def wiremock_container():
    """Запускає WireMock у контейнері для емуляції зовнішнього HTTP-сервісу та чекає на його старт."""
    with DockerContainer("wiremock/wiremock:latest").with_exposed_ports(8080) as wiremock:
        wm_ip = wiremock.get_container_host_ip()
        wm_port = wiremock.get_exposed_port(8080)
        health_url = f"http://{wm_ip}:{wm_port}/__admin/mappings"

        ready = False
        for _ in range(30):
            try:
                urllib.request.urlopen(health_url, timeout=1)
                ready = True
                break
            except Exception:
                time.sleep(0.5)

        if not ready:
            raise RuntimeError("WireMock не встиг запуститися вчасно")

        yield wiremock


@pytest.fixture(autouse=True)
def isolated_env(postgres_container, wiremock_container, monkeypatch):
    """
    Забезпечує ізоляцію: підміняє env-змінні для підключення до БД,
    очищає таблиці та скидає стан WireMock перед КОЖНИМ тестом.
    """
    # Налаштовуємо динамічні параметри БД для поточного контейнера
    monkeypatch.setenv("DB_HOST", postgres_container.get_container_host_ip())
    monkeypatch.setenv("DB_PORT", str(postgres_container.get_exposed_port(5432)))
    monkeypatch.setenv("DB_NAME", postgres_container.dbname)
    monkeypatch.setenv("DB_USER", postgres_container.username)
    monkeypatch.setenv("DB_PASSWORD", postgres_container.password)

    with connect() as conn:
        conn.execute("TRUNCATE shipments, parcels, customers RESTART IDENTITY;")

    # Формування базового URL WireMock
    wm_ip = wiremock_container.get_container_host_ip()
    wm_port = wiremock_container.get_exposed_port(8080)
    wm_url = f"http://{wm_ip}:{wm_port}"

    reset_req = urllib.request.Request(f"{wm_url}/__admin/reset", method="POST")
    urllib.request.urlopen(reset_req)

    yield wm_url


# ==========================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ WIREMOCK
# ==========================================

def set_wiremock_stub(base_url, http_status, response_body=None):
    """Налаштовує відповідь зовнішнього сервісу."""
    mapping = {
        "request": {"method": "POST", "url": "/shipping-policy"},
        "response": {"status": http_status, "headers": {"Content-Type": "application/json"}}
    }
    if response_body is not None:
        mapping["response"]["body"] = json.dumps(response_body)

    req = urllib.request.Request(f"{base_url}/__admin/mappings",
                                 data=json.dumps(mapping).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    urllib.request.urlopen(req)


def get_wiremock_requests(base_url):
    """Отримує всі фактичні HTTP-запити, які надійшли до WireMock."""
    req = urllib.request.Request(f"{base_url}/__admin/requests", method="GET")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


# ==========================================
# 3. ТЕСТОВІ СЦЕНАРІЇ
# ==========================================

def test_positive_business_scenario_with_discount(isolated_env):
    """3.5. Позитивний бізнес-сценарій. Очікується успішне збереження та 1 HTTP-запит."""
    wm_url = isolated_env
    set_wiremock_stub(wm_url, 200, {"allowed": True})

    with connect() as db:
        customers = PostgresCustomerRepository(db)
        parcels = PostgresParcelRepository(db)
        shipments = PostgresShipmentRepository(db)
        policy = HttpShippingPolicy(wm_url, timeout=2)
        service = ShippingService(customers, parcels, shipments, policy)

        customer = customers.create(Customer(id=0, code="CUST-POS", regular=True))
        parcel = parcels.create(Parcel(id=0, code="PARC-POS", weight_grams=1500, declared_value=5000))

        # Виконання (coupon_valid=True для перевірки знижки)
        request = ShipmentRequest(reference="REF-POS", customer_id=customer.id, parcel_id=parcel.id, distance_km=100,
                                  coupon_valid=True)
        shipment = service.create_shipment(request)

        db_shipment = shipments.find_by_id(shipment.id)
        assert db_shipment is not None
        assert db_shipment.status == ShipmentStatus.REGISTERED

        # Перевірка персонального правила (Варіант 10: 15% знижка):
        # 1500г, 100км -> base = 5000 + 2*1000 + 1000 = 8000 коп. Знижка 15% = -1200. Total = 6800.
        assert db_shipment.base_fee == 8000
        assert db_shipment.adjustment == -1200
        assert db_shipment.total == 6800

    requests_data = get_wiremock_requests(wm_url)["requests"]
    assert len(requests_data) == 1

    actual_request = requests_data[0]["request"]
    assert actual_request["method"] == "POST"
    assert actual_request["url"] == "/shipping-policy"

    body = json.loads(actual_request["body"])
    assert body["customerId"] == customer.id
    assert body["parcelId"] == parcel.id
    assert body["distanceKm"] == 100


def test_external_rejection_scenario(isolated_env):
    """3.6. Сценарій зовнішньої бізнес-відмови (allowed=false)."""
    wm_url = isolated_env
    set_wiremock_stub(wm_url, 200, {"allowed": False})

    with connect() as db:
        customers = PostgresCustomerRepository(db)
        parcels = PostgresParcelRepository(db)
        shipments = PostgresShipmentRepository(db)
        service = ShippingService(customers, parcels, shipments, HttpShippingPolicy(wm_url, timeout=2))

        customer = customers.create(Customer(id=0, code="CUST-REJ", regular=True))
        parcel = parcels.create(Parcel(id=0, code="PARC-REJ", weight_grams=1500, declared_value=5000))

        request = ShipmentRequest(reference="REF-REJ", customer_id=customer.id, parcel_id=parcel.id, distance_km=100)

        with pytest.raises(ShipmentRejectedError, match="Доставку не дозволено"):
            service.create_shipment(request)

        # Перевірка відсутності небажаного запису в БД
        assert len(shipments.find_shipments(customer.id, ShipmentStatus.REGISTERED)) == 0

    # HTTP-запит ДО WireMock МАВ БУТИ виконаний
    assert len(get_wiremock_requests(wm_url)["requests"]) == 1


def test_service_unavailable_scenario(isolated_env):
    """3.7. Сценарій недоступності зовнішнього service (Помилка 503)."""
    wm_url = isolated_env
    set_wiremock_stub(wm_url, 503)

    with connect() as db:
        customers = PostgresCustomerRepository(db)
        parcels = PostgresParcelRepository(db)
        shipments = PostgresShipmentRepository(db)
        service = ShippingService(customers, parcels, shipments, HttpShippingPolicy(wm_url, timeout=2))

        customer = customers.create(Customer(id=0, code="CUST-ERR", regular=True))
        parcel = parcels.create(Parcel(id=0, code="PARC-ERR", weight_grams=1500, declared_value=5000))

        request = ShipmentRequest(reference="REF-ERR", customer_id=customer.id, parcel_id=parcel.id, distance_km=100)

        with pytest.raises(ShippingPolicyError, match="Помилка HTTP-політики: 503"):
            service.create_shipment(request)

        # База даних залишається у коректному стані (без відправлення)
        assert len(shipments.find_shipments(customer.id, ShipmentStatus.REGISTERED)) == 0

    # HTTP-запит був виконаний
    assert len(get_wiremock_requests(wm_url)["requests"]) == 1


def test_local_business_rejection_scenario(isolated_env):
    """3.8. Локальна відмова. Відхиляється до звернення до WireMock."""
    wm_url = isolated_env

    with connect() as db:
        customers = PostgresCustomerRepository(db)
        parcels = PostgresParcelRepository(db)
        shipments = PostgresShipmentRepository(db)
        service = ShippingService(customers, parcels, shipments, HttpShippingPolicy(wm_url, timeout=2))

        customer = customers.create(Customer(id=0, code="CUST-LOC", regular=True))
        parcel = parcels.create(Parcel(id=0, code="PARC-LOC", weight_grams=1500, declared_value=5000))

        # Перевищення ліміту дистанції (допустимо 1-5000 згідно README), викликає ValueError
        request = ShipmentRequest(reference="REF-LOC", customer_id=customer.id, parcel_id=parcel.id, distance_km=6000)

        with pytest.raises(ValueError, match="Ціле число поза допустимими межами"):
            service.create_shipment(request)

        # У базу нічого не записалось
        assert len(shipments.find_shipments(customer.id, ShipmentStatus.REGISTERED)) == 0

    # HTTP-запит НЕ БУВ виконаний (0 запитів)
    assert len(get_wiremock_requests(wm_url)["requests"]) == 0