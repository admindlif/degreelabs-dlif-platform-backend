from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings


def get_r2_client():
    required = {
        "R2_ENDPOINT": settings.r2_endpoint,
        "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
        "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
        "R2_BUCKET_NAME": settings.r2_bucket_name,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            f"Missing Cloudflare R2 configuration: {', '.join(missing)}"
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def upload_bytes(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> None:
    client = get_r2_client()

    client.put_object(
        Bucket=settings.r2_bucket_name,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )


def upload_file(
    local_path: str | Path,
    object_key: str,
    content_type: str = "video/mp4",
) -> None:
    client = get_r2_client()

    client.upload_file(
        str(local_path),
        settings.r2_bucket_name,
        object_key,
        ExtraArgs={
            "ContentType": content_type,
        },
    )


def generate_presigned_get_url(
    object_key: str,
    expires_in: int | None = None,
) -> str:
    client = get_r2_client()

    expiry = (
        expires_in
        if expires_in is not None
        else settings.r2_presigned_url_expiry_seconds
    )

    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": object_key,
        },
        ExpiresIn=expiry,
    )


def delete_object(object_key: str) -> None:
    client = get_r2_client()

    client.delete_object(
        Bucket=settings.r2_bucket_name,
        Key=object_key,
    )

def object_exists(object_key: str) -> bool:
    client = get_r2_client()

    try:
        client.head_object(
            Bucket=settings.r2_bucket_name,
            Key=object_key,
        )
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")

        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False

        raise
