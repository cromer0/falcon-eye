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
        *   Adjust `DATA_GATHERING_INTERVAL_SECONDS` if you need a different interval for historical data collection (default is 60 seconds).

5.  **Database Initialization:**
    *   The application will attempt to initialize the database (create tables) on the first run based on the `DATABASE_TYPE` specified in your `.env` file.
    *   For SQLite, the database file will be created in the `data/` directory (e.g., `data/sys_stats.db`). Ensure the `data/` directory is writable by the application.
    *   For PostgreSQL, ensure the specified database exists and the provided user has permissions to create tables.

6.  **Custom Logo (Optional):**
    *   The application includes a FalconEye logo at `static/images/logo.svg`.
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

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any bugs, feature requests, or improvements.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details (if one is added).
