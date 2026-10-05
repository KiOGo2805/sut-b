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

    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=True))

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
        shipment = service.create_shipment(request)

        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment is not None
        assert db_shipment.status == ShipmentStatus.REGISTERED
        assert db_shipment.base_fee == 8000
        assert db_shipment.adjustment == 0
        assert db_shipment.total == 8000
    finally:
        if 'shipment' in locals():
            shipment_repo.delete(shipment.id)
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)


def test_create_shipment_with_discount_serviceState(db_connection):
    """Сценарій персонального правила: Знижка за промокодом."""
    customer_repo = PostgresCustomerRepository(db_connection)
    parcel_repo = PostgresParcelRepository(db_connection)
    shipment_repo = PostgresShipmentRepository(db_connection)
    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=True))

    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-2", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-2", weight_grams=1500, declared_value=10000))

    request = ShipmentRequest(
        reference="REF-DISC-001",
        customer_id=customer.id,
        parcel_id=parcel.id,
        distance_km=100,
        coupon_valid=True
    )

    try:
        shipment = service.create_shipment(request)

        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment.base_fee == 8000
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

    service = ShippingService(customer_repo, parcel_repo, shipment_repo, StubShippingPolicy(allowed=False))

    customer = customer_repo.create(Customer(id=0, code="SRV-CUST-3", regular=True))
    parcel = parcel_repo.create(Parcel(id=0, code="SRV-PARC-3", weight_grams=1500, declared_value=10000))

    request = ShipmentRequest(
        reference="REF-REJ-001",
        customer_id=customer.id,
        parcel_id=parcel.id,
        distance_km=100
    )

    try:
        with pytest.raises(ShipmentRejectedError, match="Доставку не дозволено"):
            service.create_shipment(request)

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
        shipment = service.create_shipment(request)

        service.dispatch(shipment.id)

        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment.status == ShipmentStatus.DISPATCHED
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
        service.dispatch(shipment.id)

        with pytest.raises(ShipmentRejectedError, match="Перехід статусу заборонений"):
            service.cancel(shipment.id)

        db_shipment = shipment_repo.find_by_id(shipment.id)
        assert db_shipment.status == ShipmentStatus.DISPATCHED
    finally:
        if 'shipment' in locals():
            shipment_repo.delete(shipment.id)
        parcel_repo.delete(parcel.id)
        customer_repo.delete(customer.id)