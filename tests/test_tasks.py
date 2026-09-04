def test_create_and_query_task(client, hash_task_payload):
    created = client.post("/api/tasks", json=hash_task_payload)
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["task_id"].startswith("T")
    assert created_body["status"] == "created"

    detail = client.get(f"/api/tasks/{created_body['task_id']}")
    assert detail.status_code == 200
    assert detail.json()["target"]["content"] == "$2b$12$example"
    assert detail.json()["context"]["years"] == [2024, 2025]

    listing = client.get("/api/tasks")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["task_id"] == created_body["task_id"]


def test_status_transition_is_checked(client, hash_task_payload):
    task_id = client.post("/api/tasks", json=hash_task_payload).json()["task_id"]

    analyzed = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "analyzed"}
    )
    assert analyzed.status_code == 200
    assert analyzed.json()["status"] == "analyzed"

    idempotent = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "analyzed"}
    )
    assert idempotent.status_code == 200
    assert idempotent.json()["status"] == "analyzed"

    invalid = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "completed"}
    )
    assert invalid.status_code == 409
    assert invalid.json() == {
        "error": {
            "code": "INVALID_TASK",
            "message": "非法的任务状态转换",
            "details": {"from": "analyzed", "to": "completed"},
        }
    }


def test_task_can_be_cancelled_and_cannot_resume(client, hash_task_payload):
    task_id = client.post("/api/tasks", json=hash_task_payload).json()["task_id"]

    cancelled = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    resumed = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "analyzed"}
    )
    assert resumed.status_code == 409


def test_validation_and_not_found_use_unified_error_shape(client, hash_task_payload):
    invalid_payload = hash_task_payload | {
        "target": {"type": "hash", "content": None, "file_id": None}
    }
    invalid = client.post("/api/tasks", json=invalid_payload)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_TASK"

    missing = client.get("/api/tasks/T-NOT-FOUND")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
