"""Durably accept DPW VMI document deliveries."""

from enum import StrEnum

from dpw_vmi_adapter.models import DpwVmiDocumentDelivery


class DeliveryAcceptance(StrEnum):
    """Result of accepting one managed document delivery."""

    CREATED = "created"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


def accept_document_delivery(
    *,
    program_id: str,
    confirmation_code: str,
    source_object_key: str,
    raw_body: bytes,
    vmi_timestamp: str,
) -> tuple[DpwVmiDocumentDelivery, DeliveryAcceptance]:
    """Store one delivery, preserving the first object for an idempotency identity."""
    delivery, created = DpwVmiDocumentDelivery.objects.get_or_create(
        confirmation_code=confirmation_code,
        transmission_method="s3",
        defaults={
            "program_id": program_id,
            "source_object_key": source_object_key,
            "raw_body": raw_body,
            "vmi_timestamp": vmi_timestamp,
        },
    )
    if created:
        return delivery, DeliveryAcceptance.CREATED
    if delivery.source_object_key != source_object_key:
        return delivery, DeliveryAcceptance.CONFLICT
    return delivery, DeliveryAcceptance.DUPLICATE
