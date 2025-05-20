from flask import Flask, render_template, jsonify
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

# --- Configuration ---
load_dotenv()
DATABASE_PATH = os.path.join('data', 'sys_stats.db')
HISTORICAL_DATA_COLLECTION_INTERVAL = 60
MAX_HISTORICAL_ENTRIES = 1440
SSH_TIMEOUT = 25

app = Flask(__name__)

# --- Database Setup & Local Stats ---
def init_db():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cpu_percent REAL,
            ram_percent REAL,
            disk_percent REAL
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON stats (timestamp);")
    conn.commit()
    conn.close()

def store_stats(cpu, ram, disk):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO stats (cpu_percent, ram_percent, disk_percent) VALUES (?, ?, ?)",
                   (cpu, ram, disk))
    cursor.execute(f'''
        DELETE FROM stats
        WHERE timestamp NOT IN (
            SELECT timestamp
            FROM stats
            ORDER BY timestamp DESC
            LIMIT {MAX_HISTORICAL_ENTRIES}
        )
    ''')
    conn.commit()
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
    while True:
        try:
            stats = get_current_stats()
            store_stats(stats['cpu_percent'], stats['ram_percent'], stats['disk_percent'])
        except Exception as e:
            print(f"Error in historical_data_collector: {e}")
        time.sleep(HISTORICAL_DATA_COLLECTION_INTERVAL)


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
    """Parses server configurations from .env and stores them with their original index as key."""
    servers_map = {} # Use a map: index_str -> config
    i = 1
    while True:
        host_var = f'REMOTE_SERVER_{i}_HOST'
        if os.getenv(host_var):
            server_conf = {
                'original_index': i, # Store original index for reference
                'name': os.getenv(f'REMOTE_SERVER_{i}_NAME', os.getenv(host_var)),
                'host': os.getenv(host_var),
                'port': os.getenv(f'REMOTE_SERVER_{i}_PORT', '22'),
                'user': os.getenv(f'REMOTE_SERVER_{i}_USER'),
                'password': os.getenv(f'REMOTE_SERVER_{i}_PASSWORD'),
                'key_path': os.getenv(f'REMOTE_SERVER_{i}_KEY_PATH'),
                'key_passphrase': os.getenv(f'REMOTE_SERVER_{i}_KEY_PASSPHRASE'),
                'disk_path': os.getenv(f'REMOTE_SERVER_{i}_DISK_PATH', '/'),
                'jump_server_index': os.getenv(f'REMOTE_SERVER_{i}_JUMP_SERVER') # String or None
            }

            # Basic validation
            if not server_conf['user']:
                print(f"Warning: REMOTE_SERVER_{i}_USER not set. Skipping server {server_conf['name']}.")
            elif not server_conf['password'] and not server_conf['key_path']:
                print(f"Warning: Neither PASSWORD nor KEY_PATH set for REMOTE_SERVER_{i}. Skipping server {server_conf['name']}.")
            else:
                servers_map[str(i)] = server_conf # Store with string index '1', '2', etc.
            i += 1
        else:
            break
    return servers_map


# --- Flask Routes ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/current_stats')
def api_current_stats(): return jsonify(get_current_stats())

@app.route('/api/historical_stats')
def api_historical_stats():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, cpu_percent, ram_percent, disk_percent FROM stats ORDER BY timestamp ASC")
    rows = cursor.fetchall()
    conn.close()
    data = {
        'labels': [row['timestamp'] for row in rows],
        'cpu_data': [row['cpu_percent'] for row in rows],
        'ram_data': [row['ram_percent'] for row in rows],
        'disk_data': [row['disk_percent'] for row in rows]
    }
    return jsonify(data)

@app.route('/api/remote_servers_stats')
def api_remote_servers_stats():
    # `server_configs_map` is a dict: {'1': config1, '2': config2}
    server_configs_map = parse_remote_server_configs()
    all_stats = []

    # Convert map values to a list of configs to iterate for thread pool
    configs_to_process = list(server_configs_map.values())

    if not configs_to_process:
        return jsonify([])

    with ThreadPoolExecutor(max_workers=len(configs_to_process)) as executor:
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
