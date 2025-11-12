# utils/aws_s3.py
import boto3
from botocore.config import Config
from django.conf import settings
import uuid

def get_s3_client():
    region = settings.AWS_S3_REGION_NAME
    key    = settings.AWS_ACCESS_KEY_ID
    secret = settings.AWS_SECRET_ACCESS_KEY

    if not key or not secret:
        raise RuntimeError("AWS keys missing in settings.py")

    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )

def get_bucket_and_region():
    bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None) or getattr(settings, "AWS_S3_BUCKET_NAME", None)
    region = settings.AWS_S3_REGION_NAME
    if not bucket:
        raise RuntimeError("Set AWS_STORAGE_BUCKET_NAME (or AWS_S3_BUCKET_NAME) in settings.py")
    return bucket, region

# upload image for chat functionality
def upload_image_to_aws(file, folder='chat', expire_seconds=3600):
    s3_client = get_s3_client()
    bucket, region = get_bucket_and_region()
    folder = (folder or 'chat').strip('/')
    key = f"{folder}/{uuid.uuid4()}_{file.name}"
    s3_client.upload_fileobj(
        file,
        bucket,
        key,
        ExtraArgs={'ContentType': getattr(file, 'content_type', 'application/octet-stream')}
    )
    # generate presigned GET URL
    url = s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': key},
        ExpiresIn=expire_seconds
    )
    return url