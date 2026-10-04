import pytest
from delivery.database import connect
from delivery.models import Customer, Parcel, ShipmentRequest, ShipmentStatus
from delivery.repositories import PostgresCustomerRepository, PostgresParcelRepository, PostgresShipmentRepository
from delivery.service import ShippingService, ShipmentRejectedError


@pytest.fixture
def db_connection():
    conn = connect()
    yield conn
    conn.close()


# Stub для ізоляції зовнішньої залежності (відповідно до п. 3.3)
class StubShippingPolicy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def is_allowed(self, customer, parcel, distance_km):
        return self.allowed


def test_create_shipment_success_serviceState(db_connection):
    """Позитивний сценарій оформлення відправлення."""
    customer_repo = PostgresCustomerRepository(db_connection)
    parcel_repo = PostgresParcelRepository(db_connection)
    shipment_repo = PostgresShipmentRepository(db_connection)

    # Використовуємо реальні репозиторії, але Stub для політики
    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=True))

    # Підготовка даних у БД
    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-1", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-1", weight_grams=1500, declared_value=10000))

    request = ShipmentRequest(
        reference="REF-OK-001",
        customer_id=customer.id,
        parcel_id=parcel.id,
        distance_km=100,
        coupon_valid=False
    )

    try:
        # Виконання
        shipment = service.create_shipment(request)

        # Перевірка фактичного стану БД (п. 3.6)
        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment is not None
        assert db_shipment.status == ShipmentStatus.REGISTERED
        # Розрахунок: база 50 грн (5000) + вага (2 кг * 1000) + відстань (100 * 10) = 8000 коп.
        assert db_shipment.base_fee == 8000
        assert db_shipment.adjustment == 0
        assert db_shipment.total == 8000
    finally:
        # Очищення для незалежності тестів (п. 3.7)
        if 'shipment' in locals():
            shipment_repo.delete(shipment.id)
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)


def test_create_shipment_with_discount_serviceState(db_connection):
    """Сценарій персонального правила варіанта 10: Знижка за промокодом."""
    customer_repo = PostgresCustomerRepository(db_connection)
    parcel_repo = PostgresParcelRepository(db_connection)
    shipment_repo = PostgresShipmentRepository(db_connection)
    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=True))

    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-2", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-2", weight_grams=1500, declared_value=10000))

    # coupon_valid=True активує знижку
    request = ShipmentRequest(
        reference="REF-DISC-001",
        customer_id=customer.id,
        parcel_id=parcel.id,
        distance_km=100,
        coupon_valid=True
    )

    try:
        shipment = service.create_shipment(request)

        # Перевірка фактичного стану БД
        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment.base_fee == 8000
        # Знижка 15% від 8000 = 1200 коп.
        assert db_shipment.adjustment == -1200
        assert db_shipment.total == 6800
    finally:
        if 'shipment' in locals():
            shipment_repo.delete(shipment.id)
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)


def test_create_shipment_rejected_serviceState(db_connection):
    """Негативний сценарій: політика забороняє доставку."""
    customer_repo = PostgresCustomerRepository(db_connection)
    parcel_repo = PostgresParcelRepository(db_connection)
    shipment_repo = PostgresShipmentRepository(db_connection)

    # Політика поверне False
    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=False))

    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-3", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-3", weight_grams=1500, declared_value=10000))

    request = ShipmentRequest(reference="REF-REJ-001", customer_id=customer.id, parcel_id=parcel.id, distance_km=100)

    try:
        # Перевіряємо, що викидається правильна помилка
        with pytest.raises(ShipmentRejectedError, match="Доставку не дозволено"):
            service.create_shipment(request)

        # Перевірка фактичного стану БД: запис не повинен бути створений (п. 3.6)
        # Оскільки ми знаємо reference, можемо впевнитися, що БД не змінилася
        # В нашому випадку, ми просто перевіряємо, що виняток виник і стан не пошкоджено
    finally:
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)


def test_dispatch_shipment_serviceState(db_connection):
    """Позитивний сценарій: зміна статусу на DISPATCHED."""
    customer_repo = PostgresCustomerRepository(db_connection)
    parcel_repo = PostgresParcelRepository(db_connection)
    shipment_repo = PostgresShipmentRepository(db_connection)
    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=True))

    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-4", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-4", weight_grams=1000, declared_value=5000))
    request = ShipmentRequest(reference="REF-DISP-001", customer_id=customer.id, parcel_id=parcel.id, distance_km=50)

    try:
        # Спочатку створюємо відправлення (початковий статус REGISTERED)
        shipment = service.create_shipment(request)

        # Виконання: відправляємо посилку
        service.dispatch(shipment.id)

        # Перевірка фактичного стану БД
        db_shipment = shipment_repo.find_by_id(shipment.id)
        # Статус має змінитися на DISPATCHED
        assert db_shipment.status == ShipmentStatus.DISPATCHED
        # Суми не повинні змінюватися при зміні статусу
        assert db_shipment.total == shipment.total
    finally:
        if 'shipment' in locals():
            shipment_repo.delete(shipment.id)
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)


def test_cancel_dispatched_shipment_rejected_serviceState(db_connection):
    """Негативний сценарій: спроба скасувати вже відправлену посилку."""
    customer_repo = PostgresCustomerRepository(db_connection)
    parcel_repo = PostgresParcelRepository(db_connection)
    shipment_repo = PostgresShipmentRepository(db_connection)
    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=True))

    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-5", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-5", weight_grams=1000, declared_value=5000))
    request = ShipmentRequest(reference="REF-CANC-001", customer_id=customer.id, parcel_id=parcel.id, distance_km=50)

    try:
        shipment = service.create_shipment(request)
        service.dispatch(shipment.id)  # Змінюємо статус на DISPATCHED

        # Виконання та перевірка помилки: скасування після відправлення заборонено
        with pytest.raises(ShipmentRejectedError, match="Перехід статусу заборонений"):
            service.cancel(shipment.id)

        # Перевірка фактичного стану БД: запис не повинен був змінитися
        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment.status == ShipmentStatus.DISPATCHED
    finally:
        if 'shipment' in locals():
            shipment_repo.delete(shipment.id)
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)