import boto3, mimetypes
from celery import shared_task
from django.conf import settings
from django.db import transaction
from .models import Receipt, ReceiptLine
from .utils.gemini_client import parse_receipt_with_gemini
from .utils.order_resolver import assign_lines_to_orders, pick_best_order_for_receipt
from .utils.orders_for_driver import get_active_orders_for_driver

def s3_client(): return boto3.client("s3", region_name=settings.AWS_REGION)
def rekognition_client(): return boto3.client("rekognition", region_name=settings.AWS_REGION)

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def parse_receipt_task(self, receipt_id: int):
    r = Receipt.objects.get(id=receipt_id)
    Receipt.objects.filter(id=receipt_id).update(status='processing', error='')
    try:
        obj = s3_client().get_object(Bucket=r.s3_bucket, Key=r.s3_key)
        data = obj["Body"].read()
        mime = mimetypes.guess_type(r.s3_key)[0] or "image/jpeg"

        # Optional OCR context from Rekognition
        dt = rekognition_client().detect_text(Image={"S3Object":{"Bucket": r.s3_bucket, "Name": r.s3_key}})
        lines_ctx = [t["DetectedText"] for t in dt.get("TextDetections", []) if t.get("Type")=="LINE"]

        parsed = parse_receipt_with_gemini(data, mime, text_lines=lines_ctx)
        lines = parsed.get("lines", [])

        with transaction.atomic():
            r.gemini_json = parsed
            ReceiptLine.objects.filter(receipt=r).delete()
            for ln in lines:
                ReceiptLine.objects.create(
                    receipt=r,
                    name=str(ln.get("name","")).strip()[:256],
                    quantity=float(ln.get("quantity") or 1),
                    unit_price=ln.get("unit_price"),
                    total_price=ln.get("total_price"),
                    meta=ln
                )

            orders = get_active_orders_for_driver(r.uploader_id or 0)
            assigns = assign_lines_to_orders(
                [{"name":ln.get("name",""), "quantity":float(ln.get("quantity") or 1)} for ln in lines],
                orders
            )
            pick = pick_best_order_for_receipt(assigns)
            r.inferred_order_id = pick["order_id"]
            r.status = 'done'
            r.save()

        return {"ok": True, "inferred_order_id": r.inferred_order_id}
    except Exception as e:
        Receipt.objects.filter(id=receipt_id).update(status='error', error=str(e))
        raise
