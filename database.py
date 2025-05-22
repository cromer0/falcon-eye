import os
import sqlite3
import psycopg2
import psycopg2.extras # For DictCursor later if needed for other functions
import json # Added import
from dotenv import load_dotenv

load_dotenv() # Ensure .env is loaded if database.py is run standalone or imported early

DATABASE_TYPE = os.getenv("DATABASE_TYPE", "sqlite")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/sys_stats.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MAX_HISTORICAL_ENTRIES = int(os.getenv("MAX_HISTORICAL_ENTRIES", 1440)) # Added config

def get_db_connection():
    try:
        if DATABASE_TYPE == "postgres":
            if not DATABASE_URL:
                raise ValueError("DATABASE_URL must be set for PostgreSQL connections.")
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        else: # Default to SQLite
            # Ensure the directory for SQLite DB exists
            os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
            conn = sqlite3.connect(DATABASE_PATH)
            return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        # Optionally, re-raise the exception or handle it as per application needs
        raise

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DROP TABLE IF EXISTS stats;") # Keep this for legacy cleanup if any

        # --- historical_stats table ---
        if DATABASE_TYPE == "postgres":
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historical_stats (
                    id SERIAL PRIMARY KEY,
                    server_host TEXT NOT NULL,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    cpu_percent REAL,
                    ram_percent REAL,
                    disk_percent REAL
                )
            ''')
        else: # SQLite
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historical_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_host TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    cpu_percent REAL,
                    ram_percent REAL,
                    disk_percent REAL
                )
            ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_server_host_timestamp ON historical_stats (server_host, timestamp);")

        # --- alerts table ---
        if DATABASE_TYPE == "postgres":
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id SERIAL PRIMARY KEY,
                    alert_name TEXT NOT NULL,
                    server_hosts JSONB NOT NULL,
                    resource_types JSONB NOT NULL,
                    threshold_percent REAL NOT NULL,
                    time_frame_minutes INTEGER NOT NULL,
                    communication_channel TEXT NOT NULL,
                    recipients JSONB NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    last_triggered_at TIMESTAMP WITH TIME ZONE NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else: # SQLite
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_name TEXT NOT NULL,
                    server_hosts TEXT NOT NULL, -- Store as JSON string
                    resource_types TEXT NOT NULL, -- Store as JSON string
                    threshold_percent REAL NOT NULL,
                    time_frame_minutes INTEGER NOT NULL,
                    communication_channel TEXT NOT NULL,
                    recipients TEXT NOT NULL, -- Store as JSON string
                    is_active INTEGER NOT NULL DEFAULT 1, -- 0 for false, 1 for true
                    last_triggered_at DATETIME NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_is_active ON alerts (is_active);")
        
        conn.commit()
    except Exception as e:
        print(f"Error during DB initialization: {e}")
        conn.rollback() # Rollback changes if an error occurs
    finally:
        cursor.close()
        conn.close()

def get_historical_stats_from_db(server_host_filter=None):
    conn = get_db_connection()
    
    # Determine cursor type for dictionary-like access
    # Using DictCursor for psycopg2 to get dict-like rows directly
    # For SQLite, conn.row_factory = sqlite3.Row provides dict-like access
    if DATABASE_TYPE == "postgres":
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    else: # SQLite
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

    rows_data = []
    try:
        placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"
        query = "SELECT timestamp, cpu_percent, ram_percent, disk_percent, server_host FROM historical_stats"
        params = []

        if server_host_filter:
            query += f" WHERE server_host = {placeholder}"
            params.append(server_host_filter)
        query += " ORDER BY timestamp ASC"

        cursor.execute(query, params)
        fetched_rows = cursor.fetchall()
        
        # Convert rows to list of standard dicts for consistent return type
        # sqlite3.Row objects are dict-like but not actual dicts.
        # psycopg2.extras.DictRow are also special, converting to dict ensures serializability.
        rows_data = [dict(row) for row in fetched_rows]

    except Exception as e:
        print(f"Error in get_historical_stats_from_db: {e}")
        # Return empty structure on error, or re-raise
        return {'labels': [], 'cpu_data': [], 'ram_data': [], 'disk_data': [], 'server_hosts': []}
    finally:
        cursor.close()
        conn.close()

    # Prepare data in the format expected by the frontend
    # This logic is moved from app.py's api_historical_stats
    import datetime # Ensure datetime is available for isinstance check
    if not server_host_filter and rows_data:
        data = {
            'labels': [row['timestamp'].isoformat() if isinstance(row['timestamp'], datetime.datetime) else str(row['timestamp']) for row in rows_data],
            'cpu_data': [row['cpu_percent'] for row in rows_data],
            'ram_data': [row['ram_percent'] for row in rows_data],
            'disk_data': [row['disk_percent'] for row in rows_data],
            'server_hosts': [row['server_host'] for row in rows_data]
        }
    else: # Single server request or no data
        data = {
            'labels': [row['timestamp'].isoformat() if isinstance(row['timestamp'], datetime.datetime) else str(row['timestamp']) for row in rows_data],
            'cpu_data': [row['cpu_percent'] for row in rows_data],
            'ram_data': [row['ram_percent'] for row in rows_data],
            'disk_data': [row['disk_percent'] for row in rows_data]
            # server_hosts field is omitted for single server view as it's implicit
        }
    return data

# --- Data Access for Alert Evaluation ---

def get_active_alerts_for_evaluation():
    conn = get_db_connection()
    cursor = conn.cursor()
    alerts = []
    active_param = True if DATABASE_TYPE == "postgres" else 1
    sql = "SELECT * FROM alerts WHERE is_active = %s" if DATABASE_TYPE == "postgres" else "SELECT * FROM alerts WHERE is_active = ?"
    
    try:
        cursor.execute(sql, (active_param,))
        rows = cursor.fetchall()
        for row_tuple in rows:
            alerts.append(_format_alert_from_db(row_tuple, cursor.description, DATABASE_TYPE))
        return alerts
    except Exception as e:
        print(f"Error fetching active alerts for evaluation: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_historical_data_for_alert_check(server_hosts, time_window_start):
    if not server_hosts:
        return []
        
    conn = get_db_connection()
    cursor = conn.cursor()
    results = []

    try:
        placeholders = ', '.join([("%s" if DATABASE_TYPE == "postgres" else "?")] * len(server_hosts))
        # Ensure time_window_start is a datetime object, it should be passed as such
        sql_placeholder_timestamp = "%s" if DATABASE_TYPE == "postgres" else "?"
        
        sql = f"""
            SELECT server_host, timestamp, cpu_percent, ram_percent, disk_percent 
            FROM historical_stats 
            WHERE server_host IN ({placeholders}) AND timestamp >= {sql_placeholder_timestamp}
            ORDER BY server_host, timestamp ASC
        """
        params = list(server_hosts) + [time_window_start]
        
        cursor.execute(sql, tuple(params)) # Ensure params is a tuple for some drivers
        
        column_names = [desc[0] for desc in cursor.description]
        for row_tuple in cursor.fetchall():
            results.append(dict(zip(column_names, row_tuple)))
            
        return results
    except Exception as e:
        print(f"Error fetching historical data for alert check: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def update_alert_last_triggered(alert_id, trigger_timestamp):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"
    
    sql = f"UPDATE alerts SET last_triggered_at = {placeholder} WHERE id = {placeholder}"
    
    try:
        cursor.execute(sql, (trigger_timestamp, alert_id))
        updated_rows = cursor.rowcount
        conn.commit()
        return updated_rows > 0
    except Exception as e:
        conn.rollback()
        print(f"Error updating alert last_triggered_at: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

def store_server_stats(server_host, cpu, ram, disk):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"
    
    try:
        sql_insert = f"INSERT INTO historical_stats (server_host, cpu_percent, ram_percent, disk_percent) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})"
        cursor.execute(sql_insert, (server_host, cpu, ram, disk))

        if DATABASE_TYPE == "postgres":
            sql_delete = f'''
                DELETE FROM historical_stats
                WHERE id IN (
                    SELECT id
                    FROM historical_stats
                    WHERE server_host = {placeholder}
                    ORDER BY timestamp ASC
                    OFFSET 0 
                    LIMIT GREATEST(0, (SELECT COUNT(*) FROM historical_stats WHERE server_host = {placeholder}) - {placeholder}::integer)
                )
            '''
            cursor.execute(sql_delete, (server_host, server_host, MAX_HISTORICAL_ENTRIES))
        else: # SQLite
            sql_delete = f'''
                DELETE FROM historical_stats
                WHERE id IN (
                    SELECT id
                    FROM historical_stats
                    WHERE server_host = ?
                    ORDER BY timestamp ASC
                    LIMIT MAX(0, (SELECT COUNT(*) FROM historical_stats WHERE server_host = ?) - ?)
                )
            '''
            cursor.execute(sql_delete, (server_host, server_host, MAX_HISTORICAL_ENTRIES))
        
        conn.commit()
    except Exception as e:
        print(f"Error in store_server_stats: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

# --- Alert CRUD DB Operations ---

def _format_alert_from_db(alert_row_tuple, cursor_description, db_type):
    if alert_row_tuple is None:
        return None
    
    columns = [desc[0] for desc in cursor_description]
    alert_dict = dict(zip(columns, alert_row_tuple))

    for field in ['server_hosts', 'resource_types', 'recipients']:
        if field in alert_dict and alert_dict[field] is not None:
            if db_type == "sqlite" and isinstance(alert_dict[field], str):
                try:
                    alert_dict[field] = json.loads(alert_dict[field])
                except json.JSONDecodeError:
                    print(f"Warning: Could not decode JSON for field {field} with value {alert_dict[field]}")
                    alert_dict[field] = [] # Default to empty list on error
            elif db_type == "postgres": # psycopg2 typically handles JSONB to list/dict
                if not isinstance(alert_dict[field], (list, dict)): # Fallback if it's still a string
                    try:
                        alert_dict[field] = json.loads(alert_dict[field])
                    except json.JSONDecodeError:
                        print(f"Warning: Could not decode JSON (postgres) for field {field} with value {alert_dict[field]}")
                        alert_dict[field] = []
        else: # Field might be missing or None
             alert_dict[field] = []


    if db_type == "sqlite":
        alert_dict['is_active'] = bool(alert_dict.get('is_active', 0))
    # For postgres, it should already be a boolean.

    # Ensure datetime objects are strings for JSON
    # This relies on psycopg2 returning datetime objects for TIMESTAMP types
    # and sqlite3 returning strings (which might already be ISO format or need parsing then reformatting)
    # For simplicity, we'll assume they are either datetime objects or already valid ISO strings.
    # If they are datetime objects, format them.
    if 'created_at' in alert_dict and hasattr(alert_dict['created_at'], 'isoformat'):
        alert_dict['created_at'] = alert_dict['created_at'].isoformat()
    if 'last_triggered_at' in alert_dict and alert_dict['last_triggered_at'] and hasattr(alert_dict['last_triggered_at'], 'isoformat'):
        alert_dict['last_triggered_at'] = alert_dict['last_triggered_at'].isoformat()
    
    return alert_dict


def create_alert_in_db(alert_data):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"
    
    # Prepare data for DB
    db_alert_data = alert_data.copy()
    for field in ['server_hosts', 'resource_types', 'recipients']:
        if DATABASE_TYPE == "sqlite":
            db_alert_data[field] = json.dumps(alert_data[field])
        # For Postgres with JSONB, psycopg2 handles Python lists/dicts directly

    is_active_val = db_alert_data.get('is_active', True)
    if DATABASE_TYPE == 'sqlite':
        is_active_val = 1 if is_active_val else 0

    sql = f"""
        INSERT INTO alerts (alert_name, server_hosts, resource_types, threshold_percent, time_frame_minutes, communication_channel, recipients, is_active)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
    """
    params = (
        db_alert_data['alert_name'], db_alert_data['server_hosts'], db_alert_data['resource_types'], 
        db_alert_data['threshold_percent'], db_alert_data['time_frame_minutes'], 
        db_alert_data['communication_channel'], db_alert_data['recipients'], 
        is_active_val
    )

    try:
        if DATABASE_TYPE == "postgres":
            sql += " RETURNING id" # Get ID back for Postgres
            cursor.execute(sql, params)
            alert_id = cursor.fetchone()[0]
        else: # SQLite
            cursor.execute(sql, params)
            alert_id = cursor.lastrowid
        conn.commit()
        return alert_id
    except Exception as e:
        conn.rollback()
        print(f"Error creating alert in DB: {e}")
        raise # Re-raise to be handled by API layer
    finally:
        cursor.close()
        conn.close()

def get_alerts_from_db():
    conn = get_db_connection()
    # For psycopg2, DictCursor is useful but we defined _format_alert_from_db to take a tuple and description
    # So, we'll use a standard cursor here for consistency with the helper.
    cursor = conn.cursor() 
    alerts = []
    try:
        cursor.execute("SELECT * FROM alerts ORDER BY created_at DESC")
        rows = cursor.fetchall()
        for row_tuple in rows:
            alerts.append(_format_alert_from_db(row_tuple, cursor.description, DATABASE_TYPE))
        return alerts
    except Exception as e:
        print(f"Error fetching alerts from DB: {e}")
        return [] # Return empty list on error
    finally:
        cursor.close()
        conn.close()

def get_alert_by_id_from_db(alert_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"
    try:
        cursor.execute(f"SELECT * FROM alerts WHERE id = {placeholder}", (alert_id,))
        row_tuple = cursor.fetchone()
        if row_tuple:
            return _format_alert_from_db(row_tuple, cursor.description, DATABASE_TYPE)
        return None
    except Exception as e:
        print(f"Error fetching alert by ID from DB: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def update_alert_in_db(alert_id, update_data):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"

    set_clauses = []
    params = []

    for key, value in update_data.items():
        # Handle JSON stringification for SQLite
        if key in ['server_hosts', 'resource_types', 'recipients'] and DATABASE_TYPE == "sqlite":
            value = json.dumps(value)
        # Handle boolean for SQLite
        if key == 'is_active' and DATABASE_TYPE == 'sqlite':
            value = 1 if value else 0
        
        set_clauses.append(f"{key} = {placeholder}")
        params.append(value)
    
    if not set_clauses:
        return False # Nothing to update

    params.append(alert_id) # For the WHERE clause

    try:
        sql = f"UPDATE alerts SET {', '.join(set_clauses)} WHERE id = {placeholder}"
        cursor.execute(sql, tuple(params))
        updated_rows = cursor.rowcount
        conn.commit()
        return updated_rows > 0
    except Exception as e:
        conn.rollback()
        print(f"Error updating alert in DB: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def delete_alert_from_db(alert_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_TYPE == "postgres" else "?"
    try:
        cursor.execute(f"DELETE FROM alerts WHERE id = {placeholder}", (alert_id,))
        deleted_rows = cursor.rowcount
        conn.commit()
        return deleted_rows > 0
    except Exception as e:
        conn.rollback()
        print(f"Error deleting alert from DB: {e}")
        raise
    finally:
        cursor.close()
        conn.close()
