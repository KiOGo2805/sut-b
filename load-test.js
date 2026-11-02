import http from 'k6/http';
import { check, sleep } from 'k6';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

export const options = {
  // Thresholds (Acceptance criteria)
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    http_req_failed: ['rate<0.05'],
  },
  // Workload profile
  stages: [
    { duration: '5s', target: 30 },
    { duration: '5s', target: 150 },
    { duration: '10s', target: 150 },
    { duration: '10s', target: 0 },
  ],
};

const BASE_URL = 'http://127.0.0.1:8080';

export default function () {
  const headers = { 'Content-Type': 'application/json' };

  const customerRes = http.post(`${BASE_URL}/customers`, JSON.stringify({
    code: `C-${uuidv4()}`,
    regular: false
  }), { headers });

  check(customerRes, { 'Customer created 201': (r) => r.status === 201 });
  const customerId = customerRes.json('id');

  const parcelRes = http.post(`${BASE_URL}/parcels`, JSON.stringify({
    code: `P-${uuidv4()}`,
    weight_grams: 1000,
    declared_value: 10000
  }), { headers });

  check(parcelRes, { 'Parcel created 201': (r) => r.status === 201 });
  const parcelId = parcelRes.json('id');

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

  sleep(1);
}