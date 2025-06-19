import unittest
from unittest.mock import patch, MagicMock, ANY
import sqlite3
import sys
import os
import json
from datetime import datetime, timedelta

# Add parent directory to path to import app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set a dummy FLASK_SECRET_KEY for testing session management
os.environ['FLASK_SECRET_KEY'] = 'test_secret_key'
# Set DB type for tests (can be overridden per test if needed)
os.environ['DATABASE_TYPE'] = 'sqlite'
# Use in-memory SQLite for tests if DATABASE_PATH is not set, or override for specific tests
os.environ['DATABASE_PATH'] = ':memory:'

# SMTP Mocking: Ensure these are set to avoid accidental real emails
# and to test the "not configured" path by default.
os.environ['SMTP_HOST'] = ''
os.environ['EMAIL_FROM_ADDRESS'] = ''


from app import app, init_db, get_db_connection, collector_status_info, ALERT_COOLDOWN_MINUTES, MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE, current_collection_interval

class TestAlertsAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Initialize the in-memory DB for the test class
        init_db()

    def setUp(self):
        self.app = app.test_client()
        # Clean and re-init the database for each test to ensure isolation
        # This is a bit heavy but ensures test independence for an in-memory DB.
        # For file-based DB, you might clean tables instead.
        with app.app_context(): # Need app context for get_db_connection
            conn, cursor = get_db_connection()
            cursor.execute("DELETE FROM alerts")
            cursor.execute("DELETE FROM stats")
            # Add other cleanup if necessary
            conn.commit()
            conn.close()

    def tearDown(self):
        # Could add more cleanup here if needed
        pass

    def _login(self):
        # Helper to simulate login by setting session variable
        with self.app as client:
            with client.session_transaction() as sess:
                sess['logged_in'] = True

    def test_01_create_alert_success(self):
        self._login()
        response = self.app.post('/api/alerts', json={
            "alert_name": "Test CPU High",
            "server_name": "local",
            "resource_type": "cpu",
            "threshold_percentage": 80.0,
            "time_window_minutes": 5,
            "emails": "test@example.com"
        })
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertIn("alert_id", data)
        self.assertEqual(data["alert_name"], "Test CPU High")

    def test_02_create_alert_missing_field(self):
        self._login()
        response = self.app.post('/api/alerts', json={
            "server_name": "local", # alert_name is missing
            "resource_type": "cpu",
            "threshold_percentage": 80.0,
            "time_window_minutes": 5,
            "emails": "test@example.com"
        })
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("Missing required field: alert_name", data["error"])

    def test_03_create_alert_invalid_email(self):
        self._login()
        response = self.app.post('/api/alerts', json={
            "alert_name": "Test Invalid Email",
            "server_name": "local",
            "resource_type": "cpu",
            "threshold_percentage": 80.0,
            "time_window_minutes": 5,
            "emails": "testexample.com" # Invalid email
        })
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("Invalid email format", data["error"])

    def test_04_get_all_alerts_empty(self):
        self._login()
        response = self.app.get('/api/alerts')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])

    def test_05_get_all_alerts_with_data(self):
        self._login()
        # Create an alert first
        self.app.post('/api/alerts', json={
            "alert_name": "Test CPU High List", "server_name": "local", "resource_type": "cpu",
            "threshold_percentage": 80.0, "time_window_minutes": 5, "emails": "list@example.com"
        })
        response = self.app.get('/api/alerts')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["alert_name"], "Test CPU High List")

    def test_06_get_specific_alert_success(self):
        self._login()
        create_resp = self.app.post('/api/alerts', json={
            "alert_name": "Specific Alert", "server_name": "server1", "resource_type": "ram",
            "threshold_percentage": 70.0, "time_window_minutes": 10, "emails": "spec@example.com"
        })
        alert_id = create_resp.get_json()["alert_id"]

        response = self.app.get(f'/api/alerts/{alert_id}')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["alert_name"], "Specific Alert")

    def test_07_get_specific_alert_not_found(self):
        self._login()
        response = self.app.get('/api/alerts/9999') # Non-existent ID
        self.assertEqual(response.status_code, 404)

    def test_08_update_alert_success(self):
        self._login()
        create_resp = self.app.post('/api/alerts', json={
            "alert_name": "Update Me", "server_name": "local", "resource_type": "disk",
            "threshold_percentage": 60.0, "time_window_minutes": 3, "emails": "update@example.com"
        })
        alert_id = create_resp.get_json()["alert_id"]

        response = self.app.put(f'/api/alerts/{alert_id}', json={"alert_name": "Updated Name", "threshold_percentage": 65.0})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["alert"]
        self.assertEqual(data["alert_name"], "Updated Name")
        self.assertEqual(data["threshold_percentage"], 65.0)

    def test_09_update_alert_not_found(self):
        self._login()
        response = self.app.put('/api/alerts/9999', json={"alert_name": "Won't Update"})
        self.assertEqual(response.status_code, 404)

    def test_10_delete_alert_success(self):
        self._login()
        create_resp = self.app.post('/api/alerts', json={
            "alert_name": "Delete Me", "server_name": "local", "resource_type": "cpu",
            "threshold_percentage": 50.0, "time_window_minutes": 1, "emails": "delete@example.com"
        })
        alert_id = create_resp.get_json()["alert_id"]

        response = self.app.delete(f'/api/alerts/{alert_id}')
        self.assertEqual(response.status_code, 200)

        # Verify it's gone
        get_response = self.app.get(f'/api/alerts/{alert_id}')
        self.assertEqual(get_response.status_code, 404)

    def test_11_delete_alert_not_found(self):
        self._login()
        response = self.app.delete('/api/alerts/9999')
        self.assertEqual(response.status_code, 404)

    def test_12_enable_disable_alert(self):
        self._login()
        create_resp = self.app.post('/api/alerts', json={
            "alert_name": "EnableDisable", "server_name": "local", "resource_type": "ram",
            "threshold_percentage": 77.0, "time_window_minutes": 7, "emails": "toggle@example.com", "is_enabled": True
        })
        alert_id = create_resp.get_json()["alert_id"]

        # Disable
        response_disable = self.app.post(f'/api/alerts/{alert_id}/disable')
        self.assertEqual(response_disable.status_code, 200)
        alert_data_disabled = self.app.get(f'/api/alerts/{alert_id}').get_json()
        self.assertEqual(alert_data_disabled["is_enabled"], False)

        # Enable
        response_enable = self.app.post(f'/api/alerts/{alert_id}/enable')
        self.assertEqual(response_enable.status_code, 200)
        alert_data_enabled = self.app.get(f'/api/alerts/{alert_id}').get_json()
        self.assertEqual(alert_data_enabled["is_enabled"], True)

# More test classes for evaluate_alerts and send_alert_email would follow
# For brevity in this response, I will sketch out evaluate_alerts tests.

class TestAlertEvaluation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.app_context = app.app_context() # For get_db_connection if used directly
        self.app_context.push()
        conn, cursor = get_db_connection()
        cursor.execute("DELETE FROM alerts")
        cursor.execute("DELETE FROM stats")
        conn.commit()
        conn.close()

        # Reset collector_status_info for each test
        collector_status_info["configured_server_names"] = ['local', 'server1', 'server2']
        collector_status_info["servers_configured_count"] = 3

        # Ensure app.current_collection_interval is set, if not available, use a default
        global current_collection_interval # Import from app
        self.original_collection_interval = current_collection_interval
        current_collection_interval = 60 # Example: 60 seconds for tests

    def tearDown(self):
        global current_collection_interval # Import from app
        current_collection_interval = self.original_collection_interval
        self.app_context.pop()


    def _create_test_alert(self, **kwargs):
        conn, cursor = get_db_connection()
        params = {
            "alert_name": "Test Alert", "server_name": "local", "resource_type": "cpu",
            "threshold_percentage": 80.0, "time_window_minutes": 5,
            "emails": "test@example.com", "is_enabled": True,
            "last_triggered_at": None, **kwargs # Allow overriding defaults
        }

        cols = ', '.join(params.keys())
        placeholders = ', '.join(['?'] * len(params))
        sql = f"INSERT INTO alerts ({cols}) VALUES ({placeholders})"
        cursor.execute(sql, tuple(params.values()))
        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()
        params['id'] = alert_id
        return params # Return the full alert dict including ID

    def _insert_stat_data(self, server_name, resource_type, value, timestamp_ago_minutes):
        conn, cursor = get_db_connection()
        ts = datetime.now() - timedelta(minutes=timestamp_ago_minutes)
        # SQLite stores datetime as TEXT by default in our app's format
        ts_str = ts.strftime('%Y-%m-%d %H:%M:%S')

        data = {"server_name": server_name, "timestamp": ts_str,
                "cpu_percent": 0, "ram_percent": 0, "disk_percent": 0}
        data[f"{resource_type}_percent"] = value

        cursor.execute("""
            INSERT INTO stats (server_name, timestamp, cpu_percent, ram_percent, disk_percent)
            VALUES (?, ?, ?, ?, ?)
        """, (data["server_name"], data["timestamp"], data["cpu_percent"], data["ram_percent"], data["disk_percent"]))
        conn.commit()
        conn.close()

    @patch('app.send_alert_email') # Patch the send_alert_email in app module
    def test_alert_should_trigger(self, mock_send_email):
        from app import evaluate_alerts # Import here to use the patched version

        alert_config = self._create_test_alert(time_window_minutes=2, threshold_percentage=50)

        # Insert stats that should trigger the alert (e.g. 3 points in a 2 min window if interval is 60s)
        # Assuming MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE = 0.8, 2*0.8 = 1.6, so needs 2 points.
        self._insert_stat_data("local", "cpu", 60, 0) # Now
        self._insert_stat_data("local", "cpu", 65, 1) # 1 min ago
        # self._insert_stat_data("local", "cpu", 70, 2) # 2 mins ago (just outside 2 min window if strictly >)
                                                  # but >= in query means this would be included.
                                                  # Let's be precise: window is last X minutes from NOW.
                                                  # So, if window is 2 mins, data from now up to 2 mins ago.

        evaluate_alerts()
        mock_send_email.assert_called_once()

        # Check if last_triggered_at was updated
        conn, cursor = get_db_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT last_triggered_at FROM alerts WHERE id = ?", (alert_config['id'],))
        updated_alert = cursor.fetchone()
        self.assertIsNotNone(updated_alert['last_triggered_at'])
        conn.close()

    @patch('app.send_alert_email')
    def test_alert_should_not_trigger_below_threshold(self, mock_send_email):
        from app import evaluate_alerts
        self._create_test_alert(time_window_minutes=2, threshold_percentage=50)
        self._insert_stat_data("local", "cpu", 40, 0)
        self._insert_stat_data("local", "cpu", 45, 1)

        evaluate_alerts()
        mock_send_email.assert_not_called()

    @patch('app.send_alert_email')
    def test_alert_cooldown_active(self, mock_send_email):
        from app import evaluate_alerts
        # Last triggered 10 minutes ago, cooldown is 30 minutes
        last_triggered = datetime.now() - timedelta(minutes=10)
        self._create_test_alert(time_window_minutes=2, threshold_percentage=50,
                               last_triggered_at=last_triggered.strftime('%Y-%m-%d %H:%M:%S'))
        self._insert_stat_data("local", "cpu", 60, 0)
        self._insert_stat_data("local", "cpu", 65, 1)

        evaluate_alerts()
        mock_send_email.assert_not_called()

    @patch('app.send_alert_email')
    def test_alert_insufficient_data(self, mock_send_email):
        from app import evaluate_alerts
        # Needs 2 points for 2 min window (80% of 2 points if interval=60s)
        self._create_test_alert(time_window_minutes=2, threshold_percentage=50)
        self._insert_stat_data("local", "cpu", 60, 0) # Only 1 point

        evaluate_alerts()
        mock_send_email.assert_not_called()

    @patch('app.send_alert_email')
    @patch('app.collector_status_info', {"configured_server_names": ['serverA', 'serverB', 'local']})
    def test_alert_wildcard_server(self, mock_collector_info, mock_send_email):
        from app import evaluate_alerts
        self._create_test_alert(server_name='*', time_window_minutes=2, threshold_percentage=50)

        # Trigger for serverA
        self._insert_stat_data("serverA", "cpu", 60, 0)
        self._insert_stat_data("serverA", "cpu", 65, 1)
        # Not for serverB
        self._insert_stat_data("serverB", "cpu", 40, 0)
        self._insert_stat_data("serverB", "cpu", 45, 1)
        # Trigger for local
        self._insert_stat_data("local", "cpu", 70, 0)
        self._insert_stat_data("local", "cpu", 75, 1)

        evaluate_alerts()
        self.assertEqual(mock_send_email.call_count, 2)
        # Check calls were for serverA and local
        # This requires more specific argument checking on the mock_send_email calls
        # For example: mock_send_email.assert_any_call(ANY, 'serverA', ANY, ANY)
        #              mock_send_email.assert_any_call(ANY, 'local', ANY, ANY)


class TestEmailSending(unittest.TestCase):

    @patch('app.smtplib.SMTP_SSL') # Patch where it's used in app.py
    @patch('app.smtplib.SMTP')    # Patch where it's used in app.py
    def test_send_email_tls(self, mock_smtp, mock_smtp_ssl):
        from app import send_alert_email # Import from app module

        os.environ['SMTP_HOST'] = 'smtp.example.com'
        os.environ['SMTP_PORT'] = '587'
        os.environ['SMTP_USER'] = 'user@example.com'
        os.environ['SMTP_PASSWORD'] = 'password'
        os.environ['SMTP_USE_TLS'] = 'true'
        os.environ['SMTP_USE_SSL'] = 'false'
        os.environ['EMAIL_FROM_ADDRESS'] = 'alerts@falconeye.com'

        mock_server_tls = MagicMock()
        mock_smtp.return_value = mock_server_tls # SMTP() returns our mock

        alert_data = {"alert_name": "TLS Test", "resource_type": "CPU",
                      "threshold_percentage": 90, "time_window_minutes": 5,
                      "emails": "receiver1@example.com, receiver2@example.com"}

        send_alert_email(alert_data, "test_server", 95.5, [92.0, 93.5, 95.5])

        mock_smtp.assert_called_with('smtp.example.com', 587, timeout=10)
        mock_server_tls.starttls.assert_called_once()
        mock_server_tls.login.assert_called_with('user@example.com', 'password')
        # Check sendmail arguments (recipients should be a list)
        call_args = mock_server_tls.sendmail.call_args
        self.assertEqual(call_args[0][0], 'alerts@falconeye.com')
        self.assertListEqual(sorted(call_args[0][1]), sorted(['receiver1@example.com', 'receiver2@example.com']))
        self.assertIn("Subject: FalconEye Alert: TLS Test triggered on test_server", call_args[0][2])
        mock_server_tls.quit.assert_called_once()
        mock_smtp_ssl.assert_not_called() # Ensure SSL was not used

    @patch('app.smtplib.SMTP_SSL')
    @patch('app.smtplib.SMTP')
    def test_send_email_ssl(self, mock_smtp, mock_smtp_ssl):
        from app import send_alert_email

        os.environ['SMTP_HOST'] = 'smtp.example.com'
        os.environ['SMTP_PORT'] = '465'
        os.environ['SMTP_USER'] = 'user@example.com'
        os.environ['SMTP_PASSWORD'] = 'password'
        os.environ['SMTP_USE_TLS'] = 'false' # Explicitly false for SSL
        os.environ['SMTP_USE_SSL'] = 'true'
        os.environ['EMAIL_FROM_ADDRESS'] = 'alerts@falconeye.com'

        mock_server_ssl = MagicMock()
        mock_smtp_ssl.return_value = mock_server_ssl # SMTP_SSL() returns our mock

        alert_data = {"alert_name": "SSL Test", "resource_type": "RAM",
                      "threshold_percentage": 80, "time_window_minutes": 10,
                      "emails": "onlyone@example.com"}

        send_alert_email(alert_data, "prod_server", 85.0, [82.0, 83.5, 85.0])

        mock_smtp_ssl.assert_called_with('smtp.example.com', 465, timeout=10)
        mock_server_ssl.login.assert_called_with('user@example.com', 'password')
        self.assertFalse(mock_server_ssl.starttls.called) # TLS should not be called
        mock_server_ssl.sendmail.assert_called_once()
        mock_server_ssl.quit.assert_called_once()
        mock_smtp.assert_not_called() # Ensure non-SSL SMTP was not used


    @patch('app.logger.warning') # Check that a warning is logged
    def test_send_email_not_configured(self, mock_logger_warning):
        from app import send_alert_email

        os.environ['SMTP_HOST'] = '' # Not configured
        os.environ['EMAIL_FROM_ADDRESS'] = '' # Not configured

        alert_data = {"alert_name": "No SMTP Test", "emails": "no@email.com",
                      "resource_type": "Disk", "threshold_percentage": 70, "time_window_minutes": 15}
        send_alert_email(alert_data, "any_server", 75, [77])

        # Check if logger.warning was called with the expected message
        mock_logger_warning.assert_any_call("SMTP_HOST or EMAIL_FROM_ADDRESS not configured. Skipping email notification.")


if __name__ == '__main__':
    unittest.main(verbosity=2)
