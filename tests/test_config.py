from sage_pass.config import Settings


def test_default_cors_origins_allow_vite_development_server(monkeypatch):
    monkeypatch.delenv("SAGE_CORS_ORIGINS", raising=False)

    settings = Settings.from_env()

    assert "http://localhost:5173" in settings.cors_origins
    assert "http://127.0.0.1:5173" in settings.cors_origins


def test_cors_preflight_allows_vite_development_server(client):
    response = client.options(
        "/api/tasks",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
