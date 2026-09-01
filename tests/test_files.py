def test_upload_then_create_file_task(client):
    uploaded = client.post(
        "/api/files",
        files={"file": ("sample.zip", b"PK-test", "application/zip")},
    )
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["file_id"].startswith("F")
    assert body["size"] == 7
    assert len(body["sha256"]) == 64

    task = client.post(
        "/api/tasks",
        json={
            "name": "ZIP评测",
            "target": {"type": "zip", "content": None, "file_id": body["file_id"]},
            "known_algorithm": None,
            "time_budget": 60,
            "candidate_budget": 1000,
            "context": {},
        },
    )
    assert task.status_code == 201


def test_upload_size_limit(client):
    response = client.post(
        "/api/files",
        files={"file": ("large.bin", b"x" * 1025, "application/octet-stream")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["details"]["max_bytes"] == 1024


def test_file_task_rejects_unknown_file_id(client):
    response = client.post(
        "/api/tasks",
        json={
            "name": "缺失文件",
            "target": {"type": "pdf", "content": None, "file_id": "F-MISSING"},
            "known_algorithm": None,
            "time_budget": 60,
            "candidate_budget": 1000,
            "context": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "INVALID_TASK",
        "message": "目标文件不存在",
        "details": {"field": "target.file_id"},
    }
