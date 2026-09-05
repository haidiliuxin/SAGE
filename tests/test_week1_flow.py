def test_analyze_plan_execute_mock_hash_task(client, hash_task_payload):
    task_id = client.post("/api/tasks", json=hash_task_payload).json()["task_id"]

    analyzed = client.post(f"/api/tasks/{task_id}/analyze")
    assert analyzed.status_code == 200
    prir = analyzed.json()
    assert prir["task_id"] == task_id
    assert prir["target_type"] == "hash"
    assert prir["algorithm"] == "bcrypt"
    assert prir["salt"] is True
    assert prir["verification_cost"] == "high"
    assert prir["status"] == "analyzed"

    planned = client.post(f"/api/tasks/{task_id}/plan")
    assert planned.status_code == 200
    plan = planned.json()
    assert plan["task_id"] == task_id
    assert plan["planner_type"] == "mock"
    assert plan["status"] == "planned"
    assert [item["strategy_id"] for item in plan["strategies"]] == ["S1", "S4"]
    assert sum(item["time_budget"] for item in plan["strategies"]) <= 300

    started = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "mock"})
    assert started.status_code == 200
    run = started.json()
    assert run["task_id"] == task_id
    assert run["run_id"].startswith("R")
    assert run["status"] == "running"

    status = client.get(f"/api/runs/{run['run_id']}/status")
    assert status.status_code == 200
    assert status.json()["task_id"] == task_id
    assert 0 <= status.json()["progress"] <= 1

    result = client.get(f"/api/runs/{run['run_id']}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "completed"
    assert body["total_tested"] > 0
    assert [item["strategy_id"] for item in body["strategy_results"]] == ["S1", "S4"]


def test_analyze_file_task_without_zip2john_degrades_gracefully(client):
    uploaded = client.post(
        "/api/files",
        files={"file": ("sample.zip", b"PK-test", "application/zip")},
    )
    file_id = uploaded.json()["file_id"]
    created = client.post(
        "/api/tasks",
        json={
            "name": "ZIP评测",
            "target": {"type": "zip", "content": None, "file_id": file_id},
            "known_algorithm": None,
            "time_budget": 60,
            "candidate_budget": 1000,
            "context": {},
        },
    )
    task_id = created.json()["task_id"]

    # 未安装/配置 zip2john 时，ZIP 分析应降级为元数据级 PRIR（200），
    # 而不是 500；提示信息不依赖运行环境中的具体二进制行为。
    analyzed = client.post(f"/api/tasks/{task_id}/analyze")
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["target_type"] == "zip"
    assert body["algorithm"] == "unknown"
    assert body["verification_cost"] == "unknown"
    assert body["candidate_space"] is None
    assert body["warnings"], "应包含降级提示，说明未能解析加密结构"


def test_execute_requires_planned_task(client, hash_task_payload):
    task_id = client.post("/api/tasks", json=hash_task_payload).json()["task_id"]

    response = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "mock"})
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "INVALID_TASK",
        "message": "任务状态不允许启动执行",
        "details": {"from": "created", "to": "running"},
    }


def test_running_task_can_be_cancelled_before_completion(client, hash_task_payload):
    task_id = client.post("/api/tasks", json=hash_task_payload).json()["task_id"]
    client.post(f"/api/tasks/{task_id}/analyze")
    client.post(f"/api/tasks/{task_id}/plan")
    started = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "mock"})
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    cancelled = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "cancelled"}
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "cancelled"


def test_context_plan_respects_minimum_task_budget(client, hash_task_payload):
    payload = hash_task_payload | {"time_budget": 1, "candidate_budget": 1}
    task_id = client.post("/api/tasks", json=payload).json()["task_id"]

    analyzed = client.post(f"/api/tasks/{task_id}/analyze")
    assert analyzed.status_code == 200

    planned = client.post(f"/api/tasks/{task_id}/plan")
    assert planned.status_code == 200
    body = planned.json()
    assert [item["strategy_id"] for item in body["strategies"]] == ["S1"]
    assert sum(item["time_budget"] for item in body["strategies"]) <= 1
    assert sum(item["candidate_budget"] for item in body["strategies"]) <= 1
    assert body["warnings"] == ["上下文策略因任务预算不足未加入计划"]
