"""Persist DPW VMI document deliveries for asynchronous processing."""

from typing import ClassVar

from django.db import models


class DeliveryStatus(models.TextChoices):
    """Document-delivery workflow states."""

    PENDING_FILE = "PENDING_FILE", "Pending file"
    PENDING_WEBHOOK = "PENDING_WEBHOOK", "Pending webhook"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED_FILE = "FAILED_FILE", "Failed file"
    FAILED_WEBHOOK = "FAILED_WEBHOOK", "Failed webhook"


class DpwVmiDocumentDelivery(models.Model):
    """A durably accepted VMI file delivery awaiting processing."""

    id = models.BigAutoField(primary_key=True)
    program_id = models.TextField()
    confirmation_code = models.TextField()
    transmission_method = models.TextField()
    source_object_key = models.TextField()
    raw_body = models.BinaryField()
    vmi_timestamp = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=DeliveryStatus,
        default=DeliveryStatus.PENDING_FILE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=("confirmation_code", "transmission_method"),
                name="unique_dpw_vmi_delivery_identity",
            )
        ]
