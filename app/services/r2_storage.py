from pathlib import Path
import logging
import time

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings


logger = logging.getLogger(__name__)


# Botocore-level retries for transient network/TLS failures.
_R2_CLIENT_CONFIG = Config(
    signature_version="s3v4",
    retries={
        "total_max_attempts": 10,
        "mode": "standard",
    },
    connect_timeout=30,
    read_timeout=300,
    tcp_keepalive=True,
)


# Use conservative multipart settings for large recordings.
#
# boto3 defaults can upload several parts concurrently. For large video files,
# particularly from WSL/local networks, multiple parallel TLS connections can
# be less reliable.
_R2_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=64 * 1024 * 1024,   # 64 MB
    multipart_chunksize=16 * 1024 * 1024,   # 16 MB per part
    max_concurrency=1,
    use_threads=False,
)


_R2_UPLOAD_ATTEMPTS = 3


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
        config=_R2_CLIENT_CONFIG,
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
    path = Path(local_path)

    if not path.is_file():
        raise FileNotFoundError(
            f"R2 upload source file does not exist: {path}"
        )

    for attempt in range(1, _R2_UPLOAD_ATTEMPTS + 1):
        client = get_r2_client()

        try:
            client.upload_file(
                str(path),
                settings.r2_bucket_name,
                object_key,
                ExtraArgs={
                    "ContentType": content_type,
                },
                Config=_R2_TRANSFER_CONFIG,
            )

            return

        except (BotoCoreError, ClientError):
            if attempt >= _R2_UPLOAD_ATTEMPTS:
                raise

            delay_seconds = 2 ** attempt

            # Do not log the exception text here because botocore exceptions
            # can contain multipart upload URLs/query parameters.
            logger.warning(
                "R2 upload attempt %s/%s failed for object %s; "
                "retrying in %s seconds",
                attempt,
                _R2_UPLOAD_ATTEMPTS,
                object_key,
                delay_seconds,
            )

            time.sleep(delay_seconds)


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
        error_code = exc.response.get(
            "Error", {}
        ).get("Code", "")

        if error_code in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return False

        raise