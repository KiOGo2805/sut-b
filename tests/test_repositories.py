import pytest
from delivery.database import connect
from delivery.models import Customer, Parcel
from delivery.repositories import PostgresCustomerRepository, PostgresParcelRepository


@pytest.fixture
def db_connection():
    conn = connect()
    yield conn
    conn.close()


def test_customer_create_and_find_repositoryState(db_connection):
    """
    Перевіряє створення клієнта та його повторне отримання з БД.
    """
    repo = PostgresCustomerRepository(db_connection)
    new_customer = Customer(id=0, code="TEST-CUST-01", regular=True)

    created = repo.create(new_customer)

    try:
        assert created.id > 0

        found = repo.find_by_id(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.code == "TEST-CUST-01"
        assert found.regular is True
    finally:
        repo.delete(created.id)


def test_parcel_update_repositoryState(db_connection):
    """
    Перевіряє оновлення даних існуючої посилки.
    """
    repo = PostgresParcelRepository(db_connection)
    parcel = Parcel(id=0, code="TEST-PARCEL-01", weight_grams=1500, declared_value=5000)
    created = repo.create(parcel)

    try:
        updated_parcel = Parcel(id=created.id, code="TEST-PARCEL-01-UPD", weight_grams=2500, declared_value=7000)

        repo.update(updated_parcel)

        found = repo.find_by_id(created.id)
        assert found.code == "TEST-PARCEL-01-UPD"
        assert found.weight_grams == 2500
        assert found.declared_value == 7000
    finally:
        repo.delete(created.id)


def test_customer_delete_repositoryState(db_connection):
    """
    Перевіряє видалення клієнта з БД.
    """
    repo = PostgresCustomerRepository(db_connection)
    customer = Customer(id=0, code="TEST-CUST-DEL", regular=False)
    created = repo.create(customer)

    repo.delete(created.id)

    found = repo.find_by_id(created.id)
    assert found is None