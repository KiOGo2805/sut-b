import pytest
from delivery.database import connect
from delivery.models import Customer, Parcel
from delivery.repositories import PostgresCustomerRepository, PostgresParcelRepository


# Фікстура для підключення до тестової БД (виконується для кожного тесту)
@pytest.fixture
def db_connection():
    conn = connect()
    yield conn
    conn.close()  # Обов'язково закриваємо з'єднання після тесту


def test_customer_create_and_find_repositoryState(db_connection):
    """
    Перевіряє створення клієнта та його повторне отримання з БД.
    """
    repo = PostgresCustomerRepository(db_connection)
    new_customer = Customer(id=0, code="TEST-CUST-01", regular=True)

    # Виконання: створюємо запис у БД
    created = repo.create(new_customer)

    try:
        # Перевірка 1: БД згенерувала новий ID
        assert created.id > 0

        # Перевірка 2: Фактичний стан БД (дістаємо запис наново)
        found = repo.find_by_id(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.code == "TEST-CUST-01"
        assert found.regular is True
    finally:
        # Очищення даних для незалежності тестів (п. 3.7)
        repo.delete(created.id)


def test_parcel_update_repositoryState(db_connection):
    """
    Перевіряє оновлення даних існуючої посилки.
    """
    repo = PostgresParcelRepository(db_connection)
    parcel = Parcel(id=0, code="TEST-PARCEL-01", weight_grams=1500, declared_value=5000)
    created = repo.create(parcel)

    try:
        # Змінюємо дані
        updated_parcel = Parcel(id=created.id, code="TEST-PARCEL-01-UPD", weight_grams=2500, declared_value=7000)

        # Виконання: оновлюємо запис у БД
        repo.update(updated_parcel)

        # Перевірка: Фактичний стан БД
        found = repo.find_by_id(created.id)
        assert found.code == "TEST-PARCEL-01-UPD"
        assert found.weight_grams == 2500
        assert found.declared_value == 7000
    finally:
        # Очищення даних
        repo.delete(created.id)


def test_customer_delete_repositoryState(db_connection):
    """
    Перевіряє видалення клієнта з БД.
    """
    repo = PostgresCustomerRepository(db_connection)
    customer = Customer(id=0, code="TEST-CUST-DEL", regular=False)
    created = repo.create(customer)

    # Виконання: видаляємо запис
    repo.delete(created.id)

    # Перевірка: Фактичний стан БД (запис відсутній)
    found = repo.find_by_id(created.id)
    assert found is None