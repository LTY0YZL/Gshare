from storages.backends.s3boto3 import S3Boto3Storage

class PrivateMediaStorage(S3Boto3Storage):
    bucket_name = "gshare-media-prod"
    region_name = "us-east-2"
    default_acl = "private"
    file_overwrite = False
    custom_domain = None        # force presigned URLs
    querystring_auth = True     # generate signed links