"""
This script loads embeddings data into a PostgreSQL database using the pgvector extension.
It reads the embeddings data from CSV files, creates a PostgreSQL database and transaction
tables (if missing), and loads embeddings data into corresponding tables.

Database credentials are read from environment variables (injected via Kubernetes Secret),
no external secret management service is required.
"""

from os import environ
from timeit import default_timer
from psycopg import connect
from pandas import read_csv
from pgvector.psycopg import register_vector

ANOMALIES="embeddings_anomalies.csv"
TRANSACTIONS=(
    "embeddings_transactions_01.csv,"
    "embeddings_transactions_02.csv,"
    "embeddings_transactions_03.csv"
)

def get_db_config():
    """
    Retrieves database configuration from environment variables.
    In-cluster: SERVICE_NAME / NAMESPACE (k8s service FQDN) or DBHOST / DBPORT.
    """
    config = {}
    config["DBHOST"] = environ.get("DBHOST", "")
    config["DBPORT"] = environ.get("DBPORT", "5432")
    config["DBUSER"] = environ.get("DBUSER", "")
    config["DBPASS"] = environ.get("DBPASS", "")
    config["DBNAME"] = environ.get("DBNAME", "")
    config["SERVICE_NAME"] = environ.get("SERVICE_NAME", "")
    config["NAMESPACE"] = environ.get("NAMESPACE", "")
    missing = [k for k in ("DBHOST", "DBUSER", "DBPASS", "DBNAME") if not config[k]]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    return config

def connect_to_postgres(host, port, user, pwd, dbname=None):
    """
    Establishes a connection to a PostgreSQL database.
    """
    conn_string = f"host={host} port={port} user={user} password={pwd}"
    if dbname:
        conn_string += f" dbname='{dbname}'"
    return connect(conn_string, autocommit=True)

def create_database(conn, dbname):
    """
    Creates a new database if it doesn't already exist.
    """
    cur = conn.cursor()
    cur.execute(f"SELECT 1 FROM pg_database WHERE datname = '{dbname}';")
    if cur.fetchone() is None:
        print(f"Database '{dbname}' does not exist. Creating it...")
        cur.execute(f"CREATE DATABASE {dbname};")

def create_tables(conn):
    """
    Creates new tables if they don't already exist.
    """
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute((
        "CREATE TABLE IF NOT EXISTS transaction "
        "(id BIGSERIAL PRIMARY KEY, embedding vector(847));"
    ))
    cur.execute((
        "CREATE TABLE IF NOT EXISTS transaction_anomalies "
        "(id BIGSERIAL PRIMARY KEY, embedding vector(847));"
    ))

def check_if_records_exist(conn, table_name):
    """
    Checks if there are any records in the specified table.
    """
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table_name};")
    count = cur.fetchone()[0]
    return count > 0

def insert_to_postgres(conn, table_name, embeddings):
    """
    Inserts embeddings data into the specified table using the pgvector extension.
    """
    # register pgvector extension
    register_vector(conn)
    print(f"Loading {len(embeddings)} rows")
    cur = conn.cursor()
    # build the copy statement using table_name
    query = f"COPY {table_name} (embedding) FROM STDIN WITH (FORMAT BINARY)"

    with cur.copy(query) as copy:
        copy.set_types(["vector"])

        for i, embedding in enumerate(embeddings):
            # show progress
            if i % 10000 == 0:
                print(".", end="", flush=True)

            copy.write_row([embedding])

            # flush data
            while conn.pgconn.flush() == 1:
                pass
    print("\nSuccess!")

def main():
    """
    Main function to load embeddings data into the PostgreSQL database.
    """
    conn = None
    database_missing = False
    table_missing = True

    try:
        # Attempt to retrieve database configuration from environment
        secret = get_db_config()

        # Determine the host based on the environment
        if "KUBERNETES_SERVICE_HOST" in environ:
            # We're inside a Kubernetes pod
            host = f"{secret['SERVICE_NAME']}.{secret['NAMESPACE']}"
        else:
            # We're outside the cluster, use the direct database host
            host = secret["DBHOST"]
    except Exception as e:
        print(f"Error retrieving database configuration: {e}")

    if secret:
        try:
            # Attempt to connect to the database
            conn = connect_to_postgres(host,
                secret["DBPORT"],
                secret["DBUSER"],
                secret["DBPASS"],
                secret["DBNAME"]
            )
        except Exception as e:
            if "database" in str(e) and "does not exist" in str(e):
                print(f"Database {secret['DBNAME']} does not exist")
                database_missing = True
            else:
                print(f"Error connecting to the database: {e}")

    if database_missing:
        try:
            # Reconnect without database name
            if conn:
                conn.close()
            conn = connect_to_postgres(host,
                secret["DBPORT"],
                secret["DBUSER"],
                secret["DBPASS"]
            )

            # Create database
            create_database(conn, secret["DBNAME"])
            print(f"Database {secret['DBNAME']} created successfully")

            # Attempt to connect again after creating the database
            if conn:
                conn.close()
            conn = connect_to_postgres(host,
                secret["DBPORT"],
                secret["DBUSER"],
                secret["DBPASS"],
                secret["DBNAME"]
            )
        except Exception as e:
            print(f"Error creating the database: {e}")

    elif conn:
        try:
            if (check_if_records_exist(conn, "transaction")
                or check_if_records_exist(conn, "transaction_anomalies")):
                print("Records already exist in the database. Skipping insertion.")
                table_missing = False
        except Exception as e:
            print(f"Error checking the tables: {e}")

    if conn and table_missing:
        try:
            print("Creating tables and inserting data...")
            create_tables(conn)

            anomalies = environ.get("EMBEDDINGS_ANOMALIES_FILES", ANOMALIES)
            transactions = environ.get("EMBEDDINGS_TRANSACTIONS_FILES", TRANSACTIONS)

            for url in anomalies.split(","):
                df = read_csv(url)
                insert_to_postgres(conn, "transaction_anomalies", df.to_numpy())

            for url in transactions.split(","):
                df = read_csv(url)
                insert_to_postgres(conn, "transaction", df.to_numpy())
        except Exception as e:
            print(f"An error occurred during execution: {e}")

    if conn:
        conn.close()

if __name__ == "__main__":
    start = default_timer()
    main()
    end = default_timer()
    print(f"Total time to insert vectors: {end - start:.2f} seconds")
