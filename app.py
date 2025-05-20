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
load_dotenv() # Load environment variables from .env file
DATABASE_PATH = os.path.join('data', 'sys_stats.db')
HISTORICAL_DATA_COLLECTION_INTERVAL = 60  # seconds (e.g., 1 minute)
MAX_HISTORICAL_ENTRIES = 1440 # Keep roughly 24 hours of data if interval is 1 minute (24*60)
SSH_TIMEOUT = 15 # Seconds for SSH connection and command execution

app = Flask(__name__)

# --- Database Setup ---
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
    # Index for faster queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON stats (timestamp);")
    conn.commit()
    conn.close()

def store_stats(cpu, ram, disk):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO stats (cpu_percent, ram_percent, disk_percent) VALUES (?, ?, ?)",
                   (cpu, ram, disk))
    # Prune old data
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

# --- Data Collection ---
def get_current_stats():
    cpu_percent = psutil.cpu_percent(interval=0.1) # Non-blocking, short interval
    ram_stats = psutil.virtual_memory()
    ram_percent = ram_stats.percent
    # For disk, you might want to specify a path, e.g., psutil.disk_usage('/')
    # Or sum up all mount points if you have multiple.
    # For simplicity, we'll use the root disk.
    try:
        disk_stats = psutil.disk_usage('/')
        disk_percent = disk_stats.percent
    except FileNotFoundError: # Handles cases like WSL where '/' might not be standard
        disk_percent = 0.0
        print("Warning: Could not get disk usage for '/'. Defaulting to 0.")

    return {
        'cpu_percent': cpu_percent,
        'ram_percent': ram_percent,
        'ram_total_gb': round(ram_stats.total / (1024**3), 2),
        'ram_used_gb': round(ram_stats.used / (1024**3), 2),
        'disk_percent': disk_percent,
        'disk_total_gb': round(disk_stats.total / (1024**3), 2) if 'disk_stats' in locals() else 0,
        'disk_used_gb': round(disk_stats.used / (1024**3), 2) if 'disk_stats' in locals() else 0,
        'timestamp': datetime.datetime.now().isoformat()
    }

def historical_data_collector():
    print("Starting historical data collection thread...")
    while True:
        try:
            stats = get_current_stats() # Re-use the same function
            store_stats(stats['cpu_percent'], stats['ram_percent'], stats['disk_percent'])
            # print(f"Stored historical data: CPU {stats['cpu_percent']}% RAM {stats['ram_percent']}% DISK {stats['disk_percent']}%")
        except Exception as e:
            print(f"Error in historical_data_collector: {e}")
        time.sleep(HISTORICAL_DATA_COLLECTION_INTERVAL)

def get_remote_server_stats(server_config):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    name = server_config.get('name', server_config['host'])
    host = server_config.get('host')
    port = int(server_config.get('port', 22))
    user = server_config.get('user')
    password = server_config.get('password')
    key_path = server_config.get('key_path')
    key_passphrase = server_config.get('key_passphrase')
    disk_to_monitor = server_config.get('disk_path', '/')

    base_result = {
        'name': name, 'host': host, 'status': 'offline',
        'cpu_percent': 0.0, 'cpu_cores': 0, 'cpu_model': "N/A", # New CPU info fields
        'ram_percent': 0.0, 'ram_total_gb': 0.0, 'ram_used_gb': 0.0,
        'disk_percent': 0.0, 'disk_total_gb': 0.0, 'disk_used_gb': 0.0,
        'error_message': None
    }

    try:
        connect_args = {'hostname': host, 'port': port, 'username': user, 'timeout': SSH_TIMEOUT}
        if key_path:
            expanded_key_path = os.path.expanduser(key_path)
            connect_args['key_filename'] = expanded_key_path
            if key_passphrase: connect_args['passphrase'] = key_passphrase
        elif password:
            connect_args['password'] = password
        else:
            base_result['error_message'] = "No password or key_path configured."
            return base_result

        client.connect(**connect_args)
        delimiter = "###STATS_DELIMITER###"

        cpu_usage_cmd = "LC_ALL=C vmstat 1 2 | awk 'END{print 100-$15}'"
        ram_cmd = "awk '/^MemTotal:/{total=$2} /^MemAvailable:/{available=$2} END{used=total-available; if (total > 0) printf \"%.2f###%.2f###%.2f\", (used*100)/total, used/1024/1024, total/1024/1024; else print \"ERROR_RAM_CALC\";}' /proc/meminfo"
        disk_cmd = f"LC_ALL=C df -P -B1K '{disk_to_monitor}' | awk 'NR==2 {{printf \"%s###%s###%s\", substr($5, 1, length($5)-1), $3, $2}}'"

        # New commands for CPU cores and model
        cpu_cores_cmd = "nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo 0" # Try nproc, fallback to /proc/cpuinfo, then 0
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
        # print(f"DEBUG ({name}): Executing command: {full_command}")

        stdin, stdout, stderr = client.exec_command(full_command, timeout=SSH_TIMEOUT)

        raw_output = stdout.read().decode(errors='ignore').strip()
        ssh_stderr_output = stderr.read().decode(errors='ignore').strip()
        ssh_exit_status = stdout.channel.recv_exit_status()

        # print(f"DEBUG ({name}): SSH Exit Status: {ssh_exit_status}")
        # print(f"DEBUG ({name}): Raw Output from SSH: '{raw_output}'")
        # print(f"DEBUG ({name}): Stderr from SSH command execution: '{ssh_stderr_output}'")

        current_error_messages = []

        if ssh_exit_status == 0 and raw_output:
            parts = raw_output.split(delimiter)
            # print(f"DEBUG ({name}): Split parts: {parts}")

            if len(parts) == 5: # Now expecting 5 parts
                cpu_usage_str, ram_str, disk_str, cpu_cores_str, cpu_model_str = parts
                # print(f"DEBUG ({name}): cpu_usage='{cpu_usage_str}', ram='{ram_str}', disk='{disk_str}', cores='{cpu_cores_str}', model='{cpu_model_str}'")

                # Parse CPU Usage
                if "ERROR_CPU_USAGE" in cpu_usage_str or not cpu_usage_str: # '0' is not empty and not ERROR
                    current_error_messages.append("CPU usage data retrieval failed.")
                else:
                    try: base_result['cpu_percent'] = float(cpu_usage_str) # float('0') is 0.0
                    except ValueError: current_error_messages.append(f"Invalid CPU usage value: '{cpu_usage_str}'.")

                # Parse RAM - '58.81###0.26###0.44'
                if "ERROR_RAM" in ram_str or not ram_str: current_error_messages.append("RAM data retrieval failed.")
                else:
                    ram_parts = ram_str.split('###') # ['58.81', '0.26', '0.44']
                    if len(ram_parts) == 3:
                        try:
                            base_result['ram_percent'] = float(ram_parts[0]) # 58.81
                            base_result['ram_used_gb'] = float(ram_parts[1]) # 0.26
                            base_result['ram_total_gb'] = float(ram_parts[2])# 0.44
                        except ValueError: current_error_messages.append(f"Invalid RAM values: '{ram_str}'.")
                    else: current_error_messages.append(f"Unexpected RAM format: '{ram_str}'.")

                # Parse DISK - '71###6348788###9065864'
                if "ERROR_DISK" in disk_str or not disk_str: current_error_messages.append("Disk data retrieval failed.")
                else:
                    disk_parts = disk_str.split('###') # ['71', '6348788', '9065864']
                    if len(disk_parts) == 3:
                        try:
                            base_result['disk_percent'] = float(disk_parts[0]) # 71.0
                            base_result['disk_used_gb'] = round(float(disk_parts[1]) / (1024 * 1024), 2) # Should be ~6.05
                            base_result['disk_total_gb'] = round(float(disk_parts[2]) / (1024 * 1024), 2) # Should be ~8.65
                        except ValueError: current_error_messages.append(f"Invalid Disk values: '{disk_str}'.")
                    else: current_error_messages.append(f"Unexpected Disk format: '{disk_str}'.")

                # Parse CPU Cores - '1'
                if "ERROR_CPU_CORES" in cpu_cores_str or not cpu_cores_str:
                    current_error_messages.append("CPU cores data retrieval failed.")
                    base_result['cpu_cores'] = 0
                else:
                    try: base_result['cpu_cores'] = int(cpu_cores_str) # int('1') is 1
                    except ValueError:
                        current_error_messages.append(f"Invalid CPU cores value: '{cpu_cores_str}'.")
                        base_result['cpu_cores'] = 0

                # Parse CPU Model - 'DO-Regular'
                if "ERROR_CPU_MODEL" in cpu_model_str or not cpu_model_str.strip() or cpu_model_str.strip().upper() == "N/A":
                    current_error_messages.append("CPU model data retrieval failed or N/A.")
                    base_result['cpu_model'] = "N/A"
                else:
                    base_result['cpu_model'] = cpu_model_str.strip() # "DO-Regular"

                if not current_error_messages: # This should be true now
                    base_result['status'] = 'online'
                else:
                    base_result['status'] = 'error'
                    base_result['error_message'] = " | ".join(current_error_messages)

                if not current_error_messages:
                    base_result['status'] = 'online'
                else:
                    base_result['status'] = 'error'
                    base_result['error_message'] = " | ".join(current_error_messages)
            else:
                base_result['status'] = 'error'
                base_result['error_message'] = f"Output format error. Expected 5 parts, got {len(parts)}. Output: '{raw_output[:150]}...'"

        elif ssh_exit_status != 0: # ... (rest of the error handling as before) ...
            base_result['status'] = 'error'
            err_msg = f"Remote script failed (exit: {ssh_exit_status})."
            if ssh_stderr_output: err_msg += f" Stderr: {ssh_stderr_output}"
            elif raw_output: err_msg += f" Stdout: {raw_output[:100]}..."
            base_result['error_message'] = err_msg
        elif not raw_output:
            base_result['status'] = 'error'
            base_result['error_message'] = f"No output from remote command (exit: {ssh_exit_status})."

    except paramiko.AuthenticationException:
        base_result['status'] = 'error'; base_result['error_message'] = "Authentication failed."
    except paramiko.SSHException as e:
        base_result['status'] = 'error'; base_result['error_message'] = f"SSH error: {str(e)}"
    except Exception as e:
        base_result['status'] = 'error'; base_result['error_message'] = f"General error: {str(e)}"
    finally:
        client.close()

    # Final sanitization for numeric values
    for key in ['cpu_percent', 'ram_percent', 'ram_total_gb', 'ram_used_gb', 'disk_percent', 'disk_total_gb', 'disk_used_gb']:
        try: base_result[key] = float(base_result.get(key, 0.0))
        except (ValueError, TypeError): base_result[key] = 0.0

    # Sanitize cpu_cores to be an int
    try: base_result['cpu_cores'] = int(base_result.get('cpu_cores', 0))
    except (ValueError, TypeError): base_result['cpu_cores'] = 0

    # Ensure cpu_model is a string
    if not isinstance(base_result.get('cpu_model'), str) or not base_result.get('cpu_model', "").strip():
        base_result['cpu_model'] = "N/A"


    if base_result['status'] == 'error' and not base_result['error_message']:
        base_result['error_message'] = "Unknown error during data retrieval."

    if base_result['error_message'] and base_result['status'] == 'error':
        print(f"FINAL Error for {name} ({host}): {base_result['error_message']}")

    #print(f"FINAL base_result for {name}: {base_result}")
    return base_result

    return base_result

def parse_remote_server_configs():
    servers = []
    i = 1
    while True:
        host_var = f'REMOTE_SERVER_{i}_HOST'
        if os.getenv(host_var):
            server_conf = {
                'name': os.getenv(f'REMOTE_SERVER_{i}_NAME', os.getenv(host_var)),
                'host': os.getenv(host_var),
                'port': os.getenv(f'REMOTE_SERVER_{i}_PORT', '22'),
                'user': os.getenv(f'REMOTE_SERVER_{i}_USER'),
                'password': os.getenv(f'REMOTE_SERVER_{i}_PASSWORD'),
                'key_path': os.getenv(f'REMOTE_SERVER_{i}_KEY_PATH'),
                'key_passphrase': os.getenv(f'REMOTE_SERVER_{i}_KEY_PASSPHRASE'),
                'disk_path': os.getenv(f'REMOTE_SERVER_{i}_DISK_PATH', '/')
            }
            if not server_conf['user']:
                print(f"Warning: REMOTE_SERVER_{i}_USER not set. Skipping server {server_conf['name']}.")
            elif not server_conf['password'] and not server_conf['key_path']:
                print(f"Warning: Neither PASSWORD nor KEY_PATH set for REMOTE_SERVER_{i}. Skipping server {server_conf['name']}.")
            else:
                servers.append(server_conf)
            i += 1
        else:
            break # No more servers defined
    return servers

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/current_stats')
def api_current_stats():
    return jsonify(get_current_stats())

@app.route('/api/historical_stats')
def api_historical_stats():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row # Access columns by name
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, cpu_percent, ram_percent, disk_percent FROM stats ORDER BY timestamp ASC")
    rows = cursor.fetchall()
    conn.close()

    # Format for Chart.js
    data = {
        'labels': [row['timestamp'] for row in rows],
        'cpu_data': [row['cpu_percent'] for row in rows],
        'ram_data': [row['ram_percent'] for row in rows],
        'disk_data': [row['disk_percent'] for row in rows]
    }
    return jsonify(data)

@app.route('/api/remote_servers_stats')
def api_remote_servers_stats():
    server_configs = parse_remote_server_configs()
    all_stats = []

    # Use ThreadPoolExecutor to fetch stats concurrently
    # Ensure 'ThreadPoolExecutor' is imported from 'concurrent.futures'
    with ThreadPoolExecutor(max_workers=len(server_configs) or 1) as executor: # This line was causing the NameError
        future_to_server = {executor.submit(get_remote_server_stats, config): config for config in server_configs}
        for future in future_to_server: # Corrected: iterate over keys of the dictionary
            config = future_to_server[future] # Get the config associated with this future
            try:
                stats = future.result() # This blocks until the future is complete
                all_stats.append(stats)
            except Exception as exc:
                server_name = config.get('name', 'Unknown Server')
                print(f'{server_name} (Host: {config.get("host")}) generated an exception during future.result(): {exc}')
                # Append a basic error structure if the task itself failed catastrophically
                all_stats.append({
                    'name': server_name, 'host': config.get('host'),
                    'status': 'error', 'error_message': f'Task execution failed: {str(exc)}',
                    'cpu_percent': 0, 'ram_percent': 0, 'disk_percent': 0,
                    'ram_total_gb': 0, 'ram_used_gb': 0,
                    'disk_total_gb': 0, 'disk_used_gb': 0,
                })

    return jsonify(all_stats)



if __name__ == '__main__':
    init_db()
    # Start the background thread for collecting historical data
    collector_thread = threading.Thread(target=historical_data_collector, daemon=True)
    collector_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000) # Make accessible on network