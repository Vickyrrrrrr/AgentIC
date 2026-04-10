import os
import boto3
from botocore.exceptions import NoCredentialsError
from botocore.client import Config

# Standard S3 / MinIO Configuration
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")   # internal Docker URL e.g. http://minio:9000
S3_ACCESS_KEY   = os.getenv("S3_ACCESS_KEY",   "")   # e.g. minioadmin
S3_SECRET_KEY   = os.getenv("S3_SECRET_KEY",   "")   # e.g. minioadmin
S3_BUCKET_NAME  = os.getenv("S3_BUCKET_NAME",  "agentic-artifacts")

# Public-facing URL that remote users can actually reach.
# Set MINIO_PUBLIC_URL in .env to your server's public address, e.g.:
#   MINIO_PUBLIC_URL=http://your-server-ip:9000
#   MINIO_PUBLIC_URL=https://storage.yourdomain.com
# Falls back to localhost:9000 for local development.
MINIO_PUBLIC_URL = os.getenv(
    "MINIO_PUBLIC_URL",
    S3_ENDPOINT_URL.replace("http://minio:9000", "http://localhost:9000") if S3_ENDPOINT_URL else "http://localhost:9000",
)

# Fallback for Cloudflare R2 specific format if still used
if not S3_ENDPOINT_URL and os.getenv("R2_ACCOUNT_ID"):
    account_id   = os.getenv("R2_ACCOUNT_ID")
    S3_ENDPOINT_URL = f"https://{account_id}.r2.cloudflarestorage.com"
    S3_ACCESS_KEY   = os.getenv("R2_ACCESS_KEY", "")
    S3_SECRET_KEY   = os.getenv("R2_SECRET_KEY", "")
    S3_BUCKET_NAME  = os.getenv("R2_BUCKET_NAME", "agentic-artifacts")
    MINIO_PUBLIC_URL = S3_ENDPOINT_URL  # R2 is already public-facing


def get_s3_client():
    if not S3_ENDPOINT_URL or not S3_ACCESS_KEY:
        return None

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",  # Required for MinIO signature v4
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def upload_artifact_to_cloud(local_file_path: str, destination_name: str) -> str:
    """
    Uploads a compiled artifact (.gds, .pdf, .vcd) to an S3-compatible service (MinIO/R2/AWS).
    Returns the public/signed URL or empty string if disabled.
    """
    s3 = get_s3_client()
    if not s3:
        return ""  # Silently fallback to local-only mode

    if not os.path.exists(local_file_path):
        return ""

    try:
        s3.upload_file(local_file_path, S3_BUCKET_NAME, destination_name)
        # Use MINIO_PUBLIC_URL so remote users get a reachable link
        return f"{MINIO_PUBLIC_URL.rstrip('/')}/{S3_BUCKET_NAME}/{destination_name}"
    except Exception as e:
        print(f"Failed to upload {destination_name} to cloud storage: {e}")
        return ""


def generate_presigned_download_url(file_key: str, expiration: int = 3600) -> str:
    """
    Generates a secure temporary download link for users.

    MinIO generates presigned URLs using S3_ENDPOINT_URL (the internal Docker
    address).  We rewrite the host portion to MINIO_PUBLIC_URL so the link
    actually works for users connecting from outside the Docker network.
    """
    s3 = get_s3_client()
    if not s3:
        return ""

    try:
        # Generate presigned URL pointing at the internal MinIO address first
        internal_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": file_key},
            ExpiresIn=expiration,
        )

        # Rewrite internal host (http://minio:9000) → public host
        # so the URL is valid for users outside the Docker network.
        internal_base = S3_ENDPOINT_URL.rstrip("/")
        public_base   = MINIO_PUBLIC_URL.rstrip("/")
        if internal_base and internal_base in internal_url:
            return internal_url.replace(internal_base, public_base, 1)

        return internal_url
    except Exception as e:
        print(f"Failed to generate signed URL for {file_key}: {e}")
        return ""
