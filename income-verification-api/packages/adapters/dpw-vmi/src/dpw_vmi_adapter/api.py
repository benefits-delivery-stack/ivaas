"""Expose the DPW VMI webhook endpoint."""

import json
import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import NoReturn, cast
from uuid import uuid4

from django.apps import apps
from django.conf import settings
from django.db import Error as DjangoDatabaseError
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from dpw_vmi_adapter.apps import DpwVmiAdapterConfig
from dpw_vmi_adapter.forwarding import forward_webhook
from dpw_vmi_adapter.security import DpwVmiAuthenticationError, DpwVmiSignature
from dpw_vmi_adapter.services import DeliveryAcceptance, accept_document_delivery
from income_verification.request_context import LogField, set_webhook_request_id

logger = logging.getLogger(__name__)
api = NinjaAPI(urls_namespace="dpw_vmi", docs_url=None, openapi_url=None)
config = cast(Mapping[str, str], settings.WEBHOOK_ADAPTERS["dpw_vmi_adapter"]["config"])


class DpwVmiEventType(StrEnum):
    """DPW VMI webhook event types handled by IVaaS."""

    REPORT_SUBMISSION = "ReportSubmission"
    TRANSMISSION_COMPLETE = "TransmissionComplete"


def _empty_response(*, status: int = 200) -> HttpResponse:
    response = HttpResponse(status=status)
    del response.headers["Content-Type"]
    return response


def _return_response_to_vmi(response: HttpResponse) -> HttpResponse:
    logger.info(
        "Returning DPW VMI webhook response.",
        extra={
            LogField.EVENT_NAME.value: "dpw_vmi.webhook.response_returned",
            LogField.HTTP_RESPONSE_STATUS_CODE.value: response.status_code,
        },
    )
    return response


@api.exception_handler(DpwVmiAuthenticationError)
def authentication_error(request: HttpRequest, exc: DpwVmiAuthenticationError) -> HttpResponse:
    return api.create_response(
        request,
        {
            "error_code": "WEBHOOK_AUTHENTICATION_FAILED",
            "message": "Webhook authentication failed. Check headers, timestamp, and signature.",
        },
        status=401,
    )


def _reject_non_json_constant(_: str) -> NoReturn:
    """Make json.loads reject NaN and Infinity, which are not valid JSON."""
    raise ValueError("Invalid JSON constant.")


def _validation_error(request: HttpRequest, field: str, reason: str) -> HttpResponse:
    logger.error(
        "DPW VMI webhook body validation failed: %s: %s",
        field,
        reason,
        extra={
            LogField.EVENT_NAME.value: "dpw_vmi.webhook.body_validation_failed",
            LogField.HTTP_RESPONSE_STATUS_CODE.value: 400,
        },
    )
    return api.create_response(
        request,
        {
            "error_code": "VALIDATION_ERROR",
            "message": "The request body failed validation.",
            "errors": [{"field": field, "reason": reason}],
        },
        status=400,
    )


def _validate_webhook_body(
    request: HttpRequest, body: dict[str, object], event_type: DpwVmiEventType
) -> HttpResponse | None:
    """Validate only the fields IVaaS reads for supported events."""
    if event_type is DpwVmiEventType.REPORT_SUBMISSION:
        metadata = body.get("report_metadata")
        if not isinstance(metadata, dict):
            return _validation_error(request, "report_metadata", "Must be a JSON object.")
        field = "report_metadata.confirmation_code"
        confirmation_code = metadata.get("confirmation_code")
    else:
        field = "confirmation_code"
        confirmation_code = body.get("confirmation_code")

    if not isinstance(confirmation_code, str) or not confirmation_code:
        return _validation_error(request, field, "Must be a non-empty string.")
    if confirmation_code != request.headers.get("X-VMI-Confirmation-Code"):
        return _validation_error(request, field, "Must match X-VMI-Confirmation-Code.")
    return None


def _forward_to_program(request: HttpRequest, *, webhook_request_id: str) -> HttpResponse:
    configuration = cast(DpwVmiAdapterConfig, apps.get_app_config("dpw_vmi_adapter"))
    program_response = forward_webhook(
        request.body,
        program=configuration.program,
        timeout_seconds=configuration.program_timeout_seconds,
        vmi_timestamp=request.headers["X-VMI-Timestamp"],
        confirmation_code=request.headers.get("X-VMI-Confirmation-Code"),
        webhook_request_id=webhook_request_id,
    )
    response = HttpResponse(program_response.body, status=program_response.status_code)
    if program_response.content_type is None:
        del response.headers["Content-Type"]
    else:
        response.headers["Content-Type"] = program_response.content_type
    return response


def _managed_object_key(
    request: HttpRequest, body: dict[str, object], *, allowed_prefix: str
) -> str | HttpResponse:
    value = body.get("uri")
    if not isinstance(value, str) or not value:
        return _validation_error(request, "uri", "Must be a non-empty string.")
    if "://" in value:
        return _validation_error(request, "uri", "Must be an S3 object key, not a URL.")
    try:
        encoded_value = value.encode()
    except UnicodeEncodeError:
        return _validation_error(request, "uri", "Must be valid UTF-8.")
    if len(encoded_value) > 1024:
        return _validation_error(request, "uri", "Must be at most 1024 bytes.")
    if value.endswith(".tar.gz.gpg"):
        return _validation_error(request, "uri", "Must identify an unencrypted .tar.gz file.")
    if not value.endswith(".tar.gz"):
        return _validation_error(request, "uri", "Must end in .tar.gz.")
    if not value.startswith(allowed_prefix):
        return _validation_error(request, "uri", "Must be under the configured inbox prefix.")
    return value


def _delivery_conflict(request: HttpRequest, *, delivery_id: int) -> HttpResponse:
    logger.error(
        "Rejected conflicting DPW VMI document delivery for DpwVmiDocumentDelivery id %s.",
        delivery_id,
        extra={
            LogField.EVENT_NAME.value: "dpw_vmi.delivery.conflict",
            LogField.ERROR_TYPE.value: "delivery_identity_conflict",
            LogField.HTTP_RESPONSE_STATUS_CODE.value: 422,
        },
    )
    return api.create_response(
        request,
        {
            "error_code": "DELIVERY_CONFLICT",
            "message": "A different object was already accepted for this transmission.",
            "errors": [{"field": "uri", "reason": "Conflicts with the accepted object key."}],
        },
        status=422,
    )


def _unsupported_transmission_method(
    request: HttpRequest, transmission_method: str
) -> HttpResponse:
    logger.error(
        "Rejected unsupported transmission method for IVaaS-managed delivery: %r.",
        transmission_method[:100],
        extra={
            LogField.EVENT_NAME.value: "dpw_vmi.delivery.unsupported_transmission_method",
            LogField.ERROR_TYPE.value: "unsupported_transmission_method",
            LogField.HTTP_RESPONSE_STATUS_CODE.value: 422,
        },
    )
    return api.create_response(
        request,
        {
            "error_code": "UNSUPPORTED_TRANSMISSION_METHOD",
            "message": "The transmission method is not supported for IVaaS-managed delivery.",
            "errors": [{"field": "transmission_method", "reason": "Must be s3."}],
        },
        status=422,
    )


def _handle_managed_transmission_complete(
    request: HttpRequest,
    body: dict[str, object],
    *,
    configuration: DpwVmiAdapterConfig,
) -> HttpResponse:
    transmission_method = body.get("transmission_method")
    if not isinstance(transmission_method, str) or not transmission_method:
        return _validation_error(request, "transmission_method", "Must be a non-empty string.")
    if transmission_method != "s3":
        return _unsupported_transmission_method(request, transmission_method)

    object_key = _managed_object_key(
        request,
        body,
        allowed_prefix=configuration.s3_inbox_prefix,
    )
    if isinstance(object_key, HttpResponse):
        return object_key

    try:
        delivery, acceptance = accept_document_delivery(
            program_id=configuration.program.program_id,
            confirmation_code=cast(str, body["confirmation_code"]),
            source_object_key=object_key,
            raw_body=request.body,
            vmi_timestamp=request.headers["X-VMI-Timestamp"],
        )
    except DjangoDatabaseError:
        logger.exception(
            "Failed to store DPW VMI document delivery.",
            extra={
                LogField.EVENT_NAME.value: "dpw_vmi.delivery.store_failed",
                LogField.ERROR_TYPE.value: "database_error",
                LogField.HTTP_RESPONSE_STATUS_CODE.value: 503,
            },
        )
        return _empty_response(status=503)

    if acceptance is DeliveryAcceptance.CONFLICT:
        return _delivery_conflict(request, delivery_id=delivery.pk)

    event_name = (
        "dpw_vmi.delivery.created"
        if acceptance is DeliveryAcceptance.CREATED
        else "dpw_vmi.delivery.duplicate"
    )
    logger.info(
        "DPW VMI document delivery %s with DpwVmiDocumentDelivery id %s.",
        acceptance,
        delivery.pk,
        extra={
            LogField.EVENT_NAME.value: event_name,
            LogField.HTTP_RESPONSE_STATUS_CODE.value: 200,
        },
    )
    return _empty_response()


def _handle_transmission_complete(
    request: HttpRequest,
    body: dict[str, object],
    *,
    webhook_request_id: str,
) -> HttpResponse:
    configuration = cast(DpwVmiAdapterConfig, apps.get_app_config("dpw_vmi_adapter"))
    if not configuration.program.managed_vmi_file_delivery_enabled:
        return _forward_to_program(request, webhook_request_id=webhook_request_id)
    return _handle_managed_transmission_complete(request, body, configuration=configuration)


def _handle_webhook(request: HttpRequest, *, webhook_request_id: str) -> HttpResponse:
    try:
        body = json.loads(request.body, parse_constant=_reject_non_json_constant)
    except ValueError, RecursionError:
        return _validation_error(request, "body", "Must be valid JSON.")
    if not isinstance(body, dict):
        return _validation_error(request, "body", "Must be a JSON object.")

    raw_event_type = body.get("event_type", DpwVmiEventType.REPORT_SUBMISSION)
    try:
        event_type = DpwVmiEventType(raw_event_type)
    except ValueError, TypeError:
        event_type = None
    if event_type is None:
        logger.warning(
            "Ignored unsupported DPW VMI webhook event type: %s.",
            raw_event_type,
            extra={
                LogField.EVENT_NAME.value: "dpw_vmi.webhook.unsupported_event_type",
                LogField.ERROR_TYPE.value: "unsupported_event_type",
                LogField.HTTP_RESPONSE_STATUS_CODE.value: 200,
            },
        )
        return _empty_response()

    error = _validate_webhook_body(request, body, event_type)
    if error is not None:
        return error
    logger.info(
        "Handling DPW VMI webhook event type: %s.",
        event_type,
        extra={LogField.EVENT_NAME.value: "dpw_vmi.webhook.event_handling"},
    )
    match event_type:
        case DpwVmiEventType.REPORT_SUBMISSION:
            return _forward_to_program(request, webhook_request_id=webhook_request_id)
        case DpwVmiEventType.TRANSMISSION_COMPLETE:
            return _handle_transmission_complete(
                request,
                body,
                webhook_request_id=webhook_request_id,
            )


@api.post(config["webhook_path"], auth=DpwVmiSignature())
def receive_income_report(request: HttpRequest) -> HttpResponse:
    """Forward a direct webhook response or durably accept managed file delivery."""
    webhook_request_id = str(uuid4())
    set_webhook_request_id(webhook_request_id)
    logger.info(
        "Authenticated DPW VMI webhook received.",
        extra={LogField.EVENT_NAME.value: "dpw_vmi.webhook.received"},
    )
    return _return_response_to_vmi(_handle_webhook(request, webhook_request_id=webhook_request_id))
