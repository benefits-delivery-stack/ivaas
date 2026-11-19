"""Check DPW VMI webhook configuration at startup."""

from importlib import import_module
from runpy import run_path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from dpw_vmi_adapter.apps import DpwVmiAdapterConfig

from config import settings as settings_module

PROGRAMS = {
    "test-program": {
        "dpw_vmi": {
            "webhook_url": "https://program.example/webhook",
            "webhook_signing_key": "test-webhook-signing-key",
            "managed_vmi_file_delivery_enabled": False,
        },
    },
}


def test_default_webhook_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPW_VMI_WEBHOOK_TIMESTAMP_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("DPW_VMI_PROGRAM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DPW_VMI_S3_INBOX_PREFIX", raising=False)
    monkeypatch.delenv("COBEES_VMI_MANAGED_FILE_DELIVERY_ENABLED", raising=False)
    settings = run_path(settings_module.__file__)
    configuration = settings["WEBHOOK_ADAPTERS"]["dpw_vmi_adapter"]["config"]
    assert configuration["webhook_timestamp_max_age_seconds"] == 300
    assert configuration["program_timeout_seconds"] == 30
    assert configuration["s3_inbox_prefix"] == ""
    assert settings["PROGRAMS"]["cobees"]["dpw_vmi"]["managed_vmi_file_delivery_enabled"] is False


@pytest.mark.parametrize(
    ("api_key", "max_age", "timeout", "s3_inbox_prefix", "message"),
    [
        ("", 30, 10, "", "API key"),
        (None, 30, 10, "", "API key"),
        ("test-key", 0, 10, "", "timestamp maximum age"),
        ("test-key", "30", 10, "", "timestamp maximum age"),
        ("test-key", 30, 0, "", "program timeout"),
        ("test-key", 30, "10", "", "program timeout"),
        ("test-key", 30, 10, None, "inbox prefix"),
    ],
)
def test_invalid_webhook_configuration_fails_startup(
    api_key: object,
    max_age: object,
    timeout: object,
    s3_inbox_prefix: object,
    message: str,
) -> None:
    app_config = DpwVmiAdapterConfig("dpw_vmi_adapter", import_module("dpw_vmi_adapter"))
    with (
        override_settings(
            WEBHOOK_ADAPTERS={
                "dpw_vmi_adapter": {
                    "enabled": True,
                    "config": {
                        "webhook_path": "/dpw-vmi/v1/income-report",
                        "webhook_api_key": api_key,
                        "webhook_timestamp_max_age_seconds": max_age,
                        "program_timeout_seconds": timeout,
                        "s3_inbox_prefix": s3_inbox_prefix,
                    },
                }
            },
            PROGRAMS=PROGRAMS,
        ),
        pytest.raises(ImproperlyConfigured, match=message),
    ):
        app_config.ready()
