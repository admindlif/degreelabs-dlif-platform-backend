'''r2_upload_benchmark.py
Utility script to benchmark Cloudflare R2 upload speed.

Usage examples:
  python scripts/r2_upload_benchmark.py --size-mib 256 --label laptop
  python scripts/r2_upload_benchmark.py --compare --size-mib 512 --label github-actions
'''

import argparse
import os
import sys
import uuid
import time
from typing import Optional

# Optional dotenv loading
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env if present
except Exception:
    pass

import boto3
from botocore.config import Config as BotocoreConfig
from boto3.s3.transfer import TransferConfig

def _require_env(var_name: str) -> str:
    """Return the value of an environment variable or exit with code 2.
    Only the variable name is printed on error.
    """
    value = os.getenv(var_name)
    if not value:
        print(var_name)
        sys.exit(2)
    return value

def _make_client() -> boto3.client:
    account_id = _require_env('R2_ACCOUNT_ID')
    access_key = _require_env('R2_ACCESS_KEY_ID')
    secret_key = _require_env('R2_SECRET_ACCESS_KEY')
    endpoint = f'https://{account_id}.r2.cloudflarestorage.com'
    cfg = BotocoreConfig(
        signature_version='s3v4',
        max_pool_connections=32,
        retries={'mode': 'standard', 'total_max_attempts': 5},
        tcp_keepalive=True,
        region_name='auto'
    )
    client = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=cfg
    )
    return client

class RandomStream:
    """File‑like object that yields random bytes without storing the whole file."""
    def __init__(self, total_bytes: int, chunk_size: int = 8192):
        self.remaining = total_bytes
        self.chunk_size = chunk_size
    def read(self, n: Optional[int] = None):
        if self.remaining <= 0:
            return b''
        if n is None or n < 0:
            n = self.chunk_size
        to_read = min(n, self.remaining)
        self.remaining -= to_read
        return os.urandom(to_read)
    def __len__(self):
        return self.remaining

def benchmark_upload(size_mib: int, part_mib: int, concurrency: int, label: str) -> tuple:
    client = _make_client()
    bucket = _require_env('R2_BUCKET_NAME')
    key = f'tests/benchmark/{uuid.uuid4()}.bin'
    total_bytes = size_mib * 1024 * 1024
    part_bytes = part_mib * 1024 * 1024
    stream = RandomStream(total_bytes)
    config = TransferConfig(
        multipart_threshold=part_bytes,
        multipart_chunksize=part_bytes,
        max_concurrency=concurrency,
        use_threads=concurrency > 1
    )
    start = time.time()
    try:
        client.upload_fileobj(stream, bucket, key, Config=config)
    finally:
        # Ensure the object is removed regardless of success/failure
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
    end = time.time()
    seconds = end - start
    mib_per_sec = size_mib / seconds if seconds > 0 else 0.0
    return (seconds, mib_per_sec)

def main():
    parser = argparse.ArgumentParser(description='R2 upload benchmark')
    parser.add_argument('--size-mib', type=int, default=256,
                        help='Total size of the test file in MiB')
    parser.add_argument('--part-mib', type=int, default=32,
                        help='Multipart part size in MiB')
    parser.add_argument('--concurrency', type=int, default=16,
                        help='Number of concurrent upload threads')
    parser.add_argument('--label', type=str, default='unlabeled',
                        help='Label to include in the output')
    parser.add_argument('--compare', action='store_true',
                        help='Run sequential (concurrency=1) then the requested concurrency')
    args = parser.parse_args()

    runs = []
    if args.compare:
        runs.append((1, f"{args.label}_seq"))
    runs.append((args.concurrency, args.label))

    results = []
    for conc, lbl in runs:
        seconds, speed = benchmark_upload(args.size_mib, args.part_mib, conc, lbl)
        results.append((lbl, args.size_mib, args.part_mib, conc, seconds, speed))
        print(f"{lbl} | {args.size_mib} | {args.part_mib} | {conc} | {seconds:.3f} | {speed:.3f}")
    if args.compare and len(results) == 2:
        seq_speed = results[0][5]
        par_speed = results[1][5]
        ratio = par_speed / seq_speed if seq_speed else 0.0
        print(f"speedup | {ratio:.3f}")

if __name__ == '__main__':
    main()
