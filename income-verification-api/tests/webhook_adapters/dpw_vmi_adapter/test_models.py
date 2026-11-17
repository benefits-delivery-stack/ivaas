"""Check DPW VMI document-delivery persistence constraints."""

import pytest
from django.db import IntegrityError, transaction
from dpw_vmi_adapter.models import DpwVmiDocumentDelivery


@pytest.mark.django_db
def test_delivery_identity_is_unique() -> None:
    values = {
        "program_id": "test-program",
        "confirmation_code": "report-123",
        "transmission_method": "s3",
        "source_object_key": "document.tar.gz",
        "raw_body": b"{}",
        "vmi_timestamp": "1700000000",
    }
    DpwVmiDocumentDelivery.objects.create(**values)

    with pytest.raises(IntegrityError), transaction.atomic():
        DpwVmiDocumentDelivery.objects.create(**values)
