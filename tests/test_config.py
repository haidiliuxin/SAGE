from sage_pass.config import Settings
import pytest


def test_default_cors_origins_allow_vite_development_server(monkeypatch):
    monkeypatch.delenv("SAGE_CORS_ORIGINS", raising=False)

    settings = Settings.from_env()

    assert "http://localhost:5173" in settings.cors_origins
    assert "http://127.0.0.1:5173" in settings.cors_origins
    assert settings.pcfg_variant == "pcfg_lite"
    assert settings.pcfg_ruleset_path is None
    assert settings.s3_generator_id is None
    assert settings.s4_generator_id is None
    assert settings.markov_ruleset_path is None
    assert settings.markov_order == 3
    # 默认与调度粒度一致（1000）；吞吐优先时可显式调大。
    assert settings.hashcat_stream_batch_size == 1_000


def test_pcfg_configuration_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGE_PCFG_VARIANT", "pcfg_full")
    monkeypatch.setenv("SAGE_PCFG_RULESET_PATH", str(tmp_path))

    settings = Settings.from_env()

    assert settings.pcfg_variant == "pcfg_full"
    assert settings.pcfg_ruleset_path == tmp_path.resolve()


def test_markov_configuration_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGE_S3_GENERATOR", "markov")
    monkeypatch.setenv("SAGE_MARKOV_RULESET_PATH", str(tmp_path))
    monkeypatch.setenv("SAGE_MARKOV_ORDER", "3")

    settings = Settings.from_env()

    assert settings.s3_generator_id == "markov"
    assert settings.markov_ruleset_path == tmp_path.resolve()
    assert settings.markov_order == 3


def test_personalized_generator_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("SAGE_S4_GENERATOR", "hybrid")

    assert Settings.from_env().s4_generator_id == "hybrid"


def test_hashcat_stream_batch_size_must_be_positive(monkeypatch):
    monkeypatch.setenv("SAGE_HASHCAT_STREAM_BATCH_SIZE", "0")
    with pytest.raises(ValueError, match="必须大于 0"):
        Settings.from_env()


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
