from flask import Flask, render_template, jsonify
import psutil
import sqlite3
import threading
import time
import datetime
import os

# --- Configuration ---
DATABASE_PATH = os.path.join('data', 'sys_stats.db')
HISTORICAL_DATA_COLLECTION_INTERVAL = 60  # seconds (e.g., 1 minute)
MAX_HISTORICAL_ENTRIES = 1440 # Keep roughly 24 hours of data if interval is 1 minute (24*60)

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

if __name__ == '__main__':
    init_db()
    # Start the background thread for collecting historical data
    collector_thread = threading.Thread(target=historical_data_collector, daemon=True)
    collector_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000) # Make accessible on network