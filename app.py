from flask import (
    Flask,
    render_template,
    jsonify,
    request,
    session,
    redirect,
    url_for,
    flash,
)
import psutil
import sqlite3
import threading
import time
import datetime
import os
from dotenv import load_dotenv
import paramiko
import json
import re
from concurrent.futures import ThreadPoolExecutor
import psycopg2
import psycopg2.extras  # For dictionary cursor
import logging
import uuid

# --- Configuration ---
load_dotenv()
DATABASE_PATH = os.path.join("data", "sys_stats.db")  # Relevant for SQLite
DATABASE_TYPE = os.getenv("DATABASE_TYPE", "sqlite").lower()

# PostgreSQL specific (if DATABASE_TYPE is 'postgresql')
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DBNAME = os.getenv("POSTGRES_DBNAME")

# --- SMTP Configuration for Email Alerts ---
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # Default to 587 for TLS
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
EMAIL_FROM_ADDRESS = os.getenv(
    "EMAIL_FROM_ADDRESS",
    f"falconeye-alerts@{(os.uname().nodename if hasattr(os, 'uname') else 'localhost')}",
)


HISTORICAL_DATA_COLLECTION_INTERVAL = 60  # Default value
DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR = "DATA_GATHERING_INTERVAL_SECONDS"
MAX_HISTORICAL_ENTRIES = 1440
SSH_TIMEOUT = 25

# --- Logger Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(threadName)s - %(module)s - %(funcName)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Determine the actual collection interval
current_collection_interval = HISTORICAL_DATA_COLLECTION_INTERVAL
data_gathering_interval_str = os.getenv(DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR)
if data_gathering_interval_str:
    try:
        interval_seconds = int(data_gathering_interval_str)
        if interval_seconds > 0:
            current_collection_interval = interval_seconds
            logger.info(
                f"Using custom data gathering interval: {current_collection_interval} seconds."
            )
        else:
            logger.warning(
                f"{DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR} ('{data_gathering_interval_str}') must be a positive integer. Using default: {current_collection_interval}s."
            )
    except ValueError:
        logger.warning(
            f"Invalid value for {DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR} ('{data_gathering_interval_str}'). Expected an integer. Using default: {current_collection_interval}s."
        )

DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR = "DETAIL_VIEW_REFRESH_INTERVAL_MS"
DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS = 3000
SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR = "SERVER_LIST_REFRESH_INTERVAL_MS"
DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS = 15000

# --- Collector Status Globals ---
collector_status_info = {
    "last_cycle_start_time": None,
    "last_cycle_end_time": None,
    "last_cycle_duration_seconds": None,
    "servers_configured_count": 0,
    "servers_processed_in_last_cycle": 0,
    "servers_failed_in_last_cycle": 0,
    "configured_server_names": [],  # List of names of all configured servers
    "status_updated_at": None,  # Timestamp of when this status dict was last updated
}
collector_status_lock = threading.Lock()

# --- Alerting Configuration ---
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
# Minimum percentage of expected data points in a window required to evaluate an alert
MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE = float(
    os.getenv("MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE", "0.8")
)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_default_secret_key")

# --- Credentials ---
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")


# --- Database Connection Helper ---
def get_db_connection():
    """
    Establishes a database connection based on DATABASE_TYPE.
    Returns a connection object and a cursor.
    The caller is responsible for closing the connection.
    """
    if DATABASE_TYPE == "sqlite":
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        conn = sqlite3.connect(DATABASE_PATH)
        # For SQLite, the default cursor is fine.
        # For consistency in return type, we can return conn.cursor()
        # but often it's created just before use.
        return conn, conn.cursor()
    elif DATABASE_TYPE == "postgresql":
        if not all([POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DBNAME]):
            raise ValueError(
                "Missing PostgreSQL connection details in environment variables."
            )
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DBNAME,
        )
        # Using DictCursor for easier column access by name
        return conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    else:
        raise ValueError(f"Unsupported DATABASE_TYPE: {DATABASE_TYPE}")


# --- Database Setup & Local Stats ---
def init_db():
    """Initializes the database schema based on DATABASE_TYPE."""
    try:
        conn, cursor = get_db_connection()
        logger.info(f"Initializing database ({DATABASE_TYPE})...")
        if DATABASE_TYPE == "sqlite":
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stats (
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    server_name TEXT NOT NULL DEFAULT 'local',
                    cpu_percent REAL,
                    ram_percent REAL,
                    disk_percent REAL
                )
            """
            )
            # Index for faster queries, especially for historical data retrieval and pruning
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_server_timestamp ON stats (server_name, timestamp);"
            )
        elif DATABASE_TYPE == "postgresql":
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stats (
                    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    server_name TEXT NOT NULL,
                    cpu_percent REAL,
                    ram_percent REAL,
                    disk_percent REAL
                )
            """
            )
            # Index for faster queries on server_name and timestamp
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_server_timestamp ON stats (server_name, timestamp);"
            )

            # --- Create alerts table ---
            logger.info(
                f"Creating/ensuring 'alerts' table exists for {DATABASE_TYPE}..."
            )
            if DATABASE_TYPE == "sqlite":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_name TEXT NOT NULL,
                        server_name TEXT NOT NULL,
                        resource_type TEXT NOT NULL,
                        threshold_percentage REAL NOT NULL,
                        time_window_minutes INTEGER NOT NULL,
                        emails TEXT NOT NULL,
                        is_enabled BOOLEAN NOT NULL DEFAULT 1,
                        last_triggered_at TIMESTAMP NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """
                )
                # Example: Index on alert_name and server_name for faster lookups
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_name_server ON alerts (alert_name, server_name);"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_enabled_resource ON alerts (is_enabled, resource_type, server_name);"
                )
            elif DATABASE_TYPE == "postgresql":
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id SERIAL PRIMARY KEY,
                        alert_name TEXT NOT NULL,
                        server_name TEXT NOT NULL,
                        resource_type TEXT NOT NULL,
                        threshold_percentage REAL NOT NULL,
                        time_window_minutes INTEGER NOT NULL,
                        emails TEXT NOT NULL,
                        is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        last_triggered_at TIMESTAMPTZ NULL,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    )
                """
                )
                # Example: Index on alert_name and server_name for faster lookups
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_name_server ON alerts (alert_name, server_name);"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_enabled_resource ON alerts (is_enabled, resource_type, server_name);"
                )
            logger.info(f"'alerts' table ready for {DATABASE_TYPE}.")

        conn.commit()
        logger.info(
            f"Database schema initialization, including 'alerts' table, completed successfully for {DATABASE_TYPE}."
        )
    except Exception as e:
        logger.error(
            f"Error during database initialization (including alerts table): {e}",
            exc_info=True,
        )
        # Depending on the error, you might want to raise it or handle it differently
    finally:
        if "conn" in locals() and conn:
            cursor.close()
            conn.close()


def store_stats(server_name, cpu, ram, disk):
    """Stores system stats into the configured database, including server_name."""
    logger.debug(
        f"Attempting to store stats for server: {server_name} (CPU: {cpu}%, RAM: {ram}%, Disk: {disk}%)"
    )
    try:
        conn, cursor = get_db_connection()

        if DATABASE_TYPE == "sqlite":
            cursor.execute(
                "INSERT INTO stats (server_name, cpu_percent, ram_percent, disk_percent) VALUES (?, ?, ?, ?)",
                (server_name, cpu, ram, disk),
            )
            # Prune old entries for the specific server
            cursor.execute(
                f"""
                DELETE FROM stats
                WHERE server_name = ? AND timestamp NOT IN (
                    SELECT timestamp
                    FROM stats
                    WHERE server_name = ?
                    ORDER BY timestamp DESC
                    LIMIT {MAX_HISTORICAL_ENTRIES}
                )
            """,
                (server_name, server_name),
            )
        elif DATABASE_TYPE == "postgresql":
            cursor.execute(
                "INSERT INTO stats (server_name, cpu_percent, ram_percent, disk_percent) VALUES (%s, %s, %s, %s)",
                (server_name, cpu, ram, disk),
            )
            # Prune old entries for the specific server
            # Using a simpler approach similar to SQLite for now, adjust if performance issues arise
            # Note: For very large tables, the subquery with ROW_NUMBER() might be more performant.
            # However, the LIMIT clause in a subquery for NOT IN might not be directly supported or efficient
            # in all PostgreSQL versions in the exact same way as SQLite.
            # A common and effective way for PostgreSQL:
            cursor.execute(
                f"""
                DELETE FROM stats
                WHERE ctid IN (
                    SELECT ctid
                    FROM (
                        SELECT ctid, ROW_NUMBER() OVER (PARTITION BY server_name ORDER BY timestamp DESC) as rn
                        FROM stats
                        WHERE server_name = %s
                    ) s
                    WHERE rn > %s
                )
            """,
                (server_name, MAX_HISTORICAL_ENTRIES),
            )
            # Alternative PostgreSQL pruning (closer to SQLite's, but check performance on large datasets):
            # cursor.execute(f'''
            #     DELETE FROM stats
            #     WHERE server_name = %s AND timestamp NOT IN (
            #         SELECT timestamp
            #         FROM stats
            #         WHERE server_name = %s
            #         ORDER BY timestamp DESC
            #         LIMIT %s
            #     )
            # ''', (server_name, server_name, MAX_HISTORICAL_ENTRIES))
        conn.commit()
        logger.info(f"Successfully stored stats for server: {server_name}")
    except Exception as e:
        logger.error(
            f"Error in store_stats for server '{server_name}': {e}", exc_info=True
        )
    finally:
        if "conn" in locals() and conn:
            cursor.close()
            conn.close()


def get_current_stats():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    ram_stats = psutil.virtual_memory()
    ram_percent = ram_stats.percent
    try:
        disk_stats_obj = psutil.disk_usage("/")  # disk_stats_obj for clarity
        disk_percent = disk_stats_obj.percent
        disk_total_gb = round(disk_stats_obj.total / (1024**3), 2)
        disk_used_gb = round(disk_stats_obj.used / (1024**3), 2)
    except FileNotFoundError:
        disk_percent = 0.0
        disk_total_gb = 0.0  # Ensure these are defined
        disk_used_gb = 0.0
        logger.warning(
            "Could not get disk usage for '/'. Defaulting to 0.", exc_info=True
        )  # Add exc_info for context

    return {
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "ram_total_gb": round(ram_stats.total / (1024**3), 2),
        "ram_used_gb": round(ram_stats.used / (1024**3), 2),
        "disk_percent": disk_percent,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "timestamp": datetime.datetime.now().isoformat(),
    }


def historical_data_collector():
    logger.info("Starting historical data collection thread...")
    # Fetch server configurations once, assuming they don't change at runtime.
    # If they can change, this should be moved inside the loop.
    try:
        server_configs_map = parse_remote_server_configs()
        logger.info(
            f"Historical data collector initialized with {len(server_configs_map)} remote server(s) configurations loaded."
        )

        # Consolidate server names for collector_status_info
        temp_configured_server_names = []
        has_explicit_local_entry = False
        for idx, cfg in server_configs_map.items():
            name = cfg.get("name", cfg.get("host", f"ServerIndex_{idx}"))
            temp_configured_server_names.append(name)
            if cfg.get("is_local", False):
                has_explicit_local_entry = True
            if name == "local":  # Also catches if a remote server is named 'local'
                has_explicit_local_entry = True

        # Add 'local' if no entry explicitly marks itself as local or is named 'local'
        if not has_explicit_local_entry and "local" not in temp_configured_server_names:
            logger.info(
                "Adding 'local' to configured_server_names for collector status as it was not explicitly defined or marked."
            )
            temp_configured_server_names.append("local")

        unique_server_names = sorted(
            list(set(temp_configured_server_names))
        )  # Sort for consistent ordering

        with collector_status_lock:
            collector_status_info["configured_server_names"] = unique_server_names
            collector_status_info["servers_configured_count"] = len(unique_server_names)
            logger.info(
                f"Collector status initialized. Monitored servers: {collector_status_info['configured_server_names']}"
            )

    except Exception as e:
        logger.critical(
            f"Could not parse server configurations for historical data collector: {e}",
            exc_info=True,
        )
        logger.info(
            "Historical data collector will default to local stats only due to parsing error."
        )
        server_configs_map = {}
        with collector_status_lock:
            collector_status_info["servers_configured_count"] = 1
            collector_status_info["configured_server_names"] = [
                "local"
            ]  # Default to 'local'

    while True:
        cycle_start_time = datetime.datetime.now()
        logger.info(f"Starting new collection cycle at {cycle_start_time.isoformat()}")

        processed_server_names_in_cycle = set()

        with collector_status_lock:
            collector_status_info["last_cycle_start_time"] = (
                cycle_start_time.isoformat()
            )
            collector_status_info["servers_processed_in_last_cycle"] = 0
            collector_status_info["servers_failed_in_last_cycle"] = 0
            # Update server_configs_map derived info here if it can change dynamically per cycle
            # For now, assuming it's loaded once at startup. If it can change, re-populate:
            # collector_status_info["servers_configured_count"] = len(server_configs_map) + (1 if 'local' not in [cfg.get('name') for cfg in server_configs_map.values()])
            # collector_status_info["configured_server_names"] = [cfg.get('name', cfg.get('host', f"ServerIndex_{idx}")) for idx, cfg in server_configs_map.items()]
            # if 'local' not in collector_status_info["configured_server_names"]: collector_status_info["configured_server_names"].append('local')

        local_processed_successfully = False
        try:
            # 1. Collect and store local server stats
            try:
                logger.debug("Attempting to collect local stats.")
                local_stats = get_current_stats()
                store_stats(
                    "local",
                    local_stats["cpu_percent"],
                    local_stats["ram_percent"],
                    local_stats["disk_percent"],
                )
                logger.info(
                    "Successfully collected and initiated storage for local server stats."
                )
                local_processed_successfully = True
                processed_server_names_in_cycle.add("local")
            except Exception as e_local:
                logger.error(
                    f"Error collecting or storing local server stats: {e_local}",
                    exc_info=True,
                )
                # No specific increment for failed local here, handled by servers_failed_in_last_cycle if 'local' is part of servers loop

            with collector_status_lock:
                collector_status_info["servers_processed_in_last_cycle"] += 1
                if not local_processed_successfully:
                    collector_status_info["servers_failed_in_last_cycle"] += 1

            # 2. Collect and store remote server stats
            # The server_configs_map is a dictionary where keys are original indices (e.g., "1", "2")
            # and values are the server configuration dictionaries.
            for server_index, remote_server_config in server_configs_map.items():
                server_display_name = remote_server_config.get(
                    "name",
                    remote_server_config.get("host", f"ServerIndex_{server_index}"),
                )

                if server_display_name in processed_server_names_in_cycle:
                    logger.warning(
                        f"Skipping duplicate configuration for server name: {server_display_name} in this cycle."
                    )
                    continue

                try:
                    # Skip collection for servers marked as 'is_local: true' in config,
                    # as their stats are (or should be) collected by the local collector above.
                    # This check helps avoid redundant SSH to localhost if it's also in remote configs.
                    # This logic assumes 'is_local' marked servers are not re-processed here.
                    # If 'local' stats are handled separately (as above), ensure it's not double-counted or missed.
                    if remote_server_config.get("is_local", False):
                        logger.debug(
                            f"Skipping historical data collection for '{server_display_name}' as it's marked local; already processed or psutil direct."
                        )
                        # It was processed as 'local' above, so no increment to servers_processed here for this entry.
                        # However, we should add it to the set to prevent processing another config with the same name.
                        processed_server_names_in_cycle.add(server_display_name)
                        continue

                    logger.info(
                        f"HDC: Processing remote server. Index: '{server_index}', Configured Name: '{server_display_name}'"
                    )
                    remote_processed_successfully = False
                    logger.info(
                        f"Attempting to fetch historical stats for remote server: {server_display_name}"
                    )
                    processed_server_names_in_cycle.add(server_display_name)
                    try:
                        remote_stats = get_remote_server_stats(
                            remote_server_config, server_configs_map
                        )
                        if remote_stats and remote_stats.get("status") == "online":
                            logger.info(
                                f"HDC: Stats fetched for Index: '{server_index}', Configured Name: '{server_display_name}', Result Name: '{remote_stats.get('name')}'. Preparing to store."
                            )
                            # logger.info(f"Storing historical stats for {remote_stats['name']}: CPU {remote_stats['cpu_percent']}%, RAM {remote_stats['ram_percent']}%, Disk {remote_stats['disk_percent']}%") # Original log
                            store_stats(
                                remote_stats["name"],
                                remote_stats["cpu_percent"],
                                remote_stats["ram_percent"],
                                remote_stats["disk_percent"],
                            )
                            remote_processed_successfully = True
                        else:
                            error_msg = (
                                remote_stats.get("error_message", "Unknown error")
                                if remote_stats
                                else "Remote stats object is None"
                            )
                            status_msg = (
                                remote_stats.get("status", "unknown")
                                if remote_stats
                                else "unknown"
                            )
                            result_name = (
                                remote_stats.get("name", "N/A")
                                if remote_stats
                                else "N/A"
                            )
                            logger.warning(
                                f"HDC: Stats fetch NOT successful for Index: '{server_index}', Configured Name: '{server_display_name}', Result Name: '{result_name}', Status: '{status_msg}', Error: {error_msg}"
                            )
                            # logger.warning(f"Not storing historical stats for {server_display_name} due to status: '{status_msg}'. Error: {error_msg}") # Original log
                    except (
                        Exception
                    ) as e_remote_fetch:  # Catch errors from get_remote_server_stats itself
                        logger.error(
                            f"HDC: Exception during get_remote_server_stats for Index: '{server_index}', Configured Name: '{server_display_name}'. Error: {e_remote_fetch}",
                            exc_info=True,
                        )
                        # remote_processed_successfully remains False

                    with collector_status_lock:
                        collector_status_info["servers_processed_in_last_cycle"] += 1
                        if not remote_processed_successfully:
                            collector_status_info["servers_failed_in_last_cycle"] += 1

                except (
                    Exception
                ) as e_remote_loop:  # Catch errors in the loop logic for a server
                    logger.error(
                        f"Unhandled exception while processing remote server {server_display_name} in historical_data_collector loop: {e_remote_loop}",
                        exc_info=True,
                    )
                    with (
                        collector_status_lock
                    ):  # Still count it as processed, but failed
                        collector_status_info[
                            "servers_processed_in_last_cycle"
                        ] += 1  # Or only if not already counted
                        collector_status_info["servers_failed_in_last_cycle"] += 1

        except (
            Exception
        ) as e_main_loop:  # Errors in the main try block of the cycle, outside server loops
            logger.critical(
                f"Critical error in historical_data_collector main loop: {e_main_loop}",
                exc_info=True,
            )
            # This type of error might mean the cycle didn't complete, so status might be partially updated.
            time.sleep(10)

        cycle_end_time = datetime.datetime.now()
        cycle_duration = (cycle_end_time - cycle_start_time).total_seconds()
        with collector_status_lock:
            collector_status_info["last_cycle_end_time"] = cycle_end_time.isoformat()
            collector_status_info["last_cycle_duration_seconds"] = cycle_duration
            collector_status_info["status_updated_at"] = (
                datetime.datetime.now().isoformat()
            )

        logger.info(
            f"Collection cycle finished in {cycle_duration:.2f} seconds. Processed: {collector_status_info['servers_processed_in_last_cycle']}, Failed: {collector_status_info['servers_failed_in_last_cycle']}."
        )

        # --- Run Alert Evaluation ---
        try:
            eval_cycle_id = uuid.uuid4()
            logger.info(
                f"Initiating alert evaluation (Cycle ID: {eval_cycle_id}) after collection cycle."
            )
            evaluate_alerts(eval_cycle_id)
        except Exception as e_alert_eval:
            logger.error(
                f"Error during evaluate_alerts call from historical_data_collector (Cycle ID: {eval_cycle_id}): {e_alert_eval}",
                exc_info=True,
            )
        # --- End Alert Evaluation ---

        logger.info(
            f"Sleeping for {current_collection_interval} seconds before next collection cycle."
        )
        time.sleep(current_collection_interval)


# --- Remote Server Stat Collection ---
def get_ssh_connection_args(server_conf_entry):
    """Helper to build common SSH connection arguments from a config entry."""
    args = {
        "hostname": server_conf_entry["host"],
        "port": int(server_conf_entry.get("port", 22)),
        "username": server_conf_entry["user"],
        "timeout": SSH_TIMEOUT,
    }
    if server_conf_entry.get("key_path"):
        expanded_key_path = os.path.expanduser(server_conf_entry["key_path"])
        args["key_filename"] = expanded_key_path
        if server_conf_entry.get("key_passphrase"):
            args["passphrase"] = server_conf_entry["key_passphrase"]
    elif server_conf_entry.get("password"):
        args["password"] = server_conf_entry["password"]
    else:
        return None  # Auth method missing
    return args


def get_remote_server_stats(target_server_config, all_server_configs_map):
    """
    Connects to a remote Ubuntu server, potentially via a jump server,
    and fetches system stats using standard Linux commands.
    `all_server_configs_map` is a dictionary mapping server index (string) to its config.
    """
    target_client = paramiko.SSHClient()
    target_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    jump_client = None  # Initialize jump client

    name = target_server_config.get("name", target_server_config["host"])
    disk_to_monitor = target_server_config.get("disk_path", "/")

    base_result = {
        "name": name,
        "host": target_server_config["host"],
        "status": "offline",
        "is_local": target_server_config.get("is_local", False),
        "cpu_percent": 0.0,
        "cpu_cores": 0,
        "cpu_model": "N/A",
        "ram_percent": 0.0,
        "ram_total_gb": 0.0,
        "ram_used_gb": 0.0,
        "disk_percent": 0.0,
        "disk_total_gb": 0.0,
        "disk_used_gb": 0.0,
        "error_message": None,
    }

    try:
        target_ssh_args = get_ssh_connection_args(target_server_config)
        if not target_ssh_args:
            base_result["error_message"] = (
                "Target server authentication details (password/key) missing in configuration."
            )
            logger.warning(
                f"Auth error for target {name}: Auth method missing. Config: {target_server_config}"
            )  # Log more info
            return base_result

        sock = None  # Socket for connection (direct or via jump)

        if target_server_config.get("jump_server_index"):
            jump_server_index_str = str(target_server_config["jump_server_index"])
            jump_server_conf = all_server_configs_map.get(jump_server_index_str)

            if not jump_server_conf:
                base_result["error_message"] = (
                    f"Jump server configuration for index '{jump_server_index_str}' not found in the provided server map."
                )
                logger.error(
                    f"Error for {name}: Jump server index '{jump_server_index_str}' invalid or missing from map. Target config: {target_server_config}"
                )
                return base_result

            logger.info(
                f"Connecting to {name} ({target_server_config['host']}) via jump server: {jump_server_conf.get('name', jump_server_conf['host'])}"
            )
            jump_client = paramiko.SSHClient()
            jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            jump_ssh_args = get_ssh_connection_args(jump_server_conf)
            if not jump_ssh_args:
                base_result["error_message"] = (
                    f"Jump server '{jump_server_conf.get('name', jump_server_conf['host'])}' authentication details (password/key) missing."
                )
                logger.warning(
                    f"Auth error for jump server {jump_server_conf.get('name', jump_server_conf['host'])}: Auth method missing. Jump config: {jump_server_conf}"
                )
                if jump_client:
                    jump_client.close()
                return base_result

            jump_client.connect(**jump_ssh_args)

            # Create a transport channel through the jump server to the target
            transport = jump_client.get_transport()
            dest_addr = (
                target_server_config["host"],
                int(target_server_config.get("port", 22)),
            )
            src_addr = (
                "127.0.0.1",
                0,
            )  # Let the system pick a source port on the jump server
            try:
                sock = transport.open_channel(
                    "direct-tcpip", dest_addr, src_addr, timeout=SSH_TIMEOUT
                )
            except paramiko.SSHException as e:
                base_result["error_message"] = (
                    f"Failed to open SSH channel via jump server {jump_server_conf.get('name', jump_server_conf['host'])}: {e}"
                )
                logger.error(
                    f"Channel error for {name} via {jump_server_conf.get('name', jump_server_conf['host'])}: {e}",
                    exc_info=True,
                )
                if jump_client:
                    jump_client.close()
                return base_result

            target_ssh_args["sock"] = sock  # Use this channel for the target connection
            logger.info(
                f"SSH channel established to {name} via jump server {jump_server_conf.get('name', jump_server_conf['host'])}."
            )
        else:
            logger.info(
                f"Connecting directly to {name} ({target_server_config['host']})."
            )
            # For direct connection, sock remains None, paramiko handles it.

        target_client.connect(**target_ssh_args)
        logger.info(
            f"Successfully connected to target server: {name} ({target_server_config['host']})."
        )

        # --- Shell commands (same as before) ---
        delimiter = "###STATS_DELIMITER###"
        cpu_usage_cmd = "LC_ALL=C vmstat 1 2 | awk 'END{print 100-$15}'"
        ram_cmd = 'awk \'/^MemTotal:/{total=$2} /^MemAvailable:/{available=$2} END{used=total-available; if (total > 0) printf "%.2f###%.2f###%.2f", (used*100)/total, used/1024/1024, total/1024/1024; else print "ERROR_RAM_CALC";}\' /proc/meminfo'
        disk_cmd = f"LC_ALL=C df -P -B1K '{disk_to_monitor}' | awk 'NR==2 {{printf \"%s###%s###%s\", substr($5, 1, length($5)-1), $3, $2}}'"
        cpu_cores_cmd = "nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo 0"
        cpu_model_cmd = "grep 'model name' /proc/cpuinfo | head -n1 | cut -d: -f2 | xargs 2>/dev/null || echo 'N/A'"

        full_command = f"""
set -e; set -o pipefail;
CPU_USAGE_OUT=$({cpu_usage_cmd} 2>/dev/null || echo "ERROR_CPU_USAGE");
RAM_OUT=$({ram_cmd} 2>/dev/null || echo "ERROR_RAM");
DISK_OUT=$({disk_cmd} 2>/dev/null || echo "ERROR_DISK");
CPU_CORES_OUT=$({cpu_cores_cmd} 2>/dev/null || echo "ERROR_CPU_CORES");
CPU_MODEL_OUT=$({cpu_model_cmd} 2>/dev/null || echo "ERROR_CPU_MODEL");
printf "%s{delimiter}%s{delimiter}%s{delimiter}%s{delimiter}%s" \
    "$CPU_USAGE_OUT" "$RAM_OUT" "$DISK_OUT" "$CPU_CORES_OUT" "$CPU_MODEL_OUT";
"""
        stdin, stdout, stderr = target_client.exec_command(
            full_command, timeout=SSH_TIMEOUT
        )
        # --- Parsing logic (same as before) ---
        raw_output = stdout.read().decode(errors="ignore").strip()
        ssh_stderr_output = stderr.read().decode(errors="ignore").strip()
        ssh_exit_status = stdout.channel.recv_exit_status()

        logger.debug(f"Target {name}: SSH Exit Status: {ssh_exit_status}")
        logger.debug(f"Target {name}: Raw Output from SSH command: '{raw_output}'")
        if ssh_stderr_output:  # Only log if there's actual stderr content
            logger.debug(
                f"Target {name}: Stderr from SSH command execution: '{ssh_stderr_output}'"
            )

        current_error_messages = []
        if ssh_exit_status == 0 and raw_output:
            parts = raw_output.split(delimiter)
            if len(parts) == 5:
                cpu_usage_str, ram_str, disk_str, cpu_cores_str, cpu_model_str = parts
                # Parse CPU Usage
                if "ERROR_CPU_USAGE" in cpu_usage_str or not cpu_usage_str:
                    current_error_messages.append("CPU usage data retrieval failed.")
                else:
                    try:
                        base_result["cpu_percent"] = float(cpu_usage_str)
                    except ValueError:
                        current_error_messages.append(
                            f"Invalid CPU usage value: '{cpu_usage_str}'."
                        )
                # Parse RAM
                if "ERROR_RAM" in ram_str or not ram_str:
                    current_error_messages.append("RAM data retrieval failed.")
                else:
                    ram_parts = ram_str.split("###")
                    if len(ram_parts) == 3:
                        try:
                            base_result["ram_percent"] = float(ram_parts[0])
                            base_result["ram_used_gb"] = float(ram_parts[1])
                            base_result["ram_total_gb"] = float(ram_parts[2])
                        except ValueError:
                            current_error_messages.append(
                                f"Invalid RAM values: '{ram_str}'."
                            )
                    else:
                        current_error_messages.append(
                            f"Unexpected RAM format: '{ram_str}'."
                        )
                # Parse DISK
                if "ERROR_DISK" in disk_str or not disk_str:
                    current_error_messages.append("Disk data retrieval failed.")
                else:
                    disk_parts = disk_str.split("###")
                    if len(disk_parts) == 3:
                        try:
                            base_result["disk_percent"] = float(disk_parts[0])
                            base_result["disk_used_gb"] = round(
                                float(disk_parts[1]) / (1024 * 1024), 2
                            )
                            base_result["disk_total_gb"] = round(
                                float(disk_parts[2]) / (1024 * 1024), 2
                            )
                        except ValueError:
                            current_error_messages.append(
                                f"Invalid Disk values: '{disk_str}'."
                            )
                    else:
                        current_error_messages.append(
                            f"Unexpected Disk format: '{disk_str}'."
                        )
                # Parse CPU Cores
                if "ERROR_CPU_CORES" in cpu_cores_str or not cpu_cores_str:
                    current_error_messages.append("CPU cores data retrieval failed.")
                    base_result["cpu_cores"] = 0
                else:
                    try:
                        base_result["cpu_cores"] = int(cpu_cores_str)
                    except ValueError:
                        current_error_messages.append(
                            f"Invalid CPU cores value: '{cpu_cores_str}'."
                        )
                        base_result["cpu_cores"] = 0
                # Parse CPU Model
                if (
                    "ERROR_CPU_MODEL" in cpu_model_str
                    or not cpu_model_str.strip()
                    or cpu_model_str.strip().upper() == "N/A"
                ):
                    current_error_messages.append(
                        "CPU model data retrieval failed or N/A."
                    )
                    base_result["cpu_model"] = "N/A"
                else:
                    base_result["cpu_model"] = cpu_model_str.strip()

                if not current_error_messages:
                    base_result["status"] = "online"
                else:
                    base_result["status"] = "error"
                    base_result["error_message"] = " | ".join(current_error_messages)
            else:
                base_result["status"] = "error"
                base_result["error_message"] = (
                    f"Output format error. Expected 5 parts, got {len(parts)}. Output: '{raw_output[:150]}...'"
                )
        elif ssh_exit_status != 0:
            base_result["status"] = "error"
            err_msg = f"Remote script execution failed on {name} (exit code: {ssh_exit_status})."
            if ssh_stderr_output:
                err_msg += f" Stderr: {ssh_stderr_output}"
            elif raw_output:
                err_msg += f" Stdout (partial): {raw_output[:100]}..."  # Include some stdout if stderr is empty
            else:
                err_msg += " No stdout or stderr received."
            base_result["error_message"] = err_msg
            logger.warning(
                f"Remote script execution failed for {name}. Exit code: {ssh_exit_status}. Stderr: '{ssh_stderr_output}'. Stdout: '{raw_output[:150]}...'"
            )
        elif (
            not raw_output and ssh_exit_status == 0
        ):  # Command seemingly succeeded but no output
            base_result["status"] = "error"
            base_result["error_message"] = (
                f"No output from remote command on {name}, though script reported success (exit code 0)."
            )
            logger.warning(
                f"No output from remote command for {name} (exit code 0). Stderr: '{ssh_stderr_output}'."
            )
        elif (
            ssh_exit_status != 0 and not raw_output and not ssh_stderr_output
        ):  # Catch all for other command failures
            base_result["status"] = "error"
            base_result["error_message"] = (
                f"Remote command on {name} failed (exit code: {ssh_exit_status}) with no stdout/stderr."
            )
            logger.warning(
                f"Remote command on {name} failed (exit code: {ssh_exit_status}) with no stdout/stderr."
            )

    except paramiko.AuthenticationException as e:
        base_result["status"] = "error"
        base_result["error_message"] = f"Authentication failed for {name}: {str(e)}"
        logger.error(
            f"Authentication failed for {name} ({target_server_config['host']}): {e}",
            exc_info=True,
        )
    except paramiko.SSHException as e:  # Includes timeouts, other SSH layer errors
        base_result["status"] = "error"
        base_result["error_message"] = f"SSH connection error for {name}: {str(e)}"
        logger.error(
            f"SSH error for {name} ({target_server_config['host']}): {e}", exc_info=True
        )
    except Exception as e:  # Catch-all for other unexpected errors
        base_result["status"] = "error"
        base_result["error_message"] = (
            f"A general error occurred while processing {name}: {str(e)}"
        )
        logger.error(
            f"General error processing {name} ({target_server_config['host']}): {e}",
            exc_info=True,
        )
    finally:
        target_client.close()
        if jump_client:
            jump_client.close()

    # --- Final sanitization ---
    for key in [
        "cpu_percent",
        "ram_percent",
        "ram_total_gb",
        "ram_used_gb",
        "disk_percent",
        "disk_total_gb",
        "disk_used_gb",
    ]:
        try:
            base_result[key] = float(base_result.get(key, 0.0))
        except (ValueError, TypeError):
            base_result[key] = 0.0
    try:
        base_result["cpu_cores"] = int(base_result.get("cpu_cores", 0))
    except (ValueError, TypeError):
        base_result["cpu_cores"] = 0
    if (
        not isinstance(base_result.get("cpu_model"), str)
        or not base_result.get("cpu_model", "").strip()
    ):
        base_result["cpu_model"] = "N/A"

    if base_result["status"] == "error" and not base_result["error_message"]:
        base_result["error_message"] = "Unknown error during data retrieval."
    if base_result["error_message"] and base_result["status"] == "error":
        # This log can be redundant if the error was already logged specifically.
        # Kept for a summary, but could be logger.debug if too noisy.
        logger.info(
            f"Final error state for {name} ({target_server_config['host']}): {base_result['error_message']}"
        )
    elif base_result["status"] == "online":
        logger.info(
            f"Successfully fetched stats for {name} ({target_server_config['host']})."
        )
    # logger.debug(f"FINAL base_result for {name}: {base_result}") # Potentially very verbose

    return base_result


def parse_remote_server_configs():
    servers_map = {}
    i = 1
    while True:
        host_var = f"REMOTE_SERVER_{i}_HOST"
        if os.getenv(host_var):
            server_conf = {
                "original_index": i,
                "name": os.getenv(f"REMOTE_SERVER_{i}_NAME", os.getenv(host_var)),
                "host": os.getenv(host_var),
                "port": os.getenv(f"REMOTE_SERVER_{i}_PORT", "22"),
                "user": os.getenv(f"REMOTE_SERVER_{i}_USER"),
                "password": os.getenv(f"REMOTE_SERVER_{i}_PASSWORD"),
                "key_path": os.getenv(f"REMOTE_SERVER_{i}_KEY_PATH"),
                "key_passphrase": os.getenv(f"REMOTE_SERVER_{i}_KEY_PASSPHRASE"),
                "disk_path": os.getenv(f"REMOTE_SERVER_{i}_DISK_PATH", "/"),
                "jump_server_index": os.getenv(f"REMOTE_SERVER_{i}_JUMP_SERVER"),
                "is_local": os.getenv(f"REMOTE_SERVER_{i}_IS_LOCAL", "false").lower()
                == "true",  # New flag
            }
            # Basic validation: user is mandatory for any remote (non-local or local via SSH)
            if not server_conf["user"]:
                logger.warning(
                    f"REMOTE_SERVER_{i}_USER not set for host {server_conf.get('host', 'N/A')}. Skipping server '{server_conf['name']}'."
                )
            # Auth method (pass/key) is mandatory if it's not explicitly an 'is_local' server where SSH might be skipped
            elif (
                not server_conf.get("is_local", False)
                and not server_conf["password"]
                and not server_conf["key_path"]
            ):
                logger.warning(
                    f"Neither PASSWORD nor KEY_PATH set for non-local REMOTE_SERVER_{i} ('{server_conf['name']}'). Skipping this server."
                )
            # Even if 'is_local' is true, if no direct psutil path is implemented, it might still need SSH creds.
            # For now, assume 'is_local' might bypass SSH need, but if creds are missing, it might fail later if SSH is attempted.
            # This part of logic might need refinement based on how 'is_local' is truly handled in stat fetching.
            else:
                servers_map[str(i)] = server_conf
            i += 1
        else:
            break  # No more servers defined by environment variables
    if not servers_map:
        logger.warning("No remote server configurations found or parsed successfully.")
    else:
        logger.info(
            f"Successfully parsed {len(servers_map)} remote server configuration(s)."
        )
    return servers_map


# --- Flask Routes ---
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    detail_refresh_interval_str = os.getenv(DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR)
    detail_refresh_interval = DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS
    if detail_refresh_interval_str:
        try:
            interval = int(detail_refresh_interval_str)
            if interval > 0:
                detail_refresh_interval = interval
            else:
                logger.warning(
                    f"{DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR} is not a positive integer ('{detail_refresh_interval_str}'). Using default: {DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS}ms."
                )
        except ValueError:
            logger.warning(
                f"Invalid value for {DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR} ('{detail_refresh_interval_str}'). Expected an integer. Using default: {DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS}ms."
            )

    server_list_refresh_interval_str = os.getenv(
        SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR
    )
    server_list_refresh_interval = DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS
    if server_list_refresh_interval_str:
        try:
            interval = int(server_list_refresh_interval_str)
            if interval > 0:
                server_list_refresh_interval = interval
            else:
                logger.warning(
                    f"{SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR} is not a positive integer ('{server_list_refresh_interval_str}'). Using default: {DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS}ms."
                )
        except ValueError:
            logger.warning(
                f"Invalid value for {SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR} ('{server_list_refresh_interval_str}'). Expected an integer. Using default: {DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS}ms."
            )

    return render_template(
        "index.html",
        detail_refresh_interval=detail_refresh_interval,
        server_list_refresh_interval=server_list_refresh_interval,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == APP_USERNAME and password == APP_PASSWORD:
            session["logged_in"] = True
            flash("You were successfully logged in!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid credentials. Please try again.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("You were successfully logged out.", "info")
    return redirect(url_for("login"))


@app.route("/api/current_stats")
def api_current_stats():
    return jsonify(get_current_stats())


@app.route("/api/historical_stats")
def api_historical_stats():
    requested_server_name = request.args.get("server_name")

    # Default to 'local' if no server_name is provided or if it's an empty string
    target_server_name = requested_server_name if requested_server_name else "local"

    try:
        conn, cursor = get_db_connection()

        query_sql = ""
        query_params = (target_server_name,)

        if DATABASE_TYPE == "sqlite":
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query_sql = "SELECT timestamp, cpu_percent, ram_percent, disk_percent FROM stats WHERE server_name = ? ORDER BY timestamp ASC"
        elif DATABASE_TYPE == "postgresql":
            # DictCursor is already set by get_db_connection for postgresql
            query_sql = "SELECT timestamp, cpu_percent, ram_percent, disk_percent FROM stats WHERE server_name = %s ORDER BY timestamp ASC"
        else:
            return (
                jsonify(
                    {
                        "error": "Database type not configured correctly for historical stats"
                    }
                ),
                500,
            )

        cursor.execute(query_sql, query_params)
        rows = cursor.fetchall()

        # Prepare data structure for JSON response
        data = {
            "server_name": target_server_name,  # Include the server name in the response
            "labels": [],
            "cpu_data": [],
            "ram_data": [],
            "disk_data": [],
        }

        if DATABASE_TYPE == "sqlite":
            for row in rows:
                data["labels"].append(
                    row["timestamp"]
                )  # Already string or suitable format
                data["cpu_data"].append(row["cpu_percent"])
                data["ram_data"].append(row["ram_percent"])
                data["disk_data"].append(row["disk_percent"])
        elif DATABASE_TYPE == "postgresql":
            for row in rows:
                data["labels"].append(
                    row["timestamp"].isoformat()
                )  # Ensure datetime is JSON serializable
                data["cpu_data"].append(row["cpu_percent"])
                data["ram_data"].append(row["ram_percent"])
                data["disk_data"].append(row["disk_percent"])

        return jsonify(data)

    except Exception as e:
        logger.error(
            f"Error in api_historical_stats for server '{target_server_name}': {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {
                    "error": f"An error occurred while fetching historical stats for {target_server_name}.",
                    "server_name": target_server_name,
                }
            ),
            500,
        )
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


@app.route("/api/remote_servers_stats")
def api_remote_servers_stats():
    requested_host = request.args.get("host")
    server_configs_map = parse_remote_server_configs()
    all_stats = []
    configs_to_process = []

    if requested_host:
        found_config = None
        for config in server_configs_map.values():
            if config["host"] == requested_host:
                found_config = config
                break
        if found_config:
            configs_to_process.append(found_config)
        else:
            # If a specific host is requested but not found, return an empty list.
            return jsonify([])
    else:
        # If no specific host is requested, process all servers.
        configs_to_process = list(server_configs_map.values())

    if not configs_to_process:
        return jsonify([])

    # Adjust max_workers; if only one server, no need for many workers.
    # Using min to handle cases where len(configs_to_process) is small.
    # For a single server, max_workers will be 1.
    # For multiple servers, it will be the number of servers.
    max_workers = min(
        len(configs_to_process), os.cpu_count() or 1
    )  # Ensure at least 1 worker

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Pass the full map to each worker so it can look up its jump server if needed
        future_to_config = {
            executor.submit(get_remote_server_stats, config, server_configs_map): config
            for config in configs_to_process
        }
        for future in future_to_config:
            target_config = future_to_config[future]
            try:
                stats = future.result()
                all_stats.append(stats)
            except Exception as exc:
                server_name = target_config.get("name", "Unknown Server")
                host = target_config.get("host", "N/A")
                logger.error(
                    f"Exception for server {server_name} (Host: {host}) during future.result() in api_remote_servers_stats: {exc}",
                    exc_info=True,
                )
                all_stats.append(
                    {
                        "name": server_name,
                        "host": host,
                        "status": "error",
                        "error_message": f"Task execution failed for {server_name}: {str(exc)}",  # This message is sent to client
                        "cpu_percent": 0,
                        "cpu_cores": 0,
                        "cpu_model": "N/A",
                        "ram_percent": 0,
                        "ram_total_gb": 0,
                        "ram_used_gb": 0,
                        "disk_percent": 0,
                        "disk_total_gb": 0,
                        "disk_used_gb": 0,
                    }
                )
    return jsonify(all_stats)


@app.route("/api/collector_status")
def api_collector_status():
    with collector_status_lock:
        # Create a copy to avoid holding the lock while jsonify processes it
        status_copy = collector_status_info.copy()
    # Add a timestamp for when this API endpoint was called, distinct from status_updated_at
    status_copy["api_fetch_time"] = datetime.datetime.now().isoformat()
    return jsonify(status_copy)


# --- Alerting Helper Functions (Database & Evaluation) ---


def get_stats_for_alert_evaluation(
    db_cursor, server_name, resource_column_name, time_window_minutes
):
    """
    Fetches historical stats for a given server and resource within a time window.
    Returns a list of values (floats).
    The db_cursor provided should be configured for dictionary-like row access.
    For SQLite, ensure conn.row_factory = sqlite3.Row was set before creating the cursor.
    """
    logger.debug(
        f"Fetching stats for alert eval: server='{server_name}', resource='{resource_column_name}', window='{time_window_minutes} mins'"
    )
    values = []
    try:
        if DATABASE_TYPE == "sqlite":
            query = f"""
                SELECT {resource_column_name}
                FROM stats
                WHERE server_name = ? AND timestamp >= datetime('now', '-' || CAST(? AS TEXT) || ' minutes')
                ORDER BY timestamp ASC
            """
            params = (
                server_name,
                time_window_minutes,
            )  # Pass time_window_minutes directly
        elif DATABASE_TYPE == "postgresql":
            query = f"""
                SELECT {resource_column_name}
                FROM stats
                WHERE server_name = %s AND timestamp >= (CURRENT_TIMESTAMP - make_interval(mins => %s))
                ORDER BY timestamp ASC
            """
            params = (server_name, time_window_minutes)
        else:
            logger.error(
                f"Unsupported DATABASE_TYPE '{DATABASE_TYPE}' in get_stats_for_alert_evaluation."
            )
            return None

        db_cursor.execute(query, params)
        rows = db_cursor.fetchall()

        for row in rows:
            val = row[resource_column_name]  # Assumes dict-like row access
            if val is not None:
                values.append(float(val))
        logger.debug(
            f"Found {len(values)} data points for {server_name}/{resource_column_name} in last {time_window_minutes} mins."
        )
        return values
    except Exception as e:
        logger.error(
            f"Error fetching stats for alert eval ({server_name}, {resource_column_name}): {e}",
            exc_info=True,
        )
        return None


import smtplib  # Import at the top of the file is conventional, but here for diff clarity
from email.mime.text import MIMEText


def send_alert_email(
    alert, target_server_name, current_value_or_avg, actual_values_over_window
):
    """
    Sends an email notification for a triggered alert.
    """
    if not SMTP_HOST or not EMAIL_FROM_ADDRESS:
        logger.warning(
            "SMTP_HOST or EMAIL_FROM_ADDRESS not configured. Skipping email notification."
        )
        logger.warning(f"Alert Details (would have been emailed to {alert['emails']}):")
        logger.warning(
            f"  Name: {alert['alert_name']}, Server: {target_server_name}, Resource: {alert['resource_type']}"
        )
        logger.warning(
            f"  Threshold: >{alert['threshold_percentage']}%, Window: {alert['time_window_minutes']}min"
        )
        logger.warning(
            f"  Current Value: {current_value_or_avg:.2f}%, Values in window: {actual_values_over_window}"
        )
        return

    subject = (
        f"FalconEye Alert: {alert['alert_name']} triggered on {target_server_name}"
    )

    # Constructing a more detailed body
    body_lines = [
        f"FalconEye Monitoring System has detected an alert.",
        f"\nAlert Name:         {alert['alert_name']}",
        f"Target Server:      {target_server_name}",
        f"Monitored Resource: {alert['resource_type'].upper()}",
        f"Threshold Set:      > {alert['threshold_percentage']}%",
        f"Time Window:        {alert['time_window_minutes']} minutes",
        f"Configured Emails:  {alert['emails']}",
        f"\nTrigger Details:",
        f"  The {alert['resource_type']} usage on server '{target_server_name}' has consistently been above the threshold of {alert['threshold_percentage']}% for the duration of the {alert['time_window_minutes']}-minute window.",
        f"  Approx. Current Value (at time of trigger): {current_value_or_avg:.2f}%",
        f"  Data points over the window (up to 10 samples): {', '.join(map(lambda x: f'{x:.2f}%', actual_values_over_window[:10]))}{'...' if len(actual_values_over_window) > 10 else ''}",
        f"\nTriggered At: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"\nPlease investigate this issue.",
        f"\n-- FalconEye Monitoring System",
    ]
    body = "\n".join(body_lines)

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM_ADDRESS

    # Handle comma-separated emails string for multiple recipients
    recipients = [
        email.strip() for email in alert["emails"].split(",") if email.strip()
    ]
    if not recipients:
        logger.warning(
            f"No valid recipient emails found for alert ID {alert['id']} ('{alert['alert_name']}'). Skipping email."
        )
        return
    msg["To"] = ", ".join(recipients)  # Comma-separated string for the 'To' header

    try:
        if SMTP_USE_SSL:
            logger.debug(
                f"Connecting to SMTP server {SMTP_HOST}:{SMTP_PORT} using SSL."
            )
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
        else:
            logger.debug(f"Connecting to SMTP server {SMTP_HOST}:{SMTP_PORT}.")
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            if SMTP_USE_TLS:
                logger.debug("Starting TLS with SMTP server.")
                server.starttls()

        if SMTP_USER and SMTP_PASSWORD:
            logger.debug(f"Logging in to SMTP server as {SMTP_USER}.")
            server.login(SMTP_USER, SMTP_PASSWORD)

        logger.debug(f"Sending email to {recipients} from {EMAIL_FROM_ADDRESS}.")
        server.sendmail(EMAIL_FROM_ADDRESS, recipients, msg.as_string())
        server.quit()
        logger.info(
            f"Successfully sent alert email for '{alert['alert_name']}' to {recipients}."
        )

    except smtplib.SMTPAuthenticationError as e:
        logger.error(
            f"SMTP Authentication Error for user {SMTP_USER} on {SMTP_HOST}:{SMTP_PORT}. Please check credentials. Error: {e}",
            exc_info=True,
        )
    except smtplib.SMTPConnectError as e:
        logger.error(
            f"SMTP Connection Error for {SMTP_HOST}:{SMTP_PORT}. Check host/port and network. Error: {e}",
            exc_info=True,
        )
    except smtplib.SMTPServerDisconnected as e:
        logger.error(
            f"SMTP Server Disconnected for {SMTP_HOST}:{SMTP_PORT}. Error: {e}",
            exc_info=True,
        )
    except smtplib.SMTPException as e:
        logger.error(
            f"SMTP Error sending email for alert '{alert['alert_name']}': {e}",
            exc_info=True,
        )
    except ConnectionRefusedError as e:  # More specific network error
        logger.error(
            f"Connection refused by SMTP server {SMTP_HOST}:{SMTP_PORT}. Is the server running and accessible? Error: {e}",
            exc_info=True,
        )
    except TimeoutError as e:  # For timeout on connection or operations
        logger.error(
            f"Timeout connecting or communicating with SMTP server {SMTP_HOST}:{SMTP_PORT}. Error: {e}",
            exc_info=True,
        )
    except Exception as e:  # Catch any other unexpected errors during email sending
        logger.error(
            f"Unexpected error sending email for alert '{alert['alert_name']}': {e}",
            exc_info=True,
        )


def evaluate_alerts(eval_cycle_id):  # Added eval_cycle_id parameter
    logger.info(f"(Cycle ID: {eval_cycle_id}) Starting alert evaluation cycle...")
    conn = None
    cursor = None
    try:
        conn, cursor_main = (
            get_db_connection()
        )  # Renamed to cursor_main to avoid conflict in update

        if DATABASE_TYPE == "sqlite":
            conn.row_factory = sqlite3.Row
            cursor_main = conn.cursor()

        cursor_main.execute("SELECT * FROM alerts WHERE is_enabled = %s", (True,))
        enabled_alerts_rows = cursor_main.fetchall()
        enabled_alerts = [dict(row) for row in enabled_alerts_rows]

        logger.info(
            f"(Cycle ID: {eval_cycle_id}) Found {len(enabled_alerts)} enabled alerts to evaluate."
        )
        if not enabled_alerts:
            logger.info(
                f"(Cycle ID: {eval_cycle_id}) No enabled alerts to evaluate. Cycle finished."
            )  # Added log for empty
            return

        with collector_status_lock:
            all_known_server_names = list(
                collector_status_info.get("configured_server_names", [])
            )

        if not all_known_server_names:
            logger.warning(
                f"(Cycle ID: {eval_cycle_id}) No configured server names in collector_status_info for alert evaluation."
            )
            # Fallback or error? For now, proceed, '*' alerts won't match anything.

        resource_column_map = {
            "cpu": "cpu_percent",
            "ram": "ram_percent",
            "disk": "disk_percent",
        }

        for alert in enabled_alerts:
            logger.info(
                f"(Cycle ID: {eval_cycle_id}) Evaluating alert: '{alert['alert_name']}' (ID: {alert['id']})"
            )
            resource_column = resource_column_map.get(alert["resource_type"])
            if not resource_column:
                logger.warning(
                    f"(Cycle ID: {eval_cycle_id}) Invalid resource_type '{alert['resource_type']}' for alert ID {alert['id']}. Skipping."
                )
                continue

            target_servers = (
                all_known_server_names
                if alert["server_name"] == "*"
                else [alert["server_name"]]
            )
            if not target_servers:
                logger.debug(
                    f"(Cycle ID: {eval_cycle_id}) No target servers for alert ID {alert['id']} (Server pattern: {alert['server_name']}). Skipping."
                )
                continue

            for target_server in target_servers:
                logger.debug(
                    f"(Cycle ID: {eval_cycle_id}) Checking alert ID {alert['id']} for server: '{target_server}'"
                )

                if alert["last_triggered_at"]:
                    last_triggered_dt = alert["last_triggered_at"]
                    if isinstance(last_triggered_dt, str):  # SQLite
                        try:
                            last_triggered_dt = datetime.datetime.fromisoformat(
                                last_triggered_dt.replace(" ", "T")
                            )
                        except (
                            ValueError
                        ):  # Try another common format if fromisoformat fails
                            try:
                                last_triggered_dt = datetime.datetime.strptime(
                                    last_triggered_dt, "%Y-%m-%d %H:%M:%S"
                                )
                            except:
                                try:
                                    last_triggered_dt = datetime.datetime.strptime(
                                        last_triggered_dt, "%Y-%m-%d %H:%M:%S.%f"
                                    )
                                except ValueError:
                                    logger.error(
                                        f"(Cycle ID: {eval_cycle_id}) Unparseable last_triggered_at '{last_triggered_dt}' for alert {alert['id']}. Skipping cooldown.",
                                        exc_info=True,
                                    )
                                    last_triggered_dt = None

                    current_time_for_cooldown = datetime.datetime.now(
                        getattr(last_triggered_dt, "tzinfo", None)
                    )
                    if (
                        last_triggered_dt
                        and (
                            current_time_for_cooldown - last_triggered_dt
                        ).total_seconds()
                        / 60
                        < ALERT_COOLDOWN_MINUTES
                    ):
                        logger.info(
                            f"(Cycle ID: {eval_cycle_id}) Alert ID {alert['id']} for '{target_server}' in cooldown. Last: {alert['last_triggered_at']}. Skipping."
                        )
                        continue

                stats_values = get_stats_for_alert_evaluation(
                    cursor_main,
                    target_server,
                    resource_column,
                    alert["time_window_minutes"],
                )
                if stats_values is None:  # Error already logged in helper
                    logger.warning(
                        f"(Cycle ID: {eval_cycle_id}) Could not retrieve stats for alert ID {alert['id']} on '{target_server}'. Skipping evaluation for this server."
                    )
                    continue

                expected_points = (
                    alert["time_window_minutes"] * 60
                ) / current_collection_interval
                min_points = max(
                    1, int(expected_points * MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE)
                )

                if len(stats_values) < min_points:
                    logger.info(
                        f"(Cycle ID: {eval_cycle_id}) Not enough data for alert ID {alert['id']} on '{target_server}'. Have {len(stats_values)}/{min_points} (expected approx {expected_points:.1f}). Skipping."
                    )
                    continue

                condition_met = all(
                    val > alert["threshold_percentage"] for val in stats_values
                )
                latest_val = stats_values[-1] if stats_values else 0

                if condition_met:
                    logger.warning(
                        f"(Cycle ID: {eval_cycle_id}) ALERT TRIGGERED: '{alert['alert_name']}' (ID: {alert['id']}) for '{target_server}'. Values (last {len(stats_values)}): {stats_values}"
                    )
                    send_alert_email(
                        alert, target_server, latest_val, stats_values
                    )  # Email does not need cycle_id internally

                    update_ts_query = (
                        "UPDATE alerts SET last_triggered_at = %s WHERE id = %s"
                    )
                    now_ts = datetime.datetime.now()

                    temp_conn_update, temp_cursor_update = None, None
                    try:
                        temp_conn_update, temp_cursor_update = get_db_connection()
                        params_update = (
                            (
                                now_ts.strftime("%Y-%m-%d %H:%M:%S.%f")
                                if DATABASE_TYPE == "sqlite"
                                else now_ts
                            ),
                            alert["id"],
                        )
                        temp_cursor_update.execute(update_ts_query, params_update)
                        temp_conn_update.commit()
                        logger.info(
                            f"(Cycle ID: {eval_cycle_id}) Updated last_triggered_at for alert ID {alert['id']} to {now_ts}"
                        )
                    except Exception as e_upd:
                        logger.error(
                            f"(Cycle ID: {eval_cycle_id}) Failed to update last_triggered_at for alert {alert['id']}: {e_upd}",
                            exc_info=True,
                        )
                        if temp_conn_update:
                            temp_conn_update.rollback()
                    finally:
                        if temp_cursor_update:
                            temp_cursor_update.close()
                        if temp_conn_update:
                            temp_conn_update.close()
                else:
                    logger.info(
                        f"(Cycle ID: {eval_cycle_id}) Alert ID {alert['id']} NOT MET for '{target_server}'. Latest: {latest_val} (Threshold: {alert['threshold_percentage']}%)."
                    )
    except Exception as e:
        logger.error(
            f"(Cycle ID: {eval_cycle_id}) Alert evaluation cycle error: {e}",
            exc_info=True,
        )
    finally:
        if cursor_main:
            cursor_main.close()
        if conn:
            conn.close()
        logger.info(f"(Cycle ID: {eval_cycle_id}) Alert evaluation cycle finished.")


# --- Alerting API Endpoints ---


@app.route("/api/alerts", methods=["POST"])
def create_alert():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid input. JSON payload required."}), 400

        required_fields = [
            "alert_name",
            "server_name",
            "resource_type",
            "threshold_percentage",
            "time_window_minutes",
            "emails",
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        alert_name = data["alert_name"]
        server_name = data["server_name"]
        resource_type = data["resource_type"]
        threshold_percentage = data["threshold_percentage"]
        time_window_minutes = data["time_window_minutes"]
        emails = data["emails"]
        is_enabled = data.get("is_enabled", True)  # Defaults to True

        # --- Input Validation ---
        if not isinstance(alert_name, str) or not alert_name.strip():
            return jsonify({"error": "alert_name must be a non-empty string."}), 400
        if not isinstance(server_name, str) or not server_name.strip():
            return jsonify({"error": "server_name must be a non-empty string."}), 400
        if resource_type not in ["cpu", "ram", "disk"]:
            return (
                jsonify(
                    {"error": "resource_type must be one of 'cpu', 'ram', or 'disk'."}
                ),
                400,
            )
        try:
            threshold_percentage = float(threshold_percentage)
            if not (0 <= threshold_percentage <= 100):
                raise ValueError("Threshold percentage must be between 0 and 100.")
        except ValueError as e:
            return jsonify({"error": f"Invalid threshold_percentage: {e}"}), 400
        try:
            time_window_minutes = int(time_window_minutes)
            if time_window_minutes <= 0:
                raise ValueError("Time window minutes must be a positive integer.")
        except ValueError as e:
            return jsonify({"error": f"Invalid time_window_minutes: {e}"}), 400
        if (
            not isinstance(emails, str) or not emails.strip()
        ):  # Basic check, more robust email validation could be added
            return (
                jsonify(
                    {"error": "emails must be a non-empty string (comma-separated)."}
                ),
                400,
            )
        if not isinstance(is_enabled, bool):
            return jsonify({"error": "is_enabled must be a boolean."}), 400
        # Validate email format (simple regex)
        email_list = [email.strip() for email in emails.split(",")]
        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        for email in email_list:
            if not re.match(email_regex, email):
                return jsonify({"error": f"Invalid email format: '{email}'"}), 400
        valid_emails_string = ",".join(email_list)

        conn, cursor = get_db_connection()
        insert_query = ""
        if DATABASE_TYPE == "sqlite":
            insert_query = """
                INSERT INTO alerts (alert_name, server_name, resource_type, threshold_percentage, time_window_minutes, emails, is_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
        elif DATABASE_TYPE == "postgresql":
            insert_query = """
                INSERT INTO alerts (alert_name, server_name, resource_type, threshold_percentage, time_window_minutes, emails, is_enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
            """

        params = (
            alert_name,
            server_name,
            resource_type,
            threshold_percentage,
            time_window_minutes,
            valid_emails_string,
            is_enabled,
        )

        cursor.execute(insert_query, params)
        alert_id = (
            cursor.lastrowid if DATABASE_TYPE == "sqlite" else cursor.fetchone()[0]
        )
        conn.commit()

        logger.info(
            f"Alert '{alert_name}' created successfully with ID: {alert_id} for server '{server_name}'."
        )
        return (
            jsonify(
                {
                    "message": "Alert created successfully",
                    "alert_id": alert_id,
                    "alert_name": alert_name,
                    "server_name": server_name,
                    "resource_type": resource_type,
                    "threshold_percentage": threshold_percentage,
                    "time_window_minutes": time_window_minutes,
                    "emails": valid_emails_string,
                    "is_enabled": is_enabled,
                }
            ),
            201,
        )

    except psycopg2.Error as e_pg:  # Specific error for PostgreSQL
        logger.error(f"PostgreSQL error during alert creation: {e_pg}", exc_info=True)
        return jsonify({"error": f"Database error: {e_pg.pgerror or str(e_pg)}"}), 500
    except sqlite3.Error as e_sql:  # Specific error for SQLite
        logger.error(f"SQLite error during alert creation: {e_sql}", exc_info=True)
        return jsonify({"error": f"Database error: {str(e_sql)}"}), 500
    except Exception as e:
        logger.error(f"Error creating alert: {e}", exc_info=True)
        return (
            jsonify(
                {"error": "An unexpected error occurred while creating the alert."}
            ),
            500,
        )
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


@app.route("/api/alerts", methods=["GET"])
def get_all_alerts():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn, cursor = get_db_connection()
        query = "SELECT id, alert_name, server_name, resource_type, threshold_percentage, time_window_minutes, emails, is_enabled, last_triggered_at, created_at FROM alerts ORDER BY created_at DESC"

        if DATABASE_TYPE == "sqlite":
            conn.row_factory = sqlite3.Row  # To access columns by name
            cursor = conn.cursor()
        # For PostgreSQL, DictCursor is already set by get_db_connection

        cursor.execute(query)
        alerts_raw = cursor.fetchall()

        alerts = []
        for row in alerts_raw:
            alert = dict(
                row
            )  # Convert Row object (SQLite) or DictRow (psycopg2) to dict
            # Ensure datetime objects are JSON serializable
            if alert.get("last_triggered_at") and isinstance(
                alert["last_triggered_at"], (datetime.datetime, datetime.date)
            ):
                alert["last_triggered_at"] = alert["last_triggered_at"].isoformat()
            if alert.get("created_at") and isinstance(
                alert["created_at"], (datetime.datetime, datetime.date)
            ):
                alert["created_at"] = alert["created_at"].isoformat()
            alerts.append(alert)

        logger.info(f"Retrieved {len(alerts)} alerts.")
        return jsonify(alerts), 200

    except Exception as e:
        logger.error(f"Error retrieving all alerts: {e}", exc_info=True)
        return jsonify({"error": "An error occurred while retrieving alerts."}), 500
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


@app.route("/api/alerts/<int:alert_id>", methods=["GET"])
def get_alert_by_id(alert_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn, cursor = get_db_connection()
        query = "SELECT id, alert_name, server_name, resource_type, threshold_percentage, time_window_minutes, emails, is_enabled, last_triggered_at, created_at FROM alerts WHERE id = "
        query += "%s" if DATABASE_TYPE == "postgresql" else "?"

        if DATABASE_TYPE == "sqlite":
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

        cursor.execute(query, (alert_id,))
        row = cursor.fetchone()

        if row:
            alert = dict(row)
            if alert.get("last_triggered_at") and isinstance(
                alert["last_triggered_at"], (datetime.datetime, datetime.date)
            ):
                alert["last_triggered_at"] = alert["last_triggered_at"].isoformat()
            if alert.get("created_at") and isinstance(
                alert["created_at"], (datetime.datetime, datetime.date)
            ):
                alert["created_at"] = alert["created_at"].isoformat()
            logger.info(f"Retrieved alert with ID: {alert_id}.")
            return jsonify(alert), 200
        else:
            logger.warning(f"Alert with ID {alert_id} not found.")
            return jsonify({"error": "Alert not found"}), 404

    except Exception as e:
        logger.error(f"Error retrieving alert with ID {alert_id}: {e}", exc_info=True)
        return jsonify({"error": "An error occurred while retrieving the alert."}), 500
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


@app.route("/api/alerts/<int:alert_id>", methods=["PUT"])
def update_alert(alert_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid input. JSON payload required."}), 400

        # Fetch existing alert to see what fields are being updated
        conn, cursor = get_db_connection()

        # Check if alert exists
        select_query = "SELECT * FROM alerts WHERE id = "
        select_query += "%s" if DATABASE_TYPE == "postgresql" else "?"
        if (
            DATABASE_TYPE == "sqlite"
        ):  # Set row_factory for this specific cursor if needed
            original_row_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()  # Recreate cursor if row_factory changed

        cursor.execute(select_query, (alert_id,))
        existing_alert = cursor.fetchone()

        if (
            DATABASE_TYPE == "sqlite" and original_row_factory is not None
        ):  # Reset row_factory if changed
            conn.row_factory = original_row_factory

        if not existing_alert:
            logger.warning(f"Attempt to update non-existent alert with ID {alert_id}.")
            return jsonify({"error": "Alert not found"}), 404

        # Start building the update query
        update_fields = []
        params = []

        # Validate and add fields to update
        if "alert_name" in data:
            alert_name = data["alert_name"]
            if not isinstance(alert_name, str) or not alert_name.strip():
                return jsonify({"error": "alert_name must be a non-empty string."}), 400
            update_fields.append(
                "alert_name = %s" if DATABASE_TYPE == "postgresql" else "alert_name = ?"
            )
            params.append(alert_name)

        if "server_name" in data:
            server_name = data["server_name"]
            if not isinstance(server_name, str) or not server_name.strip():
                return (
                    jsonify({"error": "server_name must be a non-empty string."}),
                    400,
                )
            update_fields.append(
                "server_name = %s"
                if DATABASE_TYPE == "postgresql"
                else "server_name = ?"
            )
            params.append(server_name)

        if "resource_type" in data:
            resource_type = data["resource_type"]
            if resource_type not in ["cpu", "ram", "disk"]:
                return (
                    jsonify(
                        {
                            "error": "resource_type must be one of 'cpu', 'ram', or 'disk'."
                        }
                    ),
                    400,
                )
            update_fields.append(
                "resource_type = %s"
                if DATABASE_TYPE == "postgresql"
                else "resource_type = ?"
            )
            params.append(resource_type)

        if "threshold_percentage" in data:
            try:
                threshold_percentage = float(data["threshold_percentage"])
                if not (0 <= threshold_percentage <= 100):
                    raise ValueError("Threshold percentage must be between 0 and 100.")
                update_fields.append(
                    "threshold_percentage = %s"
                    if DATABASE_TYPE == "postgresql"
                    else "threshold_percentage = ?"
                )
                params.append(threshold_percentage)
            except ValueError as e:
                return jsonify({"error": f"Invalid threshold_percentage: {e}"}), 400

        if "time_window_minutes" in data:
            try:
                time_window_minutes = int(data["time_window_minutes"])
                if time_window_minutes <= 0:
                    raise ValueError("Time window minutes must be a positive integer.")
                update_fields.append(
                    "time_window_minutes = %s"
                    if DATABASE_TYPE == "postgresql"
                    else "time_window_minutes = ?"
                )
                params.append(time_window_minutes)
            except ValueError as e:
                return jsonify({"error": f"Invalid time_window_minutes: {e}"}), 400

        if "emails" in data:
            emails = data["emails"]
            if not isinstance(emails, str) or not emails.strip():
                return (
                    jsonify(
                        {
                            "error": "emails must be a non-empty string (comma-separated)."
                        }
                    ),
                    400,
                )
            email_list = [email.strip() for email in emails.split(",")]
            email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            for email_val in email_list:  # Renamed to avoid conflict
                if not re.match(email_regex, email_val):
                    return (
                        jsonify({"error": f"Invalid email format: '{email_val}'"}),
                        400,
                    )
            valid_emails_string = ",".join(email_list)
            update_fields.append(
                "emails = %s" if DATABASE_TYPE == "postgresql" else "emails = ?"
            )
            params.append(valid_emails_string)

        if "is_enabled" in data:
            is_enabled = data["is_enabled"]
            if not isinstance(is_enabled, bool):
                return jsonify({"error": "is_enabled must be a boolean."}), 400
            update_fields.append(
                "is_enabled = %s" if DATABASE_TYPE == "postgresql" else "is_enabled = ?"
            )
            params.append(is_enabled)

        if not update_fields:
            return jsonify({"error": "No fields provided to update."}), 400

        # Construct and execute the update query
        sql_set_clause = ", ".join(update_fields)
        update_query = f"UPDATE alerts SET {sql_set_clause} WHERE id = "
        update_query += "%s" if DATABASE_TYPE == "postgresql" else "?"
        params.append(alert_id)

        # Need a fresh cursor for execute if row_factory was changed and reset for SQLite
        if DATABASE_TYPE == "sqlite" and original_row_factory is not None:
            cursor.close()  # Close old cursor
            cursor = conn.cursor()  # Get fresh default cursor

        cursor.execute(update_query, tuple(params))
        conn.commit()

        # Fetch the updated alert to return
        if (
            DATABASE_TYPE == "sqlite"
        ):  # Set row_factory for this specific cursor if needed
            original_row_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()  # Recreate cursor if row_factory changed

        cursor.execute(select_query, (alert_id,))  # select_query defined earlier
        updated_alert_row = cursor.fetchone()

        if (
            DATABASE_TYPE == "sqlite" and original_row_factory is not None
        ):  # Reset row_factory if changed
            conn.row_factory = original_row_factory

        if updated_alert_row:
            updated_alert = dict(updated_alert_row)
            if updated_alert.get("last_triggered_at") and isinstance(
                updated_alert["last_triggered_at"], (datetime.datetime, datetime.date)
            ):
                updated_alert["last_triggered_at"] = updated_alert[
                    "last_triggered_at"
                ].isoformat()
            if updated_alert.get("created_at") and isinstance(
                updated_alert["created_at"], (datetime.datetime, datetime.date)
            ):
                updated_alert["created_at"] = updated_alert["created_at"].isoformat()
            logger.info(
                f"Alert with ID {alert_id} updated successfully. Fields changed: {', '.join(data.keys())}"
            )
            return (
                jsonify(
                    {"message": "Alert updated successfully", "alert": updated_alert}
                ),
                200,
            )
        else:  # Should not happen if update was successful and ID is correct, but as a safeguard
            logger.error(f"Failed to retrieve alert with ID {alert_id} after update.")
            return (
                jsonify(
                    {
                        "error": "Alert updated but failed to retrieve the updated record."
                    }
                ),
                500,
            )

    except psycopg2.Error as e_pg:
        logger.error(
            f"PostgreSQL error updating alert {alert_id}: {e_pg}", exc_info=True
        )
        return jsonify({"error": f"Database error: {e_pg.pgerror or str(e_pg)}"}), 500
    except sqlite3.Error as e_sql:
        logger.error(f"SQLite error updating alert {alert_id}: {e_sql}", exc_info=True)
        return jsonify({"error": f"Database error: {str(e_sql)}"}), 500
    except Exception as e:
        logger.error(f"Error updating alert {alert_id}: {e}", exc_info=True)
        return (
            jsonify(
                {"error": "An unexpected error occurred while updating the alert."}
            ),
            500,
        )
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn, cursor = get_db_connection()

        # Check if alert exists before deleting
        select_query = "SELECT id FROM alerts WHERE id = "
        select_query += "%s" if DATABASE_TYPE == "postgresql" else "?"
        cursor.execute(select_query, (alert_id,))
        existing_alert = cursor.fetchone()

        if not existing_alert:
            logger.warning(f"Attempt to delete non-existent alert with ID {alert_id}.")
            return jsonify({"error": "Alert not found"}), 404

        delete_query = "DELETE FROM alerts WHERE id = "
        delete_query += "%s" if DATABASE_TYPE == "postgresql" else "?"

        cursor.execute(delete_query, (alert_id,))
        conn.commit()

        if cursor.rowcount > 0:
            logger.info(f"Alert with ID {alert_id} deleted successfully.")
            return jsonify({"message": "Alert deleted successfully"}), 200
        else:
            # This case should ideally be caught by the 'existing_alert' check,
            # but it's a safeguard if the delete somehow affects 0 rows despite finding it.
            logger.warning(
                f"Alert with ID {alert_id} was found but not deleted (rowcount 0)."
            )
            return jsonify({"error": "Alert not found or already deleted"}), 404

    except psycopg2.Error as e_pg:
        logger.error(
            f"PostgreSQL error deleting alert {alert_id}: {e_pg}", exc_info=True
        )
        return jsonify({"error": f"Database error: {e_pg.pgerror or str(e_pg)}"}), 500
    except sqlite3.Error as e_sql:
        logger.error(f"SQLite error deleting alert {alert_id}: {e_sql}", exc_info=True)
        return jsonify({"error": f"Database error: {str(e_sql)}"}), 500
    except Exception as e:
        logger.error(f"Error deleting alert {alert_id}: {e}", exc_info=True)
        return (
            jsonify(
                {"error": "An unexpected error occurred while deleting the alert."}
            ),
            500,
        )
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


def set_alert_enabled_status(alert_id, is_enabled_status):
    """Helper function to enable/disable an alert."""
    if not session.get(
        "logged_in"
    ):  # Repeated here for direct call safety, though routes should check
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn, cursor = get_db_connection()

        # Check if alert exists
        select_query = "SELECT id FROM alerts WHERE id = "
        select_query += "%s" if DATABASE_TYPE == "postgresql" else "?"
        cursor.execute(select_query, (alert_id,))
        existing_alert = cursor.fetchone()

        if not existing_alert:
            logger.warning(
                f"Attempt to {'enable' if is_enabled_status else 'disable'} non-existent alert with ID {alert_id}."
            )
            return jsonify({"error": "Alert not found"}), 404

        update_query = "UPDATE alerts SET is_enabled = "
        update_query += "%s" if DATABASE_TYPE == "postgresql" else "?"
        update_query += " WHERE id = "
        update_query += "%s" if DATABASE_TYPE == "postgresql" else "?"

        params = (is_enabled_status, alert_id)
        cursor.execute(update_query, params)
        conn.commit()

        action = "enabled" if is_enabled_status else "disabled"
        if cursor.rowcount > 0:
            logger.info(f"Alert with ID {alert_id} {action} successfully.")
            return jsonify({"message": f"Alert {action} successfully"}), 200
        else:
            # Should be caught by existence check, but as safeguard
            logger.warning(
                f"Alert with ID {alert_id} found but status not changed (rowcount 0)."
            )
            return (
                jsonify(
                    {"error": f"Alert not found or status already set to {action}"}
                ),
                404,
            )

    except psycopg2.Error as e_pg:
        logger.error(
            f"PostgreSQL error setting alert {alert_id} status to {is_enabled_status}: {e_pg}",
            exc_info=True,
        )
        return jsonify({"error": f"Database error: {e_pg.pgerror or str(e_pg)}"}), 500
    except sqlite3.Error as e_sql:
        logger.error(
            f"SQLite error setting alert {alert_id} status to {is_enabled_status}: {e_sql}",
            exc_info=True,
        )
        return jsonify({"error": f"Database error: {str(e_sql)}"}), 500
    except Exception as e:
        logger.error(
            f"Error setting alert {alert_id} status to {is_enabled_status}: {e}",
            exc_info=True,
        )
        return jsonify({"error": f"An unexpected error occurred."}), 500
    finally:
        if "conn" in locals() and conn:
            if "cursor" in locals() and cursor:
                cursor.close()
            conn.close()


@app.route("/api/alerts/<int:alert_id>/enable", methods=["POST"])
def enable_alert(alert_id):
    return set_alert_enabled_status(alert_id, True)


@app.route("/api/alerts/<int:alert_id>/disable", methods=["POST"])
def disable_alert(alert_id):
    return set_alert_enabled_status(alert_id, False)


if __name__ == "__main__":
    logger.info("Application starting...")
    init_db()  # init_db now has its own logging
    # Initialize configured_server_names once based on initial parsing, before thread starts
    # This is because parse_remote_server_configs is called before the thread loop
    # The historical_data_collector will then update counts and times.
    try:
        # Initialize collector_status_info with server names before starting the thread.
        # This mirrors the logic at the start of historical_data_collector for consistency.
        initial_server_configs_map = parse_remote_server_configs()
        temp_initial_server_names = []
        initial_has_explicit_local = False
        for idx, cfg in initial_server_configs_map.items():
            name = cfg.get("name", cfg.get("host", f"ServerIndex_{idx}"))
            temp_initial_server_names.append(name)
            if cfg.get("is_local", False) or name == "local":
                initial_has_explicit_local = True

        if not initial_has_explicit_local and "local" not in temp_initial_server_names:
            temp_initial_server_names.append("local")

        unique_initial_names = sorted(list(set(temp_initial_server_names)))

        with collector_status_lock:
            collector_status_info["configured_server_names"] = unique_initial_names
            collector_status_info["servers_configured_count"] = len(
                unique_initial_names
            )
            logger.info(
                f"Initial collector status populated at startup. Monitored servers: {collector_status_info['configured_server_names']}"
            )

    except Exception as e:
        logger.error(
            f"Failed to perform initial parse of server_configs for collector_status at startup: {e}",
            exc_info=True,
        )
        with collector_status_lock:  # Default to local if parsing fails
            collector_status_info["servers_configured_count"] = 1
            collector_status_info["configured_server_names"] = ["local"]

    # Only start the collector thread if not in debug mode OR if this is the main Werkzeug process
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        collector_thread = threading.Thread(
            target=historical_data_collector,
            name="HistoricalDataCollectorThread",
            daemon=True,
        )
        collector_thread.start()
        logger.info(
            "Historical data collector thread started in the appropriate process."
        )
    elif app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        logger.info(
            "Flask Debug mode is on and this is the reloader process. Collector thread will not start here."
        )

    # Note: Alert evaluation is called within the historical_data_collector loop.
    # No separate alert evaluation thread is started here to keep it sequential after data collection.

    logger.info(
        "Starting Flask development server. Note: Flask's internal logs may also be shown if DEBUG is true."
    )
    app.run(debug=False, host="0.0.0.0", port=5000)
