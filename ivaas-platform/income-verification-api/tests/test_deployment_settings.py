"""Check the generic deployment-settings extension point."""

import sys
from runpy import run_path
from types import ModuleType

import pytest

from config import settings as settings_module


def test_deployment_settings_load_before_derived_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "test_deployment_settings"
    deployment = ModuleType(module_name)
    programs: dict[str, dict[str, object]] = {"test-program": {}}
    income_source_adapters = {
        "example": {
            "class": "example_adapter.ExampleAdapter",
            "enabled": False,
            "config": {},
            "retry_policy": None,
            "circuit_breaker": None,
        }
    }
    webhook_adapters = {"example_webhook_adapter": {"enabled": True, "config": {}}}
    deployment.__dict__.update(
        PROGRAMS=programs,
        INCOME_SOURCE_ADAPTERS=income_source_adapters,
        WEBHOOK_ADAPTERS=webhook_adapters,
    )
    monkeypatch.setitem(sys.modules, module_name, deployment)
    monkeypatch.setenv("IVAAS_DEPLOYMENT_SETTINGS", module_name)

    configured = run_path(settings_module.__file__)

    assert configured["PROGRAMS"] == programs
    assert configured["INCOME_SOURCE_ADAPTERS"] == income_source_adapters
    assert "example_webhook_adapter" in configured["INSTALLED_APPS"]
    assert "example_adapter" in configured["LOGGING"]["loggers"]
    assert "example_webhook_adapter" in configured["LOGGING"]["loggers"]
