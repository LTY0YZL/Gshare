import os
import boto3
from botocore.config import Config

def get_s3_client():
    region = os.getenv("AWS_S3_REGION_NAME", "us-east-2")
    cfg = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=cfg,
    )

def get_bucket_and_region():
    return (
        os.getenv("AWS_S3_BUCKET_NAME", "gshare-media-prod"),
        os.getenv("AWS_S3_REGION_NAME", "us-east-2"),
    )