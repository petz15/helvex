def _address(first_name: str, label: str | None = None) -> dict:
    payload = {
        "first_name": first_name,
        "last_name": "User",
        "street": "Main Street",
        "number": "1",
        "postal_code": "8000",
        "city": "Zurich",
        "country": "CH",
        "company_name": "",
    }
    if label is not None:
        payload["label"] = label
    return payload


def test_can_add_multiple_billing_addresses_and_set_default(client):
    resp1 = client.post("/api/v1/auth/me/billing-addresses", json={**_address("Alice", "Office"), "make_default": True})
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert any(a["first_name"] == "Alice" for a in data1["addresses"])
    first_id = data1["default_id"]
    assert data1["default_id"] == first_id

    resp2 = client.post("/api/v1/auth/me/billing-addresses", json={**_address("Bob", "Home"), "make_default": False})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert any(a["first_name"] == "Bob" for a in data2["addresses"])
    assert data2["default_id"] == first_id

    second_id = next(a["id"] for a in data2["addresses"] if a["first_name"] == "Bob")
    resp3 = client.put(f"/api/v1/auth/me/billing-addresses/{second_id}/default")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["default_id"] == second_id


def test_delete_default_address_selects_next_as_default(client):
    resp1 = client.post("/api/v1/auth/me/billing-addresses", json={**_address("Alice"), "make_default": True})
    assert resp1.status_code == 200
    first_id = resp1.json()["default_id"]

    resp2 = client.post("/api/v1/auth/me/billing-addresses", json={**_address("Bob"), "make_default": False})
    assert resp2.status_code == 200

    resp3 = client.delete(f"/api/v1/auth/me/billing-addresses/{first_id}")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert any(a["first_name"] == "Bob" for a in data3["addresses"])
    assert data3["default_id"] is not None
