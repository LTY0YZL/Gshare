# core/tasks.py
from __future__ import absolute_import, unicode_literals

from celery import shared_task
from django.db import transaction

from core.models import Receipt
from core.ai.receipt_gemini import parse_receipt_image, apply_parsed_receipt


@shared_task
def parse_receipt_task(receipt_id: int):
    """
    Background task:
    - download receipt image from S3
    - run Gemini vision to parse it
    - store JSON + ReceiptLine rows
    """
    # Use gsharedb consistently
    receipt = Receipt.objects.using("gsharedb").get(pk=receipt_id)

    # Mark as processing
    receipt.status = "processing"
    receipt.error = ""
    receipt.save(using="gsharedb")

    try:
        data = parse_receipt_image(receipt)

        # Save JSON + create lines in a transaction
        with transaction.atomic(using="gsharedb"):
            apply_parsed_receipt(receipt, data)

    except Exception as e:
        receipt.status = "error"
        receipt.error = str(e)
        receipt.save(using="gsharedb")
        raise
