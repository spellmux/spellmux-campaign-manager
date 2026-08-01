from pathlib import Path

import yaml


def test_analysis_services_are_isolated() -> None:
    compose = yaml.safe_load(Path("compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"ollama", "ollama-model", "analysis-worker"} <= set(services)
    assert services["worker"]["environment"]["CAMPAIGN_ANALYSIS_PROVIDER"] == "disabled"
    assert services["diarization-worker"]["environment"]["CAMPAIGN_ANALYSIS_PROVIDER"] == "disabled"
    assert services["analysis-worker"]["environment"]["CAMPAIGN_ANALYSIS_PROVIDER"] == "ollama"
    assert services["analysis-worker"]["depends_on"]["ollama-model"]["condition"] == "service_completed_successfully"
