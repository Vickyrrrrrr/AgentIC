import os
import boto3
from botocore.exceptions import NoCredentialsError
from botocore.client import Config

# Standard S3 / MinIO Configuration
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "") # e.g., http://localhost:9000 
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")     # e.g., minioadmin
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")     # e.g., minioadmin
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "agentic-artifacts")

# Fallback for Cloudflare R2 specific format if still used
if not S3_ENDPOINT_URL and os.getenv("R2_ACCOUNT_ID"):
    account_id = os.getenv("R2_ACCOUNT_ID")
    S3_ENDPOINT_URL = f"https://{account_id}.r2.cloudflarestorage.com"
    S3_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
    S3_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
    S3_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "agentic-artifacts")

def get_s3_client():
    if not S3_ENDPOINT_URL or not S3_ACCESS_KEY:
        return None
        
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1", # Required for MinIO signature v4
        config=Config(signature_version="s3v4", s3={'addressing_style': 'path'}),
    )

def upload_artifact_to_cloud(local_file_path: str, destination_name: str) -> str:
    """
    Uploads a compiled artifact (.gds, .pdf, .vcd) to an S3-compatible service (MinIO/R2/AWS).
    Returns the public/signed URL or empty string if disabled.
    """
    s3 = get_s3_client()
    if not s3:
        return "" # Silently fallback to local-only mode
        
    if not os.path.exists(local_file_path):
        return ""
        
    try:
        s3.upload_file(local_file_path, S3_BUCKET_NAME, destination_name)
        
        # Return a constructed public URL for MinIO
        # Using a localhost port 9000 for local dev display, or resolving from endpoint
        base_url = S3_ENDPOINT_URL.replace("http://minio:9000", "http://localhost:9000")
        return f"{base_url}/{S3_BUCKET_NAME}/{destination_name}"
    except Exception as e:
        print(f"Failed to upload {destination_name} to cloud storage: {e}")
        return ""

def generate_presigned_download_url(file_key: str, expiration=3600) -> str:
    """Generates a secure temporary download link for users."""
    s3 = get_s3_client()
    if not s3:
        return ""
        
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": file_key},
            ExpiresIn=expiration
        )
        return url
    except Exception as e:
        print(f"Failed to generate signed URL for {file_key}: {e}")
        return ""
