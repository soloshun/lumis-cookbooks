"""S3-compatible artifact storage (SeaweedFS locally) for models and cached datasets."""

import io
import logging
from functools import lru_cache
from typing import Any

import joblib
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class S3Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S3_", env_file=".env", extra="ignore")

    endpoint_url: str = "http://localhost:8333"
    access_key: str = "gridcast"
    secret_key: str = "gridcast-local-secret"
    bucket: str = "gridcast-models"
    region: str = "us-east-1"


@lru_cache
def _client(endpoint: str, access_key: str, secret_key: str, region: str):  # noqa: ANN202
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
        aws_secret_access_key=secret_key, region_name=region,
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


class ArtifactStore:
    def __init__(self, settings: S3Settings | None = None) -> None:
        self.settings = settings or S3Settings()
        self.client = _client(self.settings.endpoint_url, self.settings.access_key,
                              self.settings.secret_key, self.settings.region)

    def ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self.client.list_buckets().get("Buckets", [])}
        if self.settings.bucket not in existing:
            self.client.create_bucket(Bucket=self.settings.bucket)
            log.info("created bucket", extra={"bucket": self.settings.bucket})

    def uri(self, key: str) -> str:
        return f"s3://{self.settings.bucket}/{key}"

    def put_bytes(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.settings.bucket, Key=key, Body=data)
        return self.uri(key)

    def get_bytes(self, uri_or_key: str) -> bytes:
        key = uri_or_key.removeprefix(f"s3://{self.settings.bucket}/")
        return self.client.get_object(Bucket=self.settings.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.settings.bucket, Key=key)
            return True
        except Exception:
            return False

    def put_object(self, key: str, obj: Any) -> tuple[str, int]:
        buffer = io.BytesIO()
        joblib.dump(obj, buffer, compress=3)
        data = buffer.getvalue()
        return self.put_bytes(key, data), len(data)

    def get_object(self, uri: str) -> Any:
        return joblib.load(io.BytesIO(self.get_bytes(uri)))
