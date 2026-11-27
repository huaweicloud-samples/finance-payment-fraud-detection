"""
Downloads embeddings CSV files from an OBS bucket to the local working directory.
Used by the init container before loading vectors into PostgreSQL.

Environment variables:
  OBS_ENDPOINT        OBS endpoint (default: https://obs.cn-north-4.myhuaweicloud.com)
  OBS_ACCESS_KEY      Access key (optional if node has IAM agency)
  OBS_SECRET_KEY      Secret key (optional if node has IAM agency)
  OBS_BUCKET          Bucket holding the embeddings files
  OBS_EMBEDDINGS_PREFIX  Object key prefix (default: embeddings/)
"""

from os import environ, makedirs, path
from obs import ObsClient

FILES = [
    "embeddings_anomalies.csv",
    "embeddings_transactions_01.csv",
    "embeddings_transactions_02.csv",
    "embeddings_transactions_03.csv",
]

def main():
    endpoint = environ.get("OBS_ENDPOINT", "https://obs.cn-north-4.myhuaweicloud.com")
    bucket = environ.get("OBS_BUCKET")
    if not bucket:
        raise RuntimeError("OBS_BUCKET environment variable is required")
    prefix = environ.get("OBS_EMBEDDINGS_PREFIX", "embeddings/")

    access_key = environ.get("OBS_ACCESS_KEY", "")
    secret_key = environ.get("OBS_SECRET_KEY", "")
    if access_key and secret_key:
        client = ObsClient(access_key_id=access_key, secret_access_key=secret_key, server=endpoint)
    else:
        client = ObsClient(server=endpoint)

    makedirs("./data", exist_ok=True)
    for name in FILES:
        key = prefix + name
        local = path.join("./data", name)
        print(f"Downloading {bucket}/{key} -> {local}")
        resp = client.getObject(bucket, key)
        if resp.status < 300:
            with open(local, "wb") as f:
                f.write(resp.body.response.read())
            print(f"  saved {local}")
        else:
            raise RuntimeError(f"Failed to download {key}: {resp.errorCode} {resp.errorMessage}")

if __name__ == "__main__":
    main()
