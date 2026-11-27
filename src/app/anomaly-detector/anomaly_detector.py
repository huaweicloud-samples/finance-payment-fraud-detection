"""
This script detects anomalies in payments data using several machine learning techniques.
It reads input data from an object storage bucket and transforms into embeddings using
Standard Scaler and Sentence Transformer, then compares them to model data stored in
PostgreSQL. Finally, the enriched dataset with cosine similarity as anomaly signal
(between 0 and 1) is stored as output data back to the object storage bucket.
"""

from os import environ, path, makedirs
from timeit import default_timer
from concurrent.futures import ProcessPoolExecutor
from pandas import get_dummies, to_datetime, concat, read_csv
from numpy import concatenate, array, ndarray, pad
from psycopg import connect
from sklearn.preprocessing import StandardScaler
from sentence_transformers import SentenceTransformer
from kubernetes import client as k8s_client, config as k8s_config
from obs import ObsClient
from warnings import warn

def get_config_map_values(config_map_name = "config-map"):
    """
    Retrieves configuration values from a Kubernetes ConfigMap.
    """
    if not load_kubernetes_config():
        return None

    v1 = k8s_client.CoreV1Api()
    namespace = get_namespace("spf-app-anomaly-detector")

    if not namespace:
        print("Failed to find namespace")
        return None

    try:
        config_map = v1.read_namespaced_config_map(config_map_name, namespace)
        return config_map.data
    except k8s_client.ApiException as e:
        print(f"Error reading ConfigMap: {e}")
        return None

def load_kubernetes_config():
    """
    Attempts to load the Kubernetes configuration.
    """
    try:
        # Try to load in-cluster configuration
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        # If that fails, try to load kubeconfig file
        try:
            k8s_config.load_kube_config()
        except k8s_config.ConfigException:
            print("Failed to load Kubernetes configuration")
            return False
    return True

def get_namespace(prefix):
    """
    Retrieves the namespace based on the provided prefix.
    """
    if not load_kubernetes_config():
        return None

    v1 = k8s_client.CoreV1Api()
    try:
        namespaces = v1.list_namespace()
        for ns in namespaces.items:
            if ns.metadata.name.startswith(prefix):
                return ns.metadata.name
    except k8s_client.ApiException as e:
        print(f"Error listing namespaces: {e}")
    return None

def build_obs_client():
    """
    Builds an OBS client from environment variables or in-cluster default.
    """
    endpoint = environ.get("OBS_ENDPOINT", "https://obs.cn-north-4.myhuaweicloud.com")
    access_key = environ.get("OBS_ACCESS_KEY", "")
    secret_key = environ.get("OBS_SECRET_KEY", "")
    if access_key and secret_key:
        return ObsClient(access_key_id=access_key, secret_access_key=secret_key, server=endpoint)
    # 依赖节点 IAM 委托（op_svc_cce agency → OBS OperateAccess）
    return ObsClient(server=endpoint)

def list_obs_files(obs_client, bucket_name, prefix):
    """
    Lists all files in the specified bucket and path.
    """
    resp = obs_client.listObjects(bucket_name, prefix=prefix, max_keys=100)
    if resp.status < 300:
        contents = resp.body.contents or []
        return [{"Key": c.key, "LastModified": c.last_modified} for c in contents]
    print(f"Error listing objects: {resp.errorCode} {resp.errorMessage}")
    return []

def download_obs_file(obs_client, bucket_name, object_key, local_file_path):
    """
    Downloads a file from the object storage bucket to a local path.
    """
    local_folder_path = path.split(local_file_path)[0]
    print(local_folder_path)
    makedirs(local_folder_path, exist_ok=True)
    resp = obs_client.getObject(bucket_name, object_key)
    if resp.status < 300:
        with open(local_file_path, "wb") as f:
            f.write(resp.body.response.read())
        print(f"File '{local_file_path}' downloaded from '{bucket_name}/{object_key}'")
    else:
        raise RuntimeError(f"Failed to download {object_key}: {resp.errorCode} {resp.errorMessage}")

def upload_obs_file(obs_client, bucket_name, local_file_path, object_key):
    """
    Uploads a file to the object storage bucket.
    """
    with open(local_file_path, "rb") as f:
        resp = obs_client.putContent(bucket_name, object_key, f.read())
    if resp.status < 300:
        print(f"File '{local_file_path}' uploaded to '{bucket_name}/{object_key}'")
    else:
        raise RuntimeError(f"Failed to upload {object_key}: {resp.errorCode} {resp.errorMessage}")

def encode_batch(batch):
    """
    Encodes a batch of text using the Sentence Transformer model.
    """
    model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
    return model.encode(batch)

def create_embeddings(df):
    """
    Creates embeddings for textual features using the Sentence Transformer model.
    """
    # Separate numerical, categorical, text and timestamps features
    numerical_features   = ["billing_zip", "billing_latitude", "billing_longitude", "order_price"]
    categorical_features = ["billing_state", "billing_country", "product_category"]
    textual_features     = [
        "customer_name", "billing_city", "billing_street",
        "customer_email", "billing_phone", "ip_address"
    ]
    timestamp_features   = ["EVENT_TIMESTAMP", "LABEL_TIMESTAMP"]

    # Feature scaling for numerical values
    scaler = StandardScaler()
    df[numerical_features] = scaler.fit_transform(df[numerical_features])

    # One-hot encoding for categorical features
    encoded_categorical_features = get_dummies(df[categorical_features], drop_first=True)
    encoded_categorical_features = encoded_categorical_features.astype(float)

    # Preprocess timestamp features
    for col in timestamp_features:
        df[col] = to_datetime(df[col])
        for unit in ['year', 'month', 'day', 'hour', 'minute', 'second']:
            df[f"{col}_{unit}"] = getattr(df[col].dt, unit)

    new_timestamp_features = [
        col for col in df.columns
        if col.startswith("EVENT_TIMESTAMP_") or col.startswith("LABEL_TIMESTAMP_")
    ]

    # Combine numerical, categorical and timestamp features
    combined_features = concat([
        df[numerical_features], encoded_categorical_features, df[new_timestamp_features]
    ], axis=1)
    print(combined_features.head())

    # Generate embeddings for textual features
    start1 = default_timer()
    # Parallel processing
    batch_size = 1000
    num_workers = 3  # Adjust based on your pod's CPU resources

    # First, create the batches
    batches = [
        df[textual_features].fillna("").sum(axis=1).iloc[i:i+batch_size].tolist()
        for i in range(0, len(df), batch_size)
    ]

    # Then process them within the context manager
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        # Add progress tracking
        total_batches = len(batches)
        text_embeddings = []
        for i, result in enumerate(executor.map(encode_batch, batches)):
            text_embeddings.append(result)
            if (i + 1) % 10 == 0:  # Print progress every 10 batches
                print(f"Processed {i + 1}/{total_batches} batches")

    text_embeddings = concatenate(text_embeddings)
    end1 = default_timer()
    print(f"Embeddings generated in {end1 - start1:.2f} seconds")

    # Combine numerical and categorical embeddings
    embeddings = concatenate([combined_features.values, text_embeddings], axis=1)

    print("Embeddings")
    print(embeddings[:5,:])
    return embeddings

def connect_to_database(dbname, dbuser, dbpass, dbhost, dbport):
    """
    Connects to a PostgreSQL database using the provided configuration.
    """
    try:
        return connect(f"host={dbhost} port={dbport} dbname={dbname} user={dbuser} password={dbpass}")
    except Exception as e:
        print(f"Error while connecting to PostgreSQL: {e}")
        raise

def is_transaction_anomaly(conn, df, embeddings, target_dim=847):
    """
    Checks if the given embeddings are anomalies based on the transaction anomalies table
    and returns the original dataframe with anomaly scores.
    """
    cursor = conn.cursor()
    scores = []
    query = "SELECT MAX(1 - (embedding <=> %s::vector)) FROM transaction_anomalies"

    # Ensure embeddings are the target dimensions
    if isinstance(embeddings, ndarray):
        current_dim = embeddings.shape[1]
        if current_dim != target_dim:
            warn(f"Adjusting embeddings from {current_dim} to {target_dim} dimensions")
            if current_dim < target_dim:
                # Pad embeddings to target dimensions
                padding_size = target_dim - current_dim
                embeddings = pad(
                    embeddings,
                    ((0, 0), (0, padding_size)),
                    mode='constant',
                    constant_values=0
                )
            elif current_dim > target_dim:
                # Truncate to target dimensions
                embeddings = embeddings[:, :target_dim]

    try:
        for embedding in embeddings:
            cursor.execute(query, (embedding.tolist(),))
            results = cursor.fetchall()
            scores.append(results[0][0])
    except Exception as e:
        print(f"Error processing embeddings: {str(e)}")
        raise
    finally:
        if cursor:
            cursor.close()

    # Add the scores as a new column to the dataframe
    df['anomaly_score'] = scores
    return df

def main():
    """
    Main function to execute the anomaly detection process.
    """
    # Get values from ConfigMap
    config_map_values = get_config_map_values()
    if not config_map_values:
        # Fallback: ConfigMap values are also injected as env vars via envFrom
        config_map_values = {
            k: environ.get(k) for k in (
                "OBS_BUCKET_NAME", "OBS_PATH_INPUT", "OBS_PATH_OUTPUT",
                "DBNAME", "DBUSER", "DBPASS", "DBHOST", "DBPORT",
                "SERVICE_NAME", "SERVICE_PORT", "NAMESPACE"
            )
        }

    # Get the values from the ConfigMap
    bucket_name = config_map_values.get("OBS_BUCKET_NAME")
    obs_path_input = config_map_values.get("OBS_PATH_INPUT")
    obs_path_output = config_map_values.get("OBS_PATH_OUTPUT")

    # Local path
    data_folder_path = "./data/"

    # Initialize the object storage client
    obs_client = build_obs_client()

    # get object storage file details
    obs_files = list_obs_files(obs_client, bucket_name, obs_path_input)

    # get csv file names only
    csv_keys = []
    for obs_file in obs_files:
        if obs_file["Key"].endswith(".csv"):
            csv_keys.append(obs_file["Key"])

    # connect to postgres and register pgvector if missing
    if "KUBERNETES_SERVICE_HOST" in environ:
        host = config_map_values.get("SERVICE_NAME") + "." + config_map_values.get("NAMESPACE")
        port = config_map_values.get("SERVICE_PORT")
    else:
        host = config_map_values.get("DBHOST")
        port = config_map_values.get("DBPORT")
    conn = connect_to_database(
        config_map_values.get("DBNAME"),
        config_map_values.get("DBUSER"),
        config_map_values.get("DBPASS"),
        host, port
    )

    # for every object storage file
    for csv_key in csv_keys:
        # set local file path
        local_file_path = path.join(data_folder_path, csv_key)
        # download object storage file to local file path
        download_obs_file(obs_client, bucket_name, csv_key, local_file_path)
        # Convert file to pandas dataframe
        df = read_csv(local_file_path)
        if df is not None:
            start1 = default_timer()

            # Create embeddings
            embeddings = create_embeddings(df)
            embeddings = array(embeddings, dtype=float)

            # Get DataFrame with anomaly scores
            df_with_scores = is_transaction_anomaly(conn, df, embeddings)

            # Create output filename for the scored data
            output_filename = path.splitext(local_file_path)[0] + '_scored.csv'

            # Save the scored DataFrame to CSV
            df_with_scores.to_csv(output_filename, index=False)

            # Upload the scored file to object storage
            scored_key = csv_key.replace(obs_path_input, obs_path_output)
            scored_key = path.splitext(scored_key)[0] + '_scored.csv'
            upload_obs_file(obs_client, bucket_name, output_filename, scored_key)
            print(f"Scored file saved as '{scored_key}'")

            end1 = default_timer()

            # Print time to process a file
            print(f"File '{csv_key}' processed in {end1 - start1:.2f} seconds")

    if conn:
        conn.close()

if __name__ == "__main__":
    start = default_timer()
    main()
    end = default_timer()
    # Print total time in seconds
    print(f"Total time to process all files: {end - start:.2f} seconds")
