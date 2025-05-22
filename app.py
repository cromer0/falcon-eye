from flask import Flask, render_template, jsonify, request
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
import psycopg2.extras # For dictionary cursor

# --- Configuration ---
load_dotenv()
DATABASE_PATH = os.path.join('data', 'sys_stats.db') # Relevant for SQLite
DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'sqlite').lower()

# PostgreSQL specific (if DATABASE_TYPE is 'postgresql')
POSTGRES_HOST = os.getenv('POSTGRES_HOST')
POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
POSTGRES_USER = os.getenv('POSTGRES_USER')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD')
POSTGRES_DBNAME = os.getenv('POSTGRES_DBNAME')

HISTORICAL_DATA_COLLECTION_INTERVAL = 60  # Default value
DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR = 'DATA_GATHERING_INTERVAL_SECONDS'
MAX_HISTORICAL_ENTRIES = 1440
SSH_TIMEOUT = 25

# Determine the actual collection interval
current_collection_interval = HISTORICAL_DATA_COLLECTION_INTERVAL
data_gathering_interval_str = os.getenv(DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR)
if data_gathering_interval_str:
    try:
        interval_seconds = int(data_gathering_interval_str)
        if interval_seconds > 0:
            current_collection_interval = interval_seconds
            print(f"Using custom data gathering interval: {current_collection_interval} seconds.")
        else:
            print(f"Warning: {DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR} ('{data_gathering_interval_str}') must be a positive integer. Using default: {current_collection_interval}s.")
    except ValueError:
        print(f"Warning: Invalid value for {DATA_GATHERING_INTERVAL_SECONDS_ENV_VAR} ('{data_gathering_interval_str}'). Expected an integer. Using default: {current_collection_interval}s.")

DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR = 'DETAIL_VIEW_REFRESH_INTERVAL_MS'
DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS = 3000
SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR = 'SERVER_LIST_REFRESH_INTERVAL_MS'
DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS = 15000

app = Flask(__name__)

# --- Database Connection Helper ---
def get_db_connection():
    """
    Establishes a database connection based on DATABASE_TYPE.
    Returns a connection object and a cursor.
    The caller is responsible for closing the connection.
    """
    if DATABASE_TYPE == 'sqlite':
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        conn = sqlite3.connect(DATABASE_PATH)
        # For SQLite, the default cursor is fine.
        # For consistency in return type, we can return conn.cursor()
        # but often it's created just before use.
        return conn, conn.cursor()
    elif DATABASE_TYPE == 'postgresql':
        if not all([POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DBNAME]):
            raise ValueError("Missing PostgreSQL connection details in environment variables.")
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DBNAME
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
        if DATABASE_TYPE == 'sqlite':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    server_name TEXT NOT NULL DEFAULT 'local',
                    cpu_percent REAL,
                    ram_percent REAL,
                    disk_percent REAL
                )
            ''')
            # Index for faster queries, especially for historical data retrieval and pruning
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_server_timestamp ON stats (server_name, timestamp);")
        elif DATABASE_TYPE == 'postgresql':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    server_name TEXT NOT NULL,
                    cpu_percent REAL,
                    ram_percent REAL,
                    disk_percent REAL
                )
            ''')
            # Index for faster queries on server_name and timestamp
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_server_timestamp ON stats (server_name, timestamp);")
        
        conn.commit()
    except Exception as e:
        print(f"Error during database initialization: {e}")
        # Depending on the error, you might want to raise it or handle it differently
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

def store_stats(server_name, cpu, ram, disk):
    """Stores system stats into the configured database, including server_name."""
    try:
        conn, cursor = get_db_connection()

        if DATABASE_TYPE == 'sqlite':
            cursor.execute("INSERT INTO stats (server_name, cpu_percent, ram_percent, disk_percent) VALUES (?, ?, ?, ?)",
                           (server_name, cpu, ram, disk))
            # Prune old entries for the specific server
            cursor.execute(f'''
                DELETE FROM stats
                WHERE server_name = ? AND timestamp NOT IN (
                    SELECT timestamp
                    FROM stats
                    WHERE server_name = ?
                    ORDER BY timestamp DESC
                    LIMIT {MAX_HISTORICAL_ENTRIES}
                )
            ''', (server_name, server_name))
        elif DATABASE_TYPE == 'postgresql':
            cursor.execute("INSERT INTO stats (server_name, cpu_percent, ram_percent, disk_percent) VALUES (%s, %s, %s, %s)",
                           (server_name, cpu, ram, disk))
            # Prune old entries for the specific server
            # Using a simpler approach similar to SQLite for now, adjust if performance issues arise
            # Note: For very large tables, the subquery with ROW_NUMBER() might be more performant.
            # However, the LIMIT clause in a subquery for NOT IN might not be directly supported or efficient
            # in all PostgreSQL versions in the exact same way as SQLite.
            # A common and effective way for PostgreSQL:
            cursor.execute(f'''
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
            ''', (server_name, MAX_HISTORICAL_ENTRIES))
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
    except Exception as e:
        print(f"Error in store_stats for server '{server_name}': {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

def get_current_stats():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    ram_stats = psutil.virtual_memory()
    ram_percent = ram_stats.percent
    try:
        disk_stats_obj = psutil.disk_usage('/') # disk_stats_obj for clarity
        disk_percent = disk_stats_obj.percent
        disk_total_gb = round(disk_stats_obj.total / (1024**3), 2)
        disk_used_gb = round(disk_stats_obj.used / (1024**3), 2)
    except FileNotFoundError:
        disk_percent = 0.0
        disk_total_gb = 0.0 # Ensure these are defined
        disk_used_gb = 0.0
        print("Warning: Could not get disk usage for '/'. Defaulting to 0.")

    return {
        'cpu_percent': cpu_percent,
        'ram_percent': ram_percent,
        'ram_total_gb': round(ram_stats.total / (1024**3), 2),
        'ram_used_gb': round(ram_stats.used / (1024**3), 2),
        'disk_percent': disk_percent,
        'disk_total_gb': disk_total_gb,
        'disk_used_gb': disk_used_gb,
        'timestamp': datetime.datetime.now().isoformat()
    }

def historical_data_collector():
    print("Starting historical data collection thread...")
    # Fetch server configurations once, assuming they don't change at runtime.
    # If they can change, this should be moved inside the loop.
    try:
        server_configs_map = parse_remote_server_configs()
        print(f"Historical data collector initialized with {len(server_configs_map)} remote server(s).")
    except Exception as e:
        print(f"CRITICAL: Could not parse server configurations for historical data collector: {e}")
        print("Historical data collector will only collect local stats.")
        server_configs_map = {} # Ensure it's a dict

    while True:
        try:
            # 1. Collect and store local server stats
            try:
                local_stats = get_current_stats()
                # Using 'local' as the server_name for the machine running this app.
                # This aligns with the previous setup and PostgreSQL's NOT NULL constraint.
                store_stats('local', local_stats['cpu_percent'], local_stats['ram_percent'], local_stats['disk_percent'])
                # print("Successfully stored local server stats.")
            except Exception as e_local:
                print(f"Error collecting or storing local server stats: {e_local}")

            # 2. Collect and store remote server stats
            # The server_configs_map is a dictionary where keys are original indices (e.g., "1", "2")
            # and values are the server configuration dictionaries.
            for server_index, remote_server_config in server_configs_map.items():
                server_display_name = remote_server_config.get('name', remote_server_config.get('host', f"ServerIndex_{server_index}"))
                try:
                    # Skip collection for servers marked as 'is_local: true' in config,
                    # as their stats are (or should be) collected by the local collector above.
                    # This check helps avoid redundant SSH to localhost if it's also in remote configs.
                    if remote_server_config.get('is_local', False):
                        # print(f"Skipping historical data collection for '{server_display_name}' as it's marked local.")
                        continue
                    
                    print(f"[Collector] Attempting to fetch stats for remote server: {remote_server_config.get('name', remote_server_config.get('host'))}")
                    # Pass the full map for jump server resolution if needed.
                    remote_stats = get_remote_server_stats(remote_server_config, server_configs_map)
                    print(f"[Collector] Raw stats for {remote_server_config.get('name')}: {remote_stats}")

                    if remote_stats and remote_stats.get('status') == 'online':
                        print(f"[Collector] Storing stats for {remote_stats['name']}: CPU {remote_stats['cpu_percent']}%, RAM {remote_stats['ram_percent']}%, Disk {remote_stats['disk_percent']}%")
                        store_stats(
                            remote_stats['name'], # Use the name from the stats result (which is derived from config)
                            remote_stats['cpu_percent'],
                            remote_stats['ram_percent'],
                            remote_stats['disk_percent']
                        )
                        # print(f"Successfully stored historical stats for {remote_stats['name']}.") # Original success print, can be kept or removed
                    else:
                        # Use server_display_name for consistency if remote_stats['name'] might be missing on error
                        error_msg = remote_stats.get('error_message', 'Unknown error')
                        status_msg = remote_stats.get('status', 'unknown')
                        print(f"[Collector] Not storing stats for {server_display_name} due to status: {status_msg}. Error: {error_msg}")
                except Exception as e_remote:
                    print(f"Unhandled exception while processing remote server {server_display_name}: {e_remote}")
        
        except Exception as e_main_loop:
            # This is a catch-all for unexpected errors in the main loop itself (e.g., issues with server_configs_map iteration)
            print(f"Critical error in historical_data_collector main loop: {e_main_loop}")
            # Potentially add a short sleep here to prevent rapid-fire logging if the error is persistent.
            time.sleep(10) 

        # Sleep at the end of processing all servers for this interval
        time.sleep(current_collection_interval)


# --- Remote Server Stat Collection ---
def get_ssh_connection_args(server_conf_entry):
    """Helper to build common SSH connection arguments from a config entry."""
    args = {
        'hostname': server_conf_entry['host'],
        'port': int(server_conf_entry.get('port', 22)),
        'username': server_conf_entry['user'],
        'timeout': SSH_TIMEOUT
    }
    if server_conf_entry.get('key_path'):
        expanded_key_path = os.path.expanduser(server_conf_entry['key_path'])
        args['key_filename'] = expanded_key_path
        if server_conf_entry.get('key_passphrase'):
            args['passphrase'] = server_conf_entry['key_passphrase']
    elif server_conf_entry.get('password'):
        args['password'] = server_conf_entry['password']
    else:
        return None # Auth method missing
    return args

def get_remote_server_stats(target_server_config, all_server_configs_map):
    """
    Connects to a remote Ubuntu server, potentially via a jump server,
    and fetches system stats using standard Linux commands.
    `all_server_configs_map` is a dictionary mapping server index (string) to its config.
    """
    target_client = paramiko.SSHClient()
    target_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    jump_client = None # Initialize jump client

    name = target_server_config.get('name', target_server_config['host'])
    disk_to_monitor = target_server_config.get('disk_path', '/')

    base_result = {
        'name': name, 'host': target_server_config['host'], 'status': 'offline',
        'is_local': target_server_config.get('is_local', False),
        'cpu_percent': 0.0, 'cpu_cores': 0, 'cpu_model': "N/A",
        'ram_percent': 0.0, 'ram_total_gb': 0.0, 'ram_used_gb': 0.0,
        'disk_percent': 0.0, 'disk_total_gb': 0.0, 'disk_used_gb': 0.0,
        'error_message': None
    }

    try:
        target_ssh_args = get_ssh_connection_args(target_server_config)
        if not target_ssh_args:
            base_result['error_message'] = "Target server auth (password/key) missing."
            print(f"Auth error for target {name}: Auth method missing.")
            return base_result

        sock = None # Socket for connection (direct or via jump)

        if target_server_config.get('jump_server_index'):
            jump_server_index_str = str(target_server_config['jump_server_index'])
            jump_server_conf = all_server_configs_map.get(jump_server_index_str)

            if not jump_server_conf:
                base_result['error_message'] = f"Jump server config for index '{jump_server_index_str}' not found."
                print(f"Error for {name}: Jump server index '{jump_server_index_str}' invalid or missing from map.")
                return base_result

            print(f"Connecting to {name} via jump server: {jump_server_conf.get('name', jump_server_conf['host'])}")
            jump_client = paramiko.SSHClient()
            jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            jump_ssh_args = get_ssh_connection_args(jump_server_conf)
            if not jump_ssh_args:
                base_result['error_message'] = f"Jump server '{jump_server_conf.get('name')}' auth (password/key) missing."
                print(f"Auth error for jump server {jump_server_conf.get('name')}: Auth method missing.")
                if jump_client: jump_client.close()
                return base_result

            jump_client.connect(**jump_ssh_args)

            # Create a transport channel through the jump server to the target
            transport = jump_client.get_transport()
            dest_addr = (target_server_config['host'], int(target_server_config.get('port', 22)))
            src_addr = ('127.0.0.1', 0) # Let the system pick a source port on the jump server
            try:
                sock = transport.open_channel("direct-tcpip", dest_addr, src_addr, timeout=SSH_TIMEOUT) # Pass timeout here too
            except paramiko.SSHException as e:
                base_result['error_message'] = f"Failed to open channel via jump server: {e}"
                print(f"Channel error for {name} via {jump_server_conf.get('name')}: {e}")
                if jump_client: jump_client.close()
                return base_result

            target_ssh_args['sock'] = sock # Use this channel for the target connection
        else:
            print(f"Connecting directly to {name}")
            # For direct connection, sock remains None, paramiko handles it.

        target_client.connect(**target_ssh_args)

        # --- Shell commands (same as before) ---
        delimiter = "###STATS_DELIMITER###"
        cpu_usage_cmd = "LC_ALL=C vmstat 1 2 | awk 'END{print 100-$15}'"
        ram_cmd = "awk '/^MemTotal:/{total=$2} /^MemAvailable:/{available=$2} END{used=total-available; if (total > 0) printf \"%.2f###%.2f###%.2f\", (used*100)/total, used/1024/1024, total/1024/1024; else print \"ERROR_RAM_CALC\";}' /proc/meminfo"
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
        stdin, stdout, stderr = target_client.exec_command(full_command, timeout=SSH_TIMEOUT)
        # --- Parsing logic (same as before) ---
        raw_output = stdout.read().decode(errors='ignore').strip()
        ssh_stderr_output = stderr.read().decode(errors='ignore').strip()
        ssh_exit_status = stdout.channel.recv_exit_status()

        # print(f"DEBUG ({name}): SSH Exit Status: {ssh_exit_status}")
        # print(f"DEBUG ({name}): Raw Output from SSH: '{raw_output}'")
        # print(f"DEBUG ({name}): Stderr from SSH command execution: '{ssh_stderr_output}'")

        current_error_messages = []
        if ssh_exit_status == 0 and raw_output:
            parts = raw_output.split(delimiter)
            if len(parts) == 5:
                cpu_usage_str, ram_str, disk_str, cpu_cores_str, cpu_model_str = parts
                # Parse CPU Usage
                if "ERROR_CPU_USAGE" in cpu_usage_str or not cpu_usage_str: current_error_messages.append("CPU usage data retrieval failed.")
                else:
                    try: base_result['cpu_percent'] = float(cpu_usage_str)
                    except ValueError: current_error_messages.append(f"Invalid CPU usage value: '{cpu_usage_str}'.")
                # Parse RAM
                if "ERROR_RAM" in ram_str or not ram_str: current_error_messages.append("RAM data retrieval failed.")
                else:
                    ram_parts = ram_str.split('###')
                    if len(ram_parts) == 3:
                        try:
                            base_result['ram_percent'] = float(ram_parts[0]); base_result['ram_used_gb'] = float(ram_parts[1]); base_result['ram_total_gb'] = float(ram_parts[2])
                        except ValueError: current_error_messages.append(f"Invalid RAM values: '{ram_str}'.")
                    else: current_error_messages.append(f"Unexpected RAM format: '{ram_str}'.")
                # Parse DISK
                if "ERROR_DISK" in disk_str or not disk_str: current_error_messages.append("Disk data retrieval failed.")
                else:
                    disk_parts = disk_str.split('###')
                    if len(disk_parts) == 3:
                        try:
                            base_result['disk_percent'] = float(disk_parts[0]); base_result['disk_used_gb'] = round(float(disk_parts[1]) / (1024 * 1024), 2); base_result['disk_total_gb'] = round(float(disk_parts[2]) / (1024 * 1024), 2)
                        except ValueError: current_error_messages.append(f"Invalid Disk values: '{disk_str}'.")
                    else: current_error_messages.append(f"Unexpected Disk format: '{disk_str}'.")
                # Parse CPU Cores
                if "ERROR_CPU_CORES" in cpu_cores_str or not cpu_cores_str: current_error_messages.append("CPU cores data retrieval failed."); base_result['cpu_cores'] = 0
                else:
                    try: base_result['cpu_cores'] = int(cpu_cores_str)
                    except ValueError: current_error_messages.append(f"Invalid CPU cores value: '{cpu_cores_str}'."); base_result['cpu_cores'] = 0
                # Parse CPU Model
                if "ERROR_CPU_MODEL" in cpu_model_str or not cpu_model_str.strip() or cpu_model_str.strip().upper() == "N/A": current_error_messages.append("CPU model data retrieval failed or N/A."); base_result['cpu_model'] = "N/A"
                else: base_result['cpu_model'] = cpu_model_str.strip()

                if not current_error_messages: base_result['status'] = 'online'
                else: base_result['status'] = 'error'; base_result['error_message'] = " | ".join(current_error_messages)
            else: base_result['status'] = 'error'; base_result['error_message'] = f"Output format error. Expected 5 parts, got {len(parts)}. Output: '{raw_output[:150]}...'"
        elif ssh_exit_status != 0:
            base_result['status'] = 'error'; err_msg = f"Remote script failed (exit: {ssh_exit_status})."
            if ssh_stderr_output: err_msg += f" Stderr: {ssh_stderr_output}"
            elif raw_output: err_msg += f" Stdout: {raw_output[:100]}..."
            base_result['error_message'] = err_msg
        elif not raw_output:
            base_result['status'] = 'error'; base_result['error_message'] = f"No output from remote command (exit: {ssh_exit_status})."

    except paramiko.AuthenticationException as e: # Specific to the current connection attempt
        base_result['status'] = 'error'; base_result['error_message'] = f"Authentication failed: {str(e)}"
    except paramiko.SSHException as e: # Includes timeouts, channel errors
        base_result['status'] = 'error'; base_result['error_message'] = f"SSH error: {str(e)}"
    except Exception as e:
        base_result['status'] = 'error'; base_result['error_message'] = f"General error: {str(e)}"
        # import traceback
        # print(f"DEBUG ({name}): General error traceback: {traceback.format_exc()}")
    finally:
        target_client.close()
        if jump_client:
            jump_client.close()

    # --- Final sanitization ---
    for key in ['cpu_percent', 'ram_percent', 'ram_total_gb', 'ram_used_gb', 'disk_percent', 'disk_total_gb', 'disk_used_gb']:
        try: base_result[key] = float(base_result.get(key, 0.0))
        except (ValueError, TypeError): base_result[key] = 0.0
    try: base_result['cpu_cores'] = int(base_result.get('cpu_cores', 0))
    except (ValueError, TypeError): base_result['cpu_cores'] = 0
    if not isinstance(base_result.get('cpu_model'), str) or not base_result.get('cpu_model', "").strip(): base_result['cpu_model'] = "N/A"

    if base_result['status'] == 'error' and not base_result['error_message']:
        base_result['error_message'] = "Unknown error during data retrieval."
    if base_result['error_message'] and base_result['status'] == 'error':
        print(f"FINAL Error for {name} ({target_server_config['host']}): {base_result['error_message']}")
    # print(f"FINAL base_result for {name}: {base_result}")

    return base_result


def parse_remote_server_configs():
    servers_map = {}
    i = 1
    while True:
        host_var = f'REMOTE_SERVER_{i}_HOST'
        if os.getenv(host_var):
            server_conf = {
                'original_index': i,
                'name': os.getenv(f'REMOTE_SERVER_{i}_NAME', os.getenv(host_var)),
                'host': os.getenv(host_var),
                'port': os.getenv(f'REMOTE_SERVER_{i}_PORT', '22'),
                'user': os.getenv(f'REMOTE_SERVER_{i}_USER'),
                'password': os.getenv(f'REMOTE_SERVER_{i}_PASSWORD'),
                'key_path': os.getenv(f'REMOTE_SERVER_{i}_KEY_PATH'),
                'key_passphrase': os.getenv(f'REMOTE_SERVER_{i}_KEY_PASSPHRASE'),
                'disk_path': os.getenv(f'REMOTE_SERVER_{i}_DISK_PATH', '/'),
                'jump_server_index': os.getenv(f'REMOTE_SERVER_{i}_JUMP_SERVER'),
                'is_local': os.getenv(f'REMOTE_SERVER_{i}_IS_LOCAL', 'false').lower() == 'true' # New flag
            }
            if not server_conf['user']:
                print(f"Warning: REMOTE_SERVER_{i}_USER not set. Skipping server {server_conf['name']}.")
            elif not server_conf['password'] and not server_conf['key_path'] and server_conf['host'] not in ['localhost', '127.0.0.1']: # Allow no auth for true local if desired and handled
                # For true local (localhost), psutil could be used directly if we adapt get_remote_server_stats
                # For now, it will try SSH even for local unless 'is_local' is used to bypass SSH
                print(f"Warning: Neither PASSWORD nor KEY_PATH set for REMOTE_SERVER_{i}. Skipping server {server_conf['name']}.")
            else:
                servers_map[str(i)] = server_conf
            i += 1
        else:
            break
    return servers_map

# --- Flask Routes ---
@app.route('/')
def index():
    detail_refresh_interval_str = os.getenv(DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR)
    detail_refresh_interval = DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS
    if detail_refresh_interval_str:
        try:
            interval = int(detail_refresh_interval_str)
            if interval > 0:
                detail_refresh_interval = interval
            else:
                print(f"Warning: {DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR} is not a positive integer ('{detail_refresh_interval_str}'). Using default: {DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS}ms.")
        except ValueError:
            print(f"Warning: Invalid value for {DETAIL_VIEW_REFRESH_INTERVAL_MS_ENV_VAR} ('{detail_refresh_interval_str}'). Expected an integer. Using default: {DEFAULT_DETAIL_VIEW_REFRESH_INTERVAL_MS}ms.")

    server_list_refresh_interval_str = os.getenv(SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR)
    server_list_refresh_interval = DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS
    if server_list_refresh_interval_str:
        try:
            interval = int(server_list_refresh_interval_str)
            if interval > 0:
                server_list_refresh_interval = interval
            else:
                print(f"Warning: {SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR} is not a positive integer ('{server_list_refresh_interval_str}'). Using default: {DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS}ms.")
        except ValueError:
            print(f"Warning: Invalid value for {SERVER_LIST_REFRESH_INTERVAL_MS_ENV_VAR} ('{server_list_refresh_interval_str}'). Expected an integer. Using default: {DEFAULT_SERVER_LIST_REFRESH_INTERVAL_MS}ms.")

    return render_template('index.html', detail_refresh_interval=detail_refresh_interval, server_list_refresh_interval=server_list_refresh_interval)

@app.route('/api/current_stats')
def api_current_stats(): return jsonify(get_current_stats())

@app.route('/api/historical_stats')
def api_historical_stats():
    requested_server_name = request.args.get('server_name')
    
    # Default to 'local' if no server_name is provided or if it's an empty string
    target_server_name = requested_server_name if requested_server_name else 'local'

    try:
        conn, cursor = get_db_connection()
        
        query_sql = ""
        query_params = (target_server_name,)

        if DATABASE_TYPE == 'sqlite':
            conn.row_factory = sqlite3.Row 
            cursor = conn.cursor() 
            query_sql = "SELECT timestamp, cpu_percent, ram_percent, disk_percent FROM stats WHERE server_name = ? ORDER BY timestamp ASC"
        elif DATABASE_TYPE == 'postgresql':
            # DictCursor is already set by get_db_connection for postgresql
            query_sql = "SELECT timestamp, cpu_percent, ram_percent, disk_percent FROM stats WHERE server_name = %s ORDER BY timestamp ASC"
        else:
            return jsonify({"error": "Database type not configured correctly for historical stats"}), 500

        cursor.execute(query_sql, query_params)
        rows = cursor.fetchall()
        
        # Prepare data structure for JSON response
        data = {
            'server_name': target_server_name, # Include the server name in the response
            'labels': [],
            'cpu_data': [],
            'ram_data': [],
            'disk_data': []
        }

        if DATABASE_TYPE == 'sqlite':
            for row in rows:
                data['labels'].append(row['timestamp']) # Already string or suitable format
                data['cpu_data'].append(row['cpu_percent'])
                data['ram_data'].append(row['ram_percent'])
                data['disk_data'].append(row['disk_percent'])
        elif DATABASE_TYPE == 'postgresql':
            for row in rows:
                data['labels'].append(row['timestamp'].isoformat()) # Ensure datetime is JSON serializable
                data['cpu_data'].append(row['cpu_percent'])
                data['ram_data'].append(row['ram_percent'])
                data['disk_data'].append(row['disk_percent'])
        
        return jsonify(data)
        
    except Exception as e:
        print(f"Error in api_historical_stats for server '{target_server_name}': {e}")
        # import traceback
        # traceback.print_exc() # For more detailed debugging if needed
        return jsonify({"error": str(e), "server_name": target_server_name}), 500
    finally:
        if 'conn' in locals() and conn:
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()


@app.route('/api/remote_servers_stats')
def api_remote_servers_stats():
    requested_host = request.args.get('host')
    server_configs_map = parse_remote_server_configs()
    all_stats = []
    configs_to_process = []

    if requested_host:
        found_config = None
        for config in server_configs_map.values():
            if config['host'] == requested_host:
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
    max_workers = min(len(configs_to_process), os.cpu_count() or 1) # Ensure at least 1 worker

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
                server_name = target_config.get('name', 'Unknown Server')
                print(f'{server_name} (Host: {target_config.get("host")}) generated an exception during future.result(): {exc}')
                all_stats.append({
                    'name': server_name, 'host': target_config.get('host'), 'status': 'error',
                    'error_message': f'Task execution failed: {str(exc)}',
                    'cpu_percent': 0, 'cpu_cores': 0, 'cpu_model': 'N/A',
                    'ram_percent': 0, 'ram_total_gb': 0, 'ram_used_gb': 0,
                    'disk_percent': 0, 'disk_total_gb': 0, 'disk_used_gb': 0,
                })
    return jsonify(all_stats)


if __name__ == '__main__':
    init_db()
    collector_thread = threading.Thread(target=historical_data_collector, daemon=True)
    collector_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000)
