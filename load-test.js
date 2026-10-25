import http from 'k6/http';
import { check, sleep } from 'k6';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

export const options = {
  // Налаштування Thresholds (Acceptance criteria)
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% запитів мають виконуватись швидше 500мс
    http_req_failed: ['rate<0.05'],   // Рівень помилок має бути меншим за 5%
  },
  // Workload profile: тут налаштовано Load test (можна змінювати для Baseline або Stress)
  stages: [
    { duration: '10s', target: 20 }, // Плавний розгін до 20 потоків (Ramp-up)
    { duration: '30s', target: 20 }, // Стабільне навантаження 20 потоків
    { duration: '10s', target: 0 },  // Плавне завершення
  ],
};

const BASE_URL = 'http://127.0.0.1:8080';

export default function () {
  const headers = { 'Content-Type': 'application/json' };

  // 1. Створення клієнта
  const customerRes = http.post(`${BASE_URL}/customers`, JSON.stringify({
    code: `C-${uuidv4()}`,
    regular: false
  }), { headers });

  check(customerRes, { 'Customer created 201': (r) => r.status === 201 });
  const customerId = customerRes.json('id');

  // 2. Створення посилки
  const parcelRes = http.post(`${BASE_URL}/parcels`, JSON.stringify({
    code: `P-${uuidv4()}`,
    weight_grams: 1000,
    declared_value: 10000
  }), { headers });

  check(parcelRes, { 'Parcel created 201': (r) => r.status === 201 });
  const parcelId = parcelRes.json('id');

  // 3. Оформлення відправлення
  if (customerId && parcelId) {
    const shipmentRes = http.post(`${BASE_URL}/shipments`, JSON.stringify({
      reference: `S-${uuidv4()}`,
      customer_id: customerId,
      parcel_id: parcelId,
      distance_km: 100,
      urgent: false,
      pickup: false,
      coupon_valid: false
    }), { headers });

    check(shipmentRes, { 'Shipment created 201': (r) => r.status === 201 });
  }

  // Пауза між ітераціями віртуального користувача
  sleep(1);
}