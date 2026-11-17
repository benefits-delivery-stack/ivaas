"""Check DPW VMI webhook authentication, body validation, and forwarding."""

import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from unittest.mock import Mock
from uuid import UUID

import pytest
from django.apps import apps
from django.conf import settings
from django.db import OperationalError
from django.test import Client, override_settings
from dpw_vmi_adapter.apps import DpwVmiAdapterConfig
from dpw_vmi_adapter.forwarding import ProgramResponse
from dpw_vmi_adapter.models import DeliveryStatus, DpwVmiDocumentDelivery
from dpw_vmi_adapter.signing import sign

from income_verification.request_context import JsonLogFormatter

WEBHOOK_PATH = "/dpw-vmi/v1/income-report"
WEBHOOK_URL = f"/api{WEBHOOK_PATH}"
WEBHOOK_KEY = "dpw-vmi-test-key"
SIGNED_AT = 1_700_000_000
OBJECT_KEY = "VMI_01234567-89ab-cdef-0123-456789abcdef_20261117_Conf-TESTCONF123.tar.gz"
BODY = (
    b'{"event_type":"ReportSubmission","report_metadata":{"confirmation_code":"report-123"},'
    b'"employment_records":42}'
)
AUTHENTICATION_ERROR = {
    "error_code": "WEBHOOK_AUTHENTICATION_FAILED",
    "message": "Webhook authentication failed. Check headers, timestamp, and signature.",
}
PROGRAMS = {
    "test-program": {
        "dpw_vmi": {
            "webhook_url": "https://program.example/webhook",
            "webhook_signing_key": "test-webhook-signing-key",
            "managed_vmi_file_delivery_enabled": False,
        },
    },
}


def transmission_complete_body(
    *,
    uri: object = OBJECT_KEY,
    transmission_method: object = "s3",
    completed_at: str = "2026-11-17T12:00:00Z",
) -> bytes:
    return json.dumps(
        {
            "event_type": "TransmissionComplete",
            "confirmation_code": "report-123",
            "transmission_method": transmission_method,
            "uri": uri,
            "completed_at": completed_at,
        },
        separators=(",", ":"),
    ).encode()


def signed_headers(
    *,
    body: bytes = BODY,
    timestamp: str = str(SIGNED_AT),
    key: str = WEBHOOK_KEY,
    confirmation_code: str | None = "report-123",
) -> dict[str, str]:
    headers = {
        "X-VMI-Timestamp": timestamp,
        "X-VMI-Signature": sign(body, key=key, timestamp=timestamp),
    }
    if confirmation_code is not None:
        headers["X-VMI-Confirmation-Code"] = confirmation_code
    return headers


@pytest.fixture(autouse=True)
def forward_webhook_mock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock(return_value=ProgramResponse(200, b"", None))
    monkeypatch.setattr("dpw_vmi_adapter.api.forward_webhook", mock)
    return mock


@pytest.fixture
def dpw_vmi_adapter() -> Iterator[None]:
    with (
        override_settings(
            ROOT_URLCONF="tests.webhook_adapters.dpw_vmi_adapter.urls",
            WEBHOOK_ADAPTERS={
                "dpw_vmi_adapter": {
                    "enabled": True,
                    "config": {
                        "webhook_path": WEBHOOK_PATH,
                        "webhook_api_key": WEBHOOK_KEY,
                        "webhook_timestamp_max_age_seconds": 300,
                        "program_timeout_seconds": 10,
                    },
                }
            },
            PROGRAMS=PROGRAMS,
        ),
        override_settings(
            INSTALLED_APPS=list(dict.fromkeys([*settings.INSTALLED_APPS, "dpw_vmi_adapter"]))
        ),
    ):
        yield


@pytest.fixture
def managed_vmi_file_delivery(dpw_vmi_adapter: None) -> Iterator[None]:
    configuration = apps.get_app_config("dpw_vmi_adapter")
    assert isinstance(configuration, DpwVmiAdapterConfig)
    direct_program = configuration.program
    configuration.program = replace(direct_program, managed_vmi_file_delivery_enabled=True)
    try:
        yield
    finally:
        configuration.program = direct_program


@pytest.mark.usefixtures("dpw_vmi_adapter")
@pytest.mark.parametrize("received_at", [SIGNED_AT - 5, SIGNED_AT + 300])
def test_webhook_is_acknowledged(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    forward_webhook_mock: Mock,
    received_at: int,
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: received_at)
    log_context_mock = Mock()
    monkeypatch.setattr("dpw_vmi_adapter.api.set_webhook_request_id", log_context_mock)
    with caplog.at_level(logging.INFO, logger="dpw_vmi_adapter.api"):
        response = client.post(
            WEBHOOK_URL, data=BODY, content_type="application/json", headers=signed_headers()
        )

    assert response.status_code == 200
    assert response.content == b""
    assert "Content-Type" not in response.headers
    assert forward_webhook_mock.call_args.args == (BODY,)
    assert forward_webhook_mock.call_args.kwargs["timeout_seconds"] == 10
    assert forward_webhook_mock.call_args.kwargs["vmi_timestamp"] == str(SIGNED_AT)
    events = {record.__dict__.get("event.name"): record for record in caplog.records}
    assert "dpw_vmi.webhook.received" in events
    assert "ReportSubmission" in events["dpw_vmi.webhook.event_handling"].getMessage()
    assert events["dpw_vmi.webhook.response_returned"].__dict__["http.response.status_code"] == 200
    generated_id = log_context_mock.call_args.args[0]
    assert str(UUID(generated_id)) == generated_id
    assert forward_webhook_mock.call_args.kwargs["webhook_request_id"] == generated_id
    assert "X-IVaaS-Webhook-Request-Id" not in response.headers


@pytest.mark.usefixtures("dpw_vmi_adapter")
def test_direct_transmission_complete_returns_program_response(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    body = transmission_complete_body()
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)
    forward_webhook_mock.return_value = ProgramResponse(
        422,
        b'{"error_code":"INVALID_REPORT"}',
        "application/problem+json",
    )
    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body),
    )

    assert response.status_code == 422
    assert response.content == b'{"error_code":"INVALID_REPORT"}'
    assert response.headers["Content-Type"] == "application/problem+json"
    assert forward_webhook_mock.call_args.kwargs["confirmation_code"] == "report-123"


@pytest.mark.usefixtures("dpw_vmi_adapter")
@pytest.mark.parametrize(
    ("body", "event_type"),
    [
        (
            transmission_complete_body(),
            "TransmissionComplete",
        ),
        (b'{"report_metadata":{"confirmation_code":"report-123"}}', "ReportSubmission"),
    ],
)
def test_webhook_forwards_other_supported_event_shapes(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    forward_webhook_mock: Mock,
    body: bytes,
    event_type: str,
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body),
    )

    assert response.status_code == 200
    assert forward_webhook_mock.call_args.args == (body,)
    handling = next(
        record
        for record in caplog.records
        if record.__dict__.get("event.name") == "dpw_vmi.webhook.event_handling"
    )
    assert event_type in handling.getMessage()


@pytest.mark.django_db
@pytest.mark.usefixtures("managed_vmi_file_delivery")
def test_managed_s3_delivery_is_stored_before_acknowledgement(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    body = transmission_complete_body()
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body),
    )

    assert response.status_code == 200
    assert response.content == b""
    assert "Content-Type" not in response.headers
    forward_webhook_mock.assert_not_called()
    delivery = DpwVmiDocumentDelivery.objects.get()
    assert delivery.program_id == "test-program"
    assert delivery.confirmation_code == "report-123"
    assert delivery.transmission_method == "s3"
    assert delivery.source_object_key == OBJECT_KEY
    assert bytes(delivery.raw_body) == body
    assert delivery.vmi_timestamp == str(SIGNED_AT)
    assert delivery.status == DeliveryStatus.PENDING_FILE


@pytest.mark.django_db
@pytest.mark.usefixtures("managed_vmi_file_delivery")
def test_managed_s3_duplicate_keeps_first_delivery(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    first_body = transmission_complete_body()
    retry_body = transmission_complete_body(completed_at="2026-11-17T12:01:00Z")
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT + 1)

    for body, timestamp in (
        (first_body, str(SIGNED_AT)),
        (retry_body, str(SIGNED_AT + 1)),
    ):
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            headers=signed_headers(body=body, timestamp=timestamp),
        )
        assert response.status_code == 200

    forward_webhook_mock.assert_not_called()
    assert DpwVmiDocumentDelivery.objects.count() == 1
    delivery = DpwVmiDocumentDelivery.objects.get()
    assert bytes(delivery.raw_body) == first_body
    assert delivery.vmi_timestamp == str(SIGNED_AT)


@pytest.mark.django_db
@pytest.mark.usefixtures("managed_vmi_file_delivery")
def test_managed_s3_conflict_rejects_different_object_key(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    first_body = transmission_complete_body()
    conflicting_body = transmission_complete_body(uri="different.tar.gz")
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)
    first_response = client.post(
        WEBHOOK_URL,
        data=first_body,
        content_type="application/json",
        headers=signed_headers(body=first_body),
    )

    response = client.post(
        WEBHOOK_URL,
        data=conflicting_body,
        content_type="application/json",
        headers=signed_headers(body=conflicting_body),
    )

    assert first_response.status_code == 200
    assert response.status_code == 422
    assert response.json() == {
        "error_code": "DELIVERY_CONFLICT",
        "message": "A different object was already accepted for this transmission.",
        "errors": [{"field": "uri", "reason": "Conflicts with the accepted object key."}],
    }
    forward_webhook_mock.assert_not_called()
    assert DpwVmiDocumentDelivery.objects.count() == 1
    delivery = DpwVmiDocumentDelivery.objects.get()
    assert delivery.source_object_key == OBJECT_KEY
    assert bytes(delivery.raw_body) == first_body


@pytest.mark.usefixtures("managed_vmi_file_delivery")
@pytest.mark.parametrize(
    ("uri", "reason"),
    [
        (None, "Must be a non-empty string."),
        (42, "Must be a non-empty string."),
        ("", "Must be a non-empty string."),
        ("s3://bucket/file.tar.gz", "Must be an S3 object key, not a URL."),
        ("\ud800.tar.gz", "Must be valid UTF-8."),
        ("file.tar.gz.gpg", "Must identify an unencrypted .tar.gz file."),
        ("file.zip", "Must end in .tar.gz."),
        (f"{'a' * 1018}.tar.gz", "Must be at most 1024 bytes."),
    ],
)
def test_managed_s3_delivery_rejects_invalid_object_key_before_database_access(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
    uri: object,
    reason: str,
) -> None:
    body = transmission_complete_body(uri=uri)
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body),
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [{"field": "uri", "reason": reason}]
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("managed_vmi_file_delivery")
def test_managed_s3_delivery_rejects_key_outside_configured_prefix(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    body = transmission_complete_body()
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)
    configuration = apps.get_app_config("dpw_vmi_adapter")
    assert isinstance(configuration, DpwVmiAdapterConfig)
    original_prefix = configuration.s3_inbox_prefix
    configuration.s3_inbox_prefix = "incoming/"
    try:
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            headers=signed_headers(body=body),
        )
    finally:
        configuration.s3_inbox_prefix = original_prefix

    assert response.status_code == 400
    assert response.json()["errors"] == [
        {"field": "uri", "reason": "Must be under the configured inbox prefix."}
    ]
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("managed_vmi_file_delivery")
@pytest.mark.parametrize("transmission_method", ["json", "shared_email", None])
def test_managed_program_still_forwards_non_s3_transmissions(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
    transmission_method: object,
) -> None:
    body = transmission_complete_body(uri=None, transmission_method=transmission_method)
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body),
    )

    assert response.status_code == 200
    assert forward_webhook_mock.call_args.args == (body,)


@pytest.mark.usefixtures("managed_vmi_file_delivery")
def test_managed_s3_delivery_returns_503_when_database_write_fails(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    body = transmission_complete_body()
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)
    monkeypatch.setattr(
        "dpw_vmi_adapter.api.accept_document_delivery",
        Mock(side_effect=OperationalError),
    )

    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body),
    )

    assert response.status_code == 503
    assert response.content == b""
    assert "Content-Type" not in response.headers
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("dpw_vmi_adapter")
@pytest.mark.parametrize(
    "body",
    [
        b'{"event_type":"FutureEvent"}',
        b'{"event_type":"reportsubmission"}',
        b'{"event_type":null}',
        b'{"event_type":[]}',
    ],
)
def test_webhook_ignores_unsupported_event_type(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    forward_webhook_mock: Mock,
    body: bytes,
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    with caplog.at_level(logging.WARNING, logger="dpw_vmi_adapter.api"):
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            headers=signed_headers(body=body),
        )

    assert response.status_code == 200
    assert response.content == b""
    assert "Content-Type" not in response.headers
    ignored = next(
        record
        for record in caplog.records
        if record.__dict__.get("event.name") == "dpw_vmi.webhook.unsupported_event_type"
    )
    assert ignored.__dict__["error.type"] == "unsupported_event_type"
    assert ignored.levelno == logging.WARNING
    assert str(json.loads(body)["event_type"]) in ignored.getMessage()
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("dpw_vmi_adapter")
def test_webhook_rejects_json_that_exceeds_parser_depth(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    body = b"{}"
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    with monkeypatch.context() as parser:
        parser.setattr("dpw_vmi_adapter.api.json.loads", Mock(side_effect=RecursionError))
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            headers=signed_headers(body=body),
        )

    assert response.status_code == 400
    assert response.json()["errors"] == [{"field": "body", "reason": "Must be valid JSON."}]
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("dpw_vmi_adapter")
@pytest.mark.parametrize(
    ("body", "header_code", "field", "reason"),
    [
        (b"{", "report-123", "body", "Must be valid JSON."),
        (b"not-json", "report-123", "body", "Must be valid JSON."),
        (b"\xff", "report-123", "body", "Must be valid JSON."),
        (
            b'{"report_metadata":{"confirmation_code":"report-123"},"value":NaN}',
            "report-123",
            "body",
            "Must be valid JSON.",
        ),
        (b"[]", "report-123", "body", "Must be a JSON object."),
        (b"{}", "report-123", "report_metadata", "Must be a JSON object."),
        (b'{"report_metadata":[]}', "report-123", "report_metadata", "Must be a JSON object."),
        (
            b'{"report_metadata":{}}',
            "report-123",
            "report_metadata.confirmation_code",
            "Must be a non-empty string.",
        ),
        (
            b'{"report_metadata":{"confirmation_code":123}}',
            "report-123",
            "report_metadata.confirmation_code",
            "Must be a non-empty string.",
        ),
        (
            b'{"event_type":"TransmissionComplete"}',
            "report-123",
            "confirmation_code",
            "Must be a non-empty string.",
        ),
        (
            b'{"event_type":"TransmissionComplete","confirmation_code":"report-123"}',
            "different",
            "confirmation_code",
            "Must match X-VMI-Confirmation-Code.",
        ),
        (
            b'{"report_metadata":{"confirmation_code":"report-123"}}',
            "different",
            "report_metadata.confirmation_code",
            "Must match X-VMI-Confirmation-Code.",
        ),
    ],
)
def test_invalid_body_returns_field_error(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    forward_webhook_mock: Mock,
    body: bytes,
    header_code: str | None,
    field: str,
    reason: str,
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)
    response = client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        headers=signed_headers(body=body, confirmation_code=header_code),
    )

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "VALIDATION_ERROR",
        "message": "The request body failed validation.",
        "errors": [{"field": field, "reason": reason}],
    }
    failure = next(
        record
        for record in caplog.records
        if record.__dict__.get("event.name") == "dpw_vmi.webhook.body_validation_failed"
    )
    log = json.loads(JsonLogFormatter().format(failure))
    assert log["level"] == "ERROR"
    assert log["event.name"] == "dpw_vmi.webhook.body_validation_failed"
    assert log["http.response.status_code"] == 400
    assert field in log["message"]
    assert reason in log["message"]
    assert "report-123" not in log["message"]
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("dpw_vmi_adapter")
@pytest.mark.parametrize(
    ("headers", "detail", "reason"),
    [
        ({"X-VMI-Signature": "a" * 128}, "Missing X-VMI-Timestamp", "missing_timestamp"),
        (signed_headers(timestamp="not-seconds"), "must be Unix seconds", "invalid_timestamp"),
        (signed_headers(timestamp="9" * 21), "must be Unix seconds", "invalid_timestamp"),
        ({"X-VMI-Timestamp": str(SIGNED_AT)}, "Missing X-VMI-Signature", "missing_signature"),
        (
            signed_headers(confirmation_code=None),
            "Missing X-VMI-Confirmation-Code",
            "missing_confirmation_code",
        ),
        (
            {**signed_headers(), "X-VMI-Signature": "invalid"},
            "128 lowercase hex characters",
            "invalid_signature",
        ),
        (signed_headers(body=b"different"), "does not match", "signature_mismatch"),
        (signed_headers(key="wrong-key"), "does not match", "signature_mismatch"),
        (signed_headers(timestamp=str(SIGNED_AT + 6)), "in the future", "future_timestamp"),
        (signed_headers(timestamp=str(SIGNED_AT - 301)), "too old", "expired_timestamp"),
    ],
)
def test_webhook_rejects_invalid_headers(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    headers: dict[str, str],
    detail: str,
    reason: str,
    forward_webhook_mock: Mock,
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)

    with caplog.at_level(logging.WARNING, logger="dpw_vmi_adapter.security"):
        response = client.post(
            WEBHOOK_URL, data=BODY, content_type="application/json", headers=headers
        )

    assert response.status_code == 401
    assert response.json() == AUTHENTICATION_ERROR
    failure = next(record for record in caplog.records if record.name == "dpw_vmi_adapter.security")
    assert failure.levelno == logging.ERROR
    assert failure.__dict__["auth.failure.reason"] == reason
    assert detail in failure.getMessage()
    assert BODY.decode() not in failure.getMessage()
    assert WEBHOOK_KEY not in failure.getMessage()
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("dpw_vmi_adapter")
def test_webhook_signature_covers_exact_body_bytes(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    forward_webhook_mock: Mock,
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT)
    response = client.post(
        WEBHOOK_URL,
        data=b'{ "event_type": "ReportSubmission" }',
        content_type="application/json",
        headers=signed_headers(),
    )

    assert response.status_code == 401
    assert response.json() == AUTHENTICATION_ERROR
    forward_webhook_mock.assert_not_called()


@pytest.mark.usefixtures("dpw_vmi_adapter")
def test_webhook_timestamp_window_is_configurable(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dpw_vmi_adapter.security.time.time", lambda: SIGNED_AT + 301)
    with (
        override_settings(
            WEBHOOK_ADAPTERS={
                "dpw_vmi_adapter": {
                    "enabled": True,
                    "config": {
                        "webhook_path": WEBHOOK_PATH,
                        "webhook_api_key": WEBHOOK_KEY,
                        "webhook_timestamp_max_age_seconds": 600,
                        "program_timeout_seconds": 10,
                    },
                }
            }
        ),
        override_settings(INSTALLED_APPS=settings.INSTALLED_APPS),
    ):
        response = client.post(
            WEBHOOK_URL, data=BODY, content_type="application/json", headers=signed_headers()
        )

    assert response.status_code == 200
