"""Colorado programs and adapters installed into the generic IVaaS platform."""

from collections.abc import Mapping

import environ

env = environ.Env()

# Program IDs are stable machine and human identifiers. The presence of an adapter
# subkey enrolls that program in the corresponding integration.
PROGRAMS: Mapping[str, Mapping[str, Mapping[str, object]]] = {
    "cobees": {
        "dpw_vmi": {
            "webhook_url": env("COBEES_VMI_WEBHOOK_URL", default=None),
            "webhook_signing_key": env("COBEES_VMI_WEBHOOK_SIGNING_KEY", default=None),
        },
    },
}

# Class paths and constructor configuration are opaque to the generic platform.
INCOME_SOURCE_ADAPTERS: Mapping[str, Mapping[str, object]] = {
    "cdle": {
        "class": "cdle_adapter.CdleAdapter",
        "enabled": env.bool("CDLE_ADAPTER_ENABLED", default=False),
        "config": {
            "account_url": env("CDLE_ACCOUNT_URL", default=None),
            "account_identifier": env("CDLE_ACCOUNT_IDENTIFIER", default=None),
            "user": env("CDLE_USER", default=None),
            "private_key": env("CDLE_PRIVATE_KEY", default=None),
            "environment": env("CDLE_ENVIRONMENT", default=None),
            "timeout_seconds": env.int("CDLE_TIMEOUT_SECONDS", default=5),
        },
        "retry_policy": {
            "max_attempts": env.int("CDLE_RETRY_POLICY_MAX_ATTEMPTS", default=3),
            "backoff": {
                "strategy": "exponential",
                "initial_seconds": env.float(
                    "CDLE_RETRY_POLICY_BACKOFF_INITIAL_SECONDS", default=0.5
                ),
                "max_seconds": env.float("CDLE_RETRY_POLICY_BACKOFF_MAX_SECONDS", default=5),
            },
        },
        "circuit_breaker": {
            "failure_threshold": env.int("CDLE_CIRCUIT_BREAKER_FAILURE_THRESHOLD", default=5),
            "reset_timeout_seconds": env.float(
                "CDLE_CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS", default=30
            ),
        },
    },
}

WEBHOOK_ADAPTERS: Mapping[str, Mapping[str, object]] = {
    "dpw_vmi_adapter": {
        "enabled": env.bool("DPW_VMI_ADAPTER_ENABLED", default=True),
        "config": {
            "webhook_path": env.str("DPW_VMI_WEBHOOK_PATH", default="/dpw-vmi/v1/income-report"),
            "webhook_api_key": env.str("DPW_VMI_WEBHOOK_API_KEY", default=""),
            "webhook_timestamp_max_age_seconds": env.int(
                "DPW_VMI_WEBHOOK_TIMESTAMP_MAX_AGE_SECONDS", default=300
            ),
            "program_timeout_seconds": env.int("DPW_VMI_PROGRAM_TIMEOUT_SECONDS", default=30),
        },
    },
}
