# FalconEye Server Monitor

FalconEye is a web application to monitor server performance metrics for local and remote machines.

## Features

*   Real-time monitoring of CPU, RAM, and Disk usage.
*   Historical data charts for performance analysis.
*   Support for monitoring multiple remote Ubuntu servers via SSH (with direct or jump server connections).
*   Configurable data collection intervals.
*   Light and Dark mode themes.
*   User authentication for accessing the application.
*   Database support for SQLite (default) and PostgreSQL.
*   **Configurable Alerting System:** Monitor server resources (CPU, RAM, Disk) and receive email notifications based on user-defined rules, thresholds, and time windows.

## Tech Stack

*   **Backend**: Python (Flask)
*   **Frontend**: HTML, CSS, JavaScript
*   **Libraries**:
    *   `psutil`: For fetching local system metrics.
    *   `paramiko`: For SSH connections to remote servers.
    *   `Chart.js`: For rendering charts.
    *   `dotenv`: For managing environment variables.

## Setup and Configuration

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <repository-name>
    ```
    *(Replace `<repository-url>` and `<repository-name>` with the actual values once available.)*

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

3.  **Install dependencies:**
    *   Ensure you have Python 3.8+ installed.
    *   Install the required packages:
        ```bash
        pip install -r requirements.txt
        ```

4.  **Set up environment variables:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   Open the `.env` file in a text editor and customize the settings. Pay special attention to the following:
        *   `APP_USERNAME`: Set the username for application login (defaults to "admin").
        *   `APP_PASSWORD`: Set the password for application login (defaults to "password").
        *   `FLASK_SECRET_KEY`: **Important!** Change this to a long, random, and secret string for session security. You can generate one using Python:
            ```python
            import secrets
            print(secrets.token_hex(24))
            ```
        *   `DATABASE_TYPE`: Choose between `sqlite` (default) or `postgresql`.
        *   If using `postgresql`, configure `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DBNAME`.
        *   Configure your `REMOTE_SERVER_*` variables for each server you want to monitor. Refer to `.env.example` for detailed examples, including jump server configuration.

        **Alerting and Data Collection Variables:**
        *   `DATA_GATHERING_INTERVAL_SECONDS`: Interval in seconds for historical data collection (default: 60). This also affects how frequently alert conditions are evaluated against new data.
        *   `SMTP_HOST`: Hostname of your SMTP server for sending email alerts.
        *   `SMTP_PORT`: Port number of your SMTP server (default: 587 for TLS, 465 for SSL).
        *   `SMTP_USER`: Username for SMTP authentication (leave blank if not required).
        *   `SMTP_PASSWORD`: Password for SMTP authentication (leave blank if not required).
        *   `SMTP_USE_TLS`: Set to `true` to use STARTTLS (recommended for port 587, default: `true`).
        *   `SMTP_USE_SSL`: Set to `true` to connect using SMTP_SSL directly (typically for port 465, default: `false`).
        *   `EMAIL_FROM_ADDRESS`: The email address from which alert notifications will be sent (e.g., `falconeye-alerts@yourdomain.com`).
        *   `ALERT_COOLDOWN_MINUTES`: Minimum time in minutes before another alert notification is sent for the same rule if the condition persists (default: 30).
        *   `MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE`: The minimum percentage of expected data points that must be present within an alert's time window for it to be evaluated (default: 0.8, i.e., 80%). This helps prevent false positives if data collection was temporarily gappy.


5.  **Database Initialization:**
    *   The application will attempt to initialize the database (create tables, including the `alerts` table) on the first run based on the `DATABASE_TYPE` specified in your `.env` file.
    *   For SQLite, the database file will be created in the `data/` directory (e.g., `data/sys_stats.db`). Ensure the `data/` directory is writable by the application.
    *   For PostgreSQL, ensure the specified database exists and the provided user has permissions to create tables.

6.  **Custom Logo (Optional):**
    *   The application includes a FalconEye logo at `static/images/logo.png`.
    *   To use your own logo, replace this file with your desired image (e.g., an SVG or PNG file).
    *   If you use a different filename or path, update the references in `templates/index.html` and `templates/login.html`.

## Running the Application

1.  **Ensure your environment variables are set** in the `.env` file.
2.  **Start the Flask development server:**
    ```bash
    python app.py
    ```
3.  Open your web browser and navigate to `http://localhost:5000` (or the host/port configured if you changed it).

## Development Notes

*   **SSH Key Paths**: When using SSH keys (`REMOTE_SERVER_X_KEY_PATH`), ensure the path is correct from the perspective of the machine running the Flask application. Use absolute paths or paths relative to the application's root directory if necessary. `~` for home directory expansion is supported.
*   **Jump Servers**: If a target server is behind a jump server, ensure the jump server configuration (`REMOTE_SERVER_X_JUMP_SERVER` pointing to another server's index) and its own authentication details are correctly set up.
*   **Firewalls**: Ensure that any firewalls (on the machine running the app, on the remote servers, or network firewalls) allow SSH connections on the specified ports.

## Alerting System

### Overview

FalconEye includes a built-in alerting system designed to notify you when server resources cross predefined thresholds. You can configure alerts to monitor CPU, RAM, or Disk utilization on specific servers or all monitored servers ('*'). When an alert condition is met for a specified duration (time window), an email notification is sent to the configured recipients.

Key configurable aspects of an alert include:
*   **Alert Name:** A descriptive name for the alert.
*   **Server Name:** Target a specific server, 'local', or 'All Servers (*)' to apply the rule broadly.
*   **Resource Type:** Choose between CPU, RAM, or Disk.
*   **Threshold Percentage:** The utilization percentage that, if exceeded, contributes to triggering the alert.
*   **Time Window (minutes):** The duration for which the resource utilization must consistently stay above the threshold for the alert to trigger.
*   **Notification Emails:** A comma-separated list of email addresses to receive notifications.

### Configuration via UI

Alerts are managed entirely through the "Alert Configuration" section within the FalconEye web interface.

*   **Creating Alerts:** Fill out the "Create New Alert" form, specifying all the parameters listed above.
*   **Viewing Alerts:** A table displays all configured alerts, showing their name, server, resource, threshold, time window, email recipients, current status (Enabled/Disabled), and when they were last triggered.
*   **Managing Alerts:** Each alert in the table has action buttons to:
    *   **Edit:** Modify the alert's parameters.
    *   **Delete:** Remove the alert.
    *   **Enable/Disable:** Toggle the alert's active status. Disabled alerts will not be evaluated.

### How it Works (Briefly)

1.  **Data Collection:** The system collects performance metrics for all monitored servers at the interval defined by `DATA_GATHERING_INTERVAL_SECONDS`.
2.  **Rule Evaluation:** After each data collection cycle, the system evaluates all "Enabled" alert rules.
3.  **Time Window Logic:** For an alert to trigger, the monitored resource (e.g., CPU utilization) must remain *above* the configured threshold percentage for *all* data points recorded within the specified time window. For example, if the time window is 5 minutes and data is collected every 60 seconds, all 5 of the most recent data points for that server and resource must be above the threshold. The `MINIMUM_DATA_POINTS_FOR_ALERT_PERCENTAGE` setting allows for some flexibility if a data point is missed.
4.  **Email Notification:** If an alert condition is met, an email is sent to the specified recipients.
5.  **Cooldown Period:** Once an alert triggers and sends an email, it enters a cooldown period defined by `ALERT_COOLDOWN_MINUTES`. This prevents a flood of emails if a resource remains consistently high. No further emails for that specific alert rule will be sent until the cooldown period has elapsed, even if the condition remains true. The `last_triggered_at` timestamp for the alert is updated upon triggering.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any bugs, feature requests, or improvements.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details (if one is added).

## Code Style and Linting

This project uses specific tools to maintain code quality and consistency.

### Python Formatting

We use **Black** for uncompromising Python code formatting.

To format your Python code, run:
```bash
pip install black  # If not already installed via requirements.txt
black .
```
The CI workflow will check if the code is formatted with Black.

### Python Linting

We use **Flake8** for linting Python code to catch common errors and style issues.

To lint your Python code, run:
```bash
pip install flake8  # If not already installed via requirements.txt
flake8 .
```
The CI workflow will run Flake8.

### HTML Linting

We use **HTMLHint** to lint HTML files.

To lint your HTML files:
1. Install HTMLHint globally (requires Node.js and npm):
   ```bash
   npm install -g htmlhint
   ```
2. Create a `.htmlhintrc` file in the project root with your desired rules. A basic example:
   ```json
   {
     "tagname-lowercase": true,
     "attr-lowercase": true,
     "attr-value-double-quotes": true,
     "doctype-first": true,
     "tag-pair": true,
     "spec-char-escape": true,
     "id-unique": true,
     "src-not-empty": true,
     "attr-no-duplication": true,
     "title-require": true
   }
   ```
3. Run HTMLHint:
   ```bash
   htmlhint "templates/**/*.html" "static/**/*.html"
   ```
The CI workflow will also run HTMLHint.

### JavaScript Linting

We use **ESLint** to lint JavaScript files.

To lint your JavaScript files:
1. Install ESLint globally (requires Node.js and npm):
   ```bash
   npm install -g eslint
   ```
2. Create an ESLint configuration file (e.g., `eslint.config.js`) in the project root. A basic example configuration is provided in the repository (`eslint.config.js`).
3. Run ESLint:
   ```bash
   eslint "static/js/**/*.js"
   ```
   You might need to adjust the command based on your file structure and if you have JS files elsewhere.
The CI workflow will also run ESLint.

### HTML & JavaScript Formatting

We use **Prettier** for formatting HTML and JavaScript files to ensure a consistent style.

To install Prettier (if not already installed via npm in the project, or globally for local use):
```bash
npm install -g prettier
```

To format your HTML and JavaScript files:
```bash
prettier --write "static/js/**/*.js" "templates/**/*.html" "static/**/*.html"
```

To check if files are formatted according to Prettier's style (useful for CI):
```bash
prettier --check "static/js/**/*.js" "templates/**/*.html" "static/**/*.html"
```
The CI workflow will use Prettier to check formatting and will also attempt to auto-format and commit changes.

---

Ensure you have Node.js and npm installed to use HTMLHint and ESLint.
