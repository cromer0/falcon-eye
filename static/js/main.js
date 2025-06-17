document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const darkModeToggle = document.getElementById('darkModeToggle');
    const body = document.body;

    const serverListView = document.getElementById('serverListView');
    const serverDetailView = document.getElementById('serverDetailView');
    const remoteServersTableBody = document.querySelector('#remoteServersTable tbody');
    const serverListLastUpdatedSpan = document.getElementById('serverListLastUpdated');

    const backToServerListButton = document.getElementById('backToServerListButton');
    const selectedServerNameEl = document.getElementById('selectedServerName');

    const detailTabButtons = document.querySelectorAll('.detail-tab-button');
    const detailTabContents = document.querySelectorAll('.detail-tab-content');

    // Detail View Gauge Elements
    const detailCpuValueEl = document.getElementById('detailCpuValue');
    const detailCpuInfoEl = document.getElementById('detailCpuInfo');
    const detailRamValueEl = document.getElementById('detailRamValue');
    const detailRamInfoEl = document.getElementById('detailRamInfo');
    const detailDiskPathEl = document.getElementById('detailDiskPath');
    const detailDiskValueEl = document.getElementById('detailDiskValue');
    const detailDiskInfoEl = document.getElementById('detailDiskInfo');
    const detailLastUpdatedEl = document.getElementById('detailLastUpdated');

    // Detail View Historical Elements
    const historicalDataMessageEl = document.getElementById('historicalDataMessage');
    const detailHistoricalChartContainerEl = document.getElementById('detailHistoricalChartContainer');
    const detailHistCpuChartEl = document.getElementById('detailHistoricalCpuChart').getContext('2d');
    const detailHistRamChartEl = document.getElementById('detailHistoricalRamChart').getContext('2d');
    const detailHistDiskChartEl = document.getElementById('detailHistoricalDiskChart').getContext('2d');

    // Alert Config View Elements
    const alertConfigView = document.getElementById('alertConfigView');
    const alertConfigForm = document.getElementById('alertConfigForm');
    const alertFormTitle = document.getElementById('alertFormTitle');
    const alertIdInput = document.getElementById('alert_id');
    const alertNameInput = document.getElementById('alert_name');
    const serverNameSelect = document.getElementById('server_name');
    const resourceTypeSelect = document.getElementById('resource_type');
    const thresholdInput = document.getElementById('threshold_percentage');
    const timeWindowInput = document.getElementById('time_window_minutes');
    const emailsInput = document.getElementById('emails');
    const saveAlertButton = document.getElementById('saveAlertButton');
    const cancelEditAlertButton = document.getElementById('cancelEditAlertButton');
    const alertsTableBody = document.getElementById('alertsTableBody');
    const userFeedbackDiv = document.getElementById('userFeedback');
    const userFeedbackMessageSpan = document.getElementById('userFeedbackMessage');

    // Navigation Elements
    const navServerList = document.getElementById('navServerList');
    const navAlertConfig = document.getElementById('navAlertConfig');


    // --- State Variables ---
    let detailCpuGaugeChart, detailRamGaugeChart, detailDiskGaugeChart;
    let detailHistoricalCpuChart, detailHistoricalRamChart, detailHistoricalDiskChart;
    let historicalChartsInitializedForDetail = false;
    let allServersData = []; // Cache for server list data
    let selectedServerData = null; // Cache for the currently selected server's full data
    let detailViewInterval; // For auto-refreshing detail view
    let currentHistoricalAllMetrics = { labels: [], cpu: [], ram: [], disk: [] }; // For combined tooltips

    // --- Config ---
    // Use interval from Flask for server list, with a fallback to 15000ms
    const SERVER_LIST_REFRESH_INTERVAL = window.APP_CONFIG && window.APP_CONFIG.serverListRefreshInterval ? window.APP_CONFIG.serverListRefreshInterval : 15000;
    // Use interval from Flask for detail view, with a fallback to 3000ms
    const DETAIL_VIEW_REFRESH_INTERVAL = window.APP_CONFIG && window.APP_CONFIG.detailViewRefreshInterval ? window.APP_CONFIG.detailViewRefreshInterval : 3000;

    // --- Helper ---
    const getCssVariable = (variable) => getComputedStyle(document.documentElement).getPropertyValue(variable).trim();

    // --- Theme & Chart Options (mostly unchanged, adapt for detail view charts/gauges if needed) ---
const getChartJsThemeOptions = () => {
        const isDarkMode = body.classList.contains('dark-mode');
        const gridColor = isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)';
        const textColor = isDarkMode ? '#e0e0e0' : '#333';
        return {
            scales: {
                x: {
                    ticks: { color: textColor, font: { size: 10 } },
                    grid: { color: gridColor, drawBorder: false },
                },
                y: {
                    ticks: { color: textColor, font: { size: 10 } },
                    grid: { color: gridColor, drawBorder: false },
                    beginAtZero: true,
                    max: 100, // For percentage-based charts
                }
            },
            plugins: {
                legend: { labels: { color: textColor, font: { size: 12 } } },
                tooltip: {
                    bodyFont: { size: 12 },
                    titleFont: { size: 14 }
                }
            },
            animation: {
                duration: 300 // smoother transitions for chart updates
            }
        };
    };

    // --- REUSABLE GAUGE/CHART FUNCTIONS (ADAPTED FOR DETAIL VIEW) ---
    function createDetailGauge(canvasId, value, color) {
        const ctx = document.getElementById(canvasId)?.getContext('2d');
        if (!ctx) return null;
        // ... (rest of createGauge logic from previous versions)
        const trackColor = getCssVariable(body.classList.contains('dark-mode') ? '--gauge-track-color' : '--gauge-track-color');
        return new Chart(ctx, {
            type: 'doughnut',
            data: { datasets: [{ data: [value, 100 - value], backgroundColor: [color, trackColor], borderWidth: 0, circumference: 270, rotation: 225 }] },
            options: { responsive: true, maintainAspectRatio: true, cutout: '75%', plugins: { tooltip: { enabled: false }, legend: { display: false } }, animation: { duration: 200 } }
        });
    }
    function updateDetailGauge(chart, value) { // Generic updateGauge
        if (chart && chart.data) {
            chart.data.datasets[0].data[0] = value;
            chart.data.datasets[0].data[1] = 100 - value;
            chart.update('none');
        }
    }
     function createDetailHistoricalChart(canvas, label, data, color, allMetricsData) { // Takes canvas context directly and allMetricsData
        const themeOptions = getChartJsThemeOptions(); // Ensure this is defined
        return new Chart(canvas, {
            type: 'line',
            data: {
                labels: data.labels.map(ts => new Date(ts)),
                datasets: [{
                    label: label, data: data.values, borderColor: color,
                    backgroundColor: color.replace(')', ', 0.15)').replace('rgb', 'rgba'),
                    fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 1.5
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: {
                    x: { ...themeOptions.scales.x, type: 'time', time: { unit: 'hour', tooltipFormat: 'MMM d, HH:mm:ss', displayFormats: { hour: 'HH:mm' } }, title: { display: true, text: 'Time', color: themeOptions.scales.x.ticks.color, font: { size: 12, weight: 'bold' } } },
                    y: { ...themeOptions.scales.y, title: { display: true, text: 'Usage (%)', color: themeOptions.scales.y.ticks.color, font: { size: 12, weight: 'bold' } } }
                },
                plugins: {
                    ...themeOptions.plugins,
                    legend: { ...themeOptions.plugins.legend, position: 'top', align: 'end' },
                    tooltip: {
                        ...themeOptions.plugins.tooltip,
                        mode: 'index', // Show tooltip for all datasets at that index
                        intersect: false, // Tooltip will appear even if not directly hovering over a point
                        callbacks: {
                            title: function(tooltipItems) {
                                // Format the timestamp for the title
                                if (tooltipItems.length > 0) {
                                    const dataIndex = tooltipItems[0].dataIndex;
                                    if (allMetricsData && allMetricsData.labels && allMetricsData.labels[dataIndex]) {
                                        return new Date(allMetricsData.labels[dataIndex]).toLocaleString();
                                    }
                                }
                                return '';
                            },
                            label: function(tooltipItem) {
                                // This callback is called for each dataset.
                                // We will build a custom multi-line tooltip in the footer or by returning an array of strings.
                                // For simplicity, we let title handle the timestamp and will show current metric here,
                                // but the more comprehensive tooltip showing ALL metrics will be managed by a custom external tooltip or by modifying this further.
                                // The default 'label' callback shows the current dataset's value.
                                // To show ALL metrics, we'd need to access allMetricsData here for each dataset.
                                // However, the 'label' callback is for *each dataset line* in the tooltip.
                                // A better approach for a combined tooltip is to use the 'footer' callback or a custom external tooltip.
                                // For this iteration, let's ensure the title is set, and then we'll refine.
                                // The prompt asks for CPU, RAM, Disk in the *same* tooltip.
                                // This requires access to allMetricsData.cpu, .ram, .disk at tooltipItem.dataIndex.

                                // This will be called for each dataset, so we return only the relevant line.
                                // The final combined tooltip will be assembled by Chart.js if mode: 'index' is effective.
                                // Let's try returning an array of lines from the 'label' callback of the *first* dataset
                                // and empty from others. Or, more simply, use afterBody/beforeBody.
                                return `${tooltipItem.dataset.label}: ${tooltipItem.formattedValue}%`;
                            },
                            afterBody: function(tooltipItems) {
                                // This callback allows us to add more lines after the default lines.
                                // We will construct the full CPU, RAM, Disk display here.
                                const lines = [];
                                if (tooltipItems.length > 0) {
                                    const dataIndex = tooltipItems[0].dataIndex;
                                    if (allMetricsData && allMetricsData.labels && allMetricsData.labels[dataIndex]) {
                                        if (allMetricsData.cpu && allMetricsData.cpu[dataIndex] !== undefined) {
                                            lines.push(`CPU: ${allMetricsData.cpu[dataIndex].toFixed(1)}%`);
                                        }
                                        if (allMetricsData.ram && allMetricsData.ram[dataIndex] !== undefined) {
                                            lines.push(`RAM: ${allMetricsData.ram[dataIndex].toFixed(1)}%`);
                                        }
                                        if (allMetricsData.disk && allMetricsData.disk[dataIndex] !== undefined) {
                                            lines.push(`Disk: ${allMetricsData.disk[dataIndex].toFixed(1)}%`);
                                        }
                                    }
                                }
                                return lines;
                            }
                        }
                    }
                }
            }
        });
    }
    function destroyDetailHistoricalCharts() {
        if (detailHistoricalCpuChart) detailHistoricalCpuChart.destroy();
        if (detailHistoricalRamChart) detailHistoricalRamChart.destroy();
        if (detailHistoricalDiskChart) detailHistoricalDiskChart.destroy();
        detailHistoricalCpuChart = null; detailHistoricalRamChart = null; detailHistoricalDiskChart = null;
        historicalChartsInitializedForDetail = false;
        currentHistoricalAllMetrics = { labels: [], cpu: [], ram: [], disk: [] }; // Reset stored data
        // Hide chart containers
        document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'none');
    }


    // --- View Management ---
    function showView(viewId) {
        document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
        const targetView = document.getElementById(viewId);
        if (targetView) {
            targetView.classList.add('active');
        }

        // Update active state for nav items
        document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
        const activeNavItem = document.querySelector(`.nav-item[data-view="${viewId}"]`);
        if (activeNavItem) {
            activeNavItem.classList.add('active');
        }


        if (viewId === 'serverListView' || viewId === 'alertConfigView') {
            if (detailViewInterval) clearInterval(detailViewInterval);
            selectedServerData = null;
            if (viewId !== 'serverDetailView') { // only destroy if not going to detail view
                 destroyDetailHistoricalCharts();
            }
        }

        if (viewId === 'serverDetailView') {
            if (detailViewInterval) clearInterval(detailViewInterval);
            detailViewInterval = setInterval(updateSelectedServerCurrentData, DETAIL_VIEW_REFRESH_INTERVAL);
        } else if (viewId === 'alertConfigView') {
            // Load data for alert config view
            populateAlertServerDropdown();
            fetchAndDisplayAlerts();
            resetAlertForm(); // Ensure form is clean when switching to this view
        }
    }

    // --- User Feedback ---
    function showFeedback(message, type = 'success') { // type can be 'success', 'error', 'warning'
        userFeedbackMessageSpan.textContent = message;
        userFeedbackDiv.className = 'alert alert-dismissible fade show'; // Reset classes
        if (type === 'success') {
            userFeedbackDiv.classList.add('alert-success');
        } else if (type === 'error') {
            userFeedbackDiv.classList.add('alert-danger');
        } else if (type === 'warning') {
            userFeedbackDiv.classList.add('alert-warning');
        } else {
            userFeedbackDiv.classList.add('alert-info');
        }
        userFeedbackDiv.style.display = 'block';
        userFeedbackDiv.classList.add('show'); // Ensure it's visible if previously hidden by 'fade'
    }

    function hideFeedback() {
        userFeedbackDiv.classList.remove('show');
        // Bootstrap's JS would handle the fade out, but if not using its JS:
        userFeedbackDiv.style.display = 'none';
    }
    // Make hideFeedback globally accessible for the button's onclick
    window.hideFeedback = hideFeedback;


    // --- Dark Mode ---
    // ... (Dark mode logic - same as before, but ensure applyThemeToVisuals is adapted) ...
    function applyThemeToVisuals() {
        // This function will now primarily re-theme the *detail view* gauges and charts
        // as the server list doesn't have complex Chart.js elements by default.
        const activeView = document.querySelector('.view.active');
        if (selectedServerData && activeView && activeView.id === 'serverDetailView') {
            // Re-create detail gauges
            if(detailCpuGaugeChart) detailCpuGaugeChart.destroy();
            if(detailRamGaugeChart) detailRamGaugeChart.destroy();
            if(detailDiskGaugeChart) detailDiskGaugeChart.destroy();

            detailCpuGaugeChart = createDetailGauge('detailCpuGauge', selectedServerData.cpu_percent || 0, getCssVariable('--gauge-cpu-color'));
            detailRamGaugeChart = createDetailGauge('detailRamGauge', selectedServerData.ram_percent || 0, getCssVariable('--gauge-ram-color'));
            detailDiskGaugeChart = createDetailGauge('detailDiskGauge', selectedServerData.disk_percent || 0, getCssVariable('--gauge-disk-color'));

            // Re-create historical charts if they exist and data is available
            if (historicalChartsInitializedForDetail && currentHistoricalAllMetrics.labels.length > 0) {
                // Destroy existing charts first
                if (detailHistoricalCpuChart) detailHistoricalCpuChart.destroy();
                if (detailHistoricalRamChart) detailHistoricalRamChart.destroy();
                if (detailHistoricalDiskChart) detailHistoricalDiskChart.destroy();

                // Re-create with current data and new theme options (implicitly picked up by createDetailHistoricalChart)
                detailHistoricalCpuChart = createDetailHistoricalChart(detailHistCpuChartEl, 'CPU Usage', { labels: currentHistoricalAllMetrics.labels, values: currentHistoricalAllMetrics.cpu }, getCssVariable('--chart-line-cpu'), currentHistoricalAllMetrics);
                detailHistoricalRamChart = createDetailHistoricalChart(detailHistRamChartEl, 'RAM Usage', { labels: currentHistoricalAllMetrics.labels, values: currentHistoricalAllMetrics.ram }, getCssVariable('--chart-line-ram'), currentHistoricalAllMetrics);
                detailHistoricalDiskChart = createDetailHistoricalChart(detailHistDiskChartEl, 'Disk Usage', { labels: currentHistoricalAllMetrics.labels, values: currentHistoricalAllMetrics.disk }, getCssVariable('--chart-line-disk'), currentHistoricalAllMetrics);
                // No need to call chart.update() as they are new charts
            }
        }
    }
    // ... (rest of dark mode setup and toggle calling applyThemeToVisuals) ...
    const setInitialTheme = () => {
        const savedTheme = localStorage.getItem("theme");
        if (savedTheme === "light") {
            document.body.classList.remove("dark-mode");
        } else {
            document.body.classList.add("dark-mode");
        }
 };
    darkModeToggle.addEventListener('click', () => {
        body.classList.toggle('dark-mode');
        localStorage.setItem("theme", body.classList.contains("dark-mode") ? "dark" : "light");
        applyThemeToVisuals(); // This will apply to detail view if active
    });


    // --- Server List Logic ---
    function fetchServerList() {
        fetch('/api/remote_servers_stats')
            .then(response => response.json())
            .then(servers => {
                allServersData = servers; // Cache the full data
                remoteServersTableBody.innerHTML = '';
                servers.forEach(server => {
                    const row = remoteServersTableBody.insertRow();
                    row.insertCell().textContent = server.name;
                    row.insertCell().textContent = server.host;

                    const statusSpan = document.createElement('span');
                    statusSpan.textContent = server.status.charAt(0).toUpperCase() + server.status.slice(1);
                    statusSpan.className = `status-${server.status}`;
                    if (server.error_message) statusSpan.title = server.error_message;
                    row.insertCell().appendChild(statusSpan);

                    // Summary progress bars in the list view
                    const cpuCell = row.insertCell();
                    const ramCell = row.insertCell();
                    const diskCell = row.insertCell();

                    if (server.status === 'online') {
                        cpuCell.appendChild(createProgressBar(server.cpu_percent, 'CPU')); // createProgressBar needs to be defined
                        ramCell.appendChild(createProgressBar(server.ram_percent, 'RAM'));
                        diskCell.appendChild(createProgressBar(server.disk_percent, 'Disk'));
                    } else {
                        cpuCell.textContent = 'N/A'; ramCell.textContent = 'N/A'; diskCell.textContent = 'N/A';
                    }

                    row.dataset.serverHost = server.host; // Store unique ID for click handling
                    row.dataset.serverName = server.name; // Store name for click handling
                    row.addEventListener('click', () => handleServerSelect(server.host));
                });
                if (serverListLastUpdatedSpan) serverListLastUpdatedSpan.textContent = new Date().toLocaleTimeString();
            })
            .catch(error => {
                console.error('Error fetching server list:', error);
                if (remoteServersTableBody) remoteServersTableBody.innerHTML = `<tr><td colspan="6">Error loading server data.</td></tr>`;
            });
    }
    // ProgressBar for list view (same as detail view's createProgressBar)
    const createProgressBar = (percentage, type) => { /* ... same logic as createDetailProgressBar from previous answers ... */
        const container = document.createElement('div');
        container.className = 'progress-bar-container';
        container.title = `${type.toUpperCase()}: ${percentage.toFixed(1)}%`;
        const bar = document.createElement('div');
        bar.className = 'progress-bar';
        let barColor;
        if (percentage <= 70) barColor = getCssVariable('--progress-bar-green');
        else if (percentage <= 90) barColor = getCssVariable('--progress-bar-yellow');
        else barColor = getCssVariable('--progress-bar-red');
        bar.style.backgroundColor = barColor;
        bar.style.width = `${Math.max(0, Math.min(100, percentage)).toFixed(1)}%`;
        const textOverlay = document.createElement('div');
        textOverlay.className = 'progress-bar-text';
        textOverlay.textContent = `${percentage.toFixed(1)}%`;
        container.appendChild(bar); container.appendChild(textOverlay);
        return container;
    };


    // --- Server Detail Logic ---
    function handleServerSelect(serverHost) {
        selectedServerData = allServersData.find(s => s.host === serverHost);
        if (!selectedServerData) {
            console.error("Selected server data not found in cache:", serverHost);
            return;
        }

        selectedServerNameEl.textContent = `${selectedServerData.name} (${selectedServerData.host})`;

        // Populate current data initially
        updateSelectedServerCurrentData(); // This will also create gauges if first time

        // Set up detail tabs (default to 'current')
        detailTabButtons.forEach(btn => btn.classList.remove('active'));
        detailTabContents.forEach(content => content.classList.remove('active'));
        document.querySelector('.detail-tab-button[data-tab="detail-current"]').classList.add('active');
        document.getElementById('detail-current').classList.add('active');

        // Handle historical data tab based on 'is_local'
        handleHistoricalTabForSelectedServer();

        showView('serverDetailView');
    }

    function updateSelectedServerCurrentData() {
        if (!selectedServerData || !selectedServerData.host) {
            console.error("No server selected or host missing, cannot update detail view.");
            // Optionally, clear the interval if this state is reached unexpectedly
            // if (detailViewInterval) clearInterval(detailViewInterval);
            return;
        }

        fetch(`/api/remote_servers_stats?host=${selectedServerData.host}`)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(freshDataResponse => {
                let freshData;
                // The backend currently returns an array. If a single host is requested,
                // it returns an array with one item, or an empty array if not found.
                if (Array.isArray(freshDataResponse) && freshDataResponse.length > 0) {
                    freshData = freshDataResponse[0];
                } else if (Array.isArray(freshDataResponse) && freshDataResponse.length === 0) {
                     console.error("Received empty array for host (server might have been removed or host is incorrect):", selectedServerData.host);
                    // Keep selectedServerData as is, but update timestamp and show an error.
                    // Or, transition to an error state more explicitly.
                    if (detailLastUpdatedEl) {
                        detailLastUpdatedEl.textContent = `Error (not found): ${new Date().toLocaleTimeString()}`;
                    }
                    // Potentially update status in UI if server is "offline" or "error"
                    // renderCurrentDetailData(selectedServerData); // Re-render to show its current (possibly error) state
                    return; // Stop further processing for this update cycle
                }
                else {
                    // This case handles if the API changes to return a single object directly
                    // or if the response is not an array as expected (e.g. an error object from proxy)
                    if (typeof freshDataResponse === 'object' && freshDataResponse !== null && !Array.isArray(freshDataResponse) && freshDataResponse.host === selectedServerData.host) {
                        freshData = freshDataResponse;
                    } else {
                        console.error("Received unexpected data format for host:", selectedServerData.host, freshDataResponse);
                        if (detailLastUpdatedEl) {
                            detailLastUpdatedEl.textContent = `Error (invalid data): ${new Date().toLocaleTimeString()}`;
                        }
                        // renderCurrentDetailData(selectedServerData); // Re-render to show its current (possibly error) state
                        return; // Stop further processing
                    }
                }

                selectedServerData = freshData; // Update the cached data
                renderCurrentDetailData(selectedServerData);
            })
            .catch(error => {
                console.error('Error fetching details for server:', selectedServerData.host, error);
                if (detailLastUpdatedEl) {
                    detailLastUpdatedEl.textContent = `Error updating: ${new Date().toLocaleTimeString()}`;
                }
                // Optionally, update status in UI to reflect the error
                // selectedServerData.status = 'error';
                // selectedServerData.error_message = `Failed to refresh: ${error.message}`;
                // renderCurrentDetailData(selectedServerData); // Re-render to show its error state
            });
    }

    function renderCurrentDetailData(data) {
        detailCpuValueEl.textContent = `${data.cpu_percent.toFixed(1)}%`;
        detailCpuInfoEl.textContent = `Cores: ${data.cpu_cores}, Model: ${data.cpu_model.substring(0,30)}${data.cpu_model.length > 30 ? '...' : ''}`;
        detailRamValueEl.textContent = `${data.ram_percent.toFixed(1)}%`;
        detailRamInfoEl.textContent = `${data.ram_used_gb.toFixed(1)} GB / ${data.ram_total_gb.toFixed(1)} GB`;
        detailDiskPathEl.textContent = data.disk_path || '/';
        detailDiskValueEl.textContent = `${data.disk_percent.toFixed(1)}%`;
        detailDiskInfoEl.textContent = `${data.disk_used_gb.toFixed(1)} GB / ${data.disk_total_gb.toFixed(1)} GB`;
        detailLastUpdatedEl.textContent = new Date().toLocaleTimeString(); // Current time of render

        // Create or update gauges
        if (!detailCpuGaugeChart) detailCpuGaugeChart = createDetailGauge('detailCpuGauge', data.cpu_percent, getCssVariable('--gauge-cpu-color'));
        else updateDetailGauge(detailCpuGaugeChart, data.cpu_percent);

        if (!detailRamGaugeChart) detailRamGaugeChart = createDetailGauge('detailRamGauge', data.ram_percent, getCssVariable('--gauge-ram-color'));
        else updateDetailGauge(detailRamGaugeChart, data.ram_percent);

        if (!detailDiskGaugeChart) detailDiskGaugeChart = createDetailGauge('detailDiskGauge', data.disk_percent, getCssVariable('--gauge-disk-color'));
        else updateDetailGauge(detailDiskGaugeChart, data.disk_percent);
    }


    function handleHistoricalTabForSelectedServer() {
        destroyDetailHistoricalCharts(); // Clear previous
        document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'none');


        if (selectedServerData) {
            historicalDataMessageEl.textContent = "Loading historical data...";
            historicalDataMessageEl.style.display = 'block';
            const serverName = selectedServerData.name;
            const url = `/api/historical_stats?server_name=${encodeURIComponent(serverName)}`;
            fetch(url)
                .then(response => response.json())
                .then(data => {
                    if (data.labels && data.labels.length > 0 && data.cpu_data && data.ram_data && data.disk_data) {
                        historicalDataMessageEl.style.display = 'none';
                        document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'block');

                        // Store all data for combined tooltips
                        currentHistoricalAllMetrics.labels = data.labels;
                        currentHistoricalAllMetrics.cpu = data.cpu_data;
                        currentHistoricalAllMetrics.ram = data.ram_data;
                        currentHistoricalAllMetrics.disk = data.disk_data;

                        detailHistoricalCpuChart = createDetailHistoricalChart(detailHistCpuChartEl, 'CPU Usage', { labels: data.labels, values: data.cpu_data }, getCssVariable('--chart-line-cpu'), currentHistoricalAllMetrics);
                        detailHistoricalRamChart = createDetailHistoricalChart(detailHistRamChartEl, 'RAM Usage', { labels: data.labels, values: data.ram_data }, getCssVariable('--chart-line-ram'), currentHistoricalAllMetrics);
                        detailHistoricalDiskChart = createDetailHistoricalChart(detailHistDiskChartEl, 'Disk Usage', { labels: data.labels, values: data.disk_data }, getCssVariable('--chart-line-disk'), currentHistoricalAllMetrics);
                        historicalChartsInitializedForDetail = true;
                        applyThemeToVisuals(); // Apply theme after creation
                    } else {
                        historicalDataMessageEl.textContent = "No historical data available for this server.";
                    }
                })
                .catch(error => {
                    console.error(`Error fetching historical data for ${serverName}:`, error);
                    historicalDataMessageEl.textContent = "Error loading historical data.";
                });
        } else {
            historicalDataMessageEl.textContent = "Historical data is only available for the local server instance defined in the configuration."; // This message might need adjustment if historical data can be fetched for remotes.
            historicalDataMessageEl.style.display = 'block';
        }
    }

    // Detail View Tab Switcher
    detailTabButtons.forEach(button => {
        button.addEventListener('click', () => {
            detailTabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            const tabId = button.getAttribute('data-tab');
            detailTabContents.forEach(content => content.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');

            if (tabId === 'detail-historical') {
                handleHistoricalTabForSelectedServer(); // Re-check/re-load if historical tab is clicked
            }
        });
    });

    backToServerListButton.addEventListener('click', () => showView('serverListView'));

    // --- Alert Configuration Logic ---
    function populateAlertServerDropdown() {
        fetch('/api/collector_status')
            .then(response => response.json())
            .then(data => {
                const currentVal = serverNameSelect.value;
                serverNameSelect.innerHTML = '<option value="" disabled>Select Server</option>'; // Clear existing
                serverNameSelect.add(new Option('All Servers (*)', '*'));
                serverNameSelect.add(new Option('Local Server (local)', 'local'));

                if (data.configured_server_names && Array.isArray(data.configured_server_names)) {
                    data.configured_server_names.forEach(name => {
                        // Avoid adding 'local' again if it's in configured_server_names
                        if (name.toLowerCase() !== 'local') {
                           serverNameSelect.add(new Option(name, name));
                        }
                    });
                }
                // Try to restore previous selection if still valid
                if (Array.from(serverNameSelect.options).some(opt => opt.value === currentVal)) {
                    serverNameSelect.value = currentVal;
                } else if (!currentVal && serverNameSelect.options.length > 0) {
                     // If no previous value, and we have options, default to the first non-disabled one.
                    const firstEnabledOption = Array.from(serverNameSelect.options).find(opt => !opt.disabled);
                    if (firstEnabledOption) serverNameSelect.value = firstEnabledOption.value;
                    else serverNameSelect.value = ""; // Fallback if all are disabled (should not happen)
                } else {
                    serverNameSelect.value = ""; // Default to "Select Server"
                }
            })
            .catch(error => {
                console.error('Error fetching server names for alert dropdown:', error);
                showFeedback('Could not load server list for dropdown.', 'error');
                serverNameSelect.innerHTML = '<option value="" disabled selected>Error loading servers</option>';
            });
    }

    function fetchAndDisplayAlerts() {
        fetch('/api/alerts')
            .then(response => response.json())
            .then(alerts => {
                alertsTableBody.innerHTML = ''; // Clear existing rows
                if (alerts.length === 0) {
                    alertsTableBody.innerHTML = '<tr><td colspan="10" style="text-align:center;">No alerts configured yet.</td></tr>';
                    return;
                }
                alerts.forEach(alert => {
                    const row = alertsTableBody.insertRow();
                    row.insertCell().textContent = alert.alert_name;
                    row.insertCell().textContent = alert.server_name;
                    row.insertCell().textContent = alert.resource_type.toUpperCase();
                    row.insertCell().textContent = alert.threshold_percentage;
                    row.insertCell().textContent = alert.time_window_minutes;
                    row.insertCell().textContent = alert.emails;

                    const statusCell = row.insertCell();
                    const statusBadge = document.createElement('span');
                    statusBadge.textContent = alert.is_enabled ? 'Enabled' : 'Disabled';
                    statusBadge.className = alert.is_enabled ? 'status-enabled' : 'status-disabled';
                    statusCell.appendChild(statusBadge);

                    row.insertCell().textContent = alert.last_triggered_at ? new Date(alert.last_triggered_at).toLocaleString() : 'Never';
                    row.insertCell().textContent = new Date(alert.created_at).toLocaleString();

                    const actionsCell = row.insertCell();
                    actionsCell.classList.add('actions-cell');
                    const editButton = document.createElement('button');
                    editButton.textContent = 'Edit';
                    editButton.className = 'button-secondary button-small';
                    editButton.dataset.alertId = alert.id;
                    editButton.addEventListener('click', handleEditAlert);
                    actionsCell.appendChild(editButton);

                    const deleteButton = document.createElement('button');
                    deleteButton.textContent = 'Delete';
                    deleteButton.className = 'button-danger button-small';
                    deleteButton.dataset.alertId = alert.id;
                    deleteButton.addEventListener('click', handleDeleteAlert);
                    actionsCell.appendChild(deleteButton);

                    const toggleEnableButton = document.createElement('button');
                    toggleEnableButton.textContent = alert.is_enabled ? 'Disable' : 'Enable';
                    toggleEnableButton.className = `button-small ${alert.is_enabled ? 'button-warning' : 'button-success'}`;
                    toggleEnableButton.dataset.alertId = alert.id;
                    toggleEnableButton.dataset.isEnabled = alert.is_enabled;
                    toggleEnableButton.addEventListener('click', handleToggleEnableAlert);
                    actionsCell.appendChild(toggleEnableButton);
                });
            })
            .catch(error => {
                console.error('Error fetching alerts:', error);
                alertsTableBody.innerHTML = '<tr><td colspan="10" style="text-align:center;">Error loading alerts.</td></tr>';
                showFeedback('Failed to load alerts.', 'error');
            });
    }

    function resetAlertForm() {
        alertConfigForm.reset();
        alertIdInput.value = '';
        alertFormTitle.textContent = 'Create New Alert';
        saveAlertButton.textContent = 'Save Alert';
        cancelEditAlertButton.style.display = 'none';
        // Reset any validation states if implemented
    }

    alertConfigForm.addEventListener('submit', function(event) {
        event.preventDefault();
        const alertId = alertIdInput.value;
        const formData = {
            alert_name: alertNameInput.value,
            server_name: serverNameSelect.value,
            resource_type: resourceTypeSelect.value,
            threshold_percentage: parseFloat(thresholdInput.value),
            time_window_minutes: parseInt(timeWindowInput.value),
            emails: emailsInput.value,
            // is_enabled is not part of the form by default, handled by enable/disable buttons
        };

        // Basic Frontend Validation (can be more extensive)
        if (!formData.alert_name || !formData.server_name || !formData.resource_type || isNaN(formData.threshold_percentage) || isNaN(formData.time_window_minutes) || !formData.emails) {
            showFeedback('Please fill in all required fields with valid values.', 'error');
            return;
        }
        if (formData.threshold_percentage < 0 || formData.threshold_percentage > 100) {
             showFeedback('Threshold percentage must be between 0 and 100.', 'error');
            return;
        }
        if (formData.time_window_minutes <=0) {
             showFeedback('Time window must be a positive integer.', 'error');
            return;
        }
        // Simple email validation (can be improved)
        const emailList = formData.emails.split(',').map(e => e.trim());
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        for (const email of emailList) {
            if (!emailRegex.test(email)) {
                showFeedback(`Invalid email format: ${email}`, 'error');
                return;
            }
        }


        const method = alertId ? 'PUT' : 'POST';
        const url = alertId ? `/api/alerts/${alertId}` : '/api/alerts';

        fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        })
        .then(response => response.json().then(data => ({ ok: response.ok, status: response.status, body: data })))
        .then(res => {
            if (res.ok) {
                showFeedback(`Alert ${alertId ? 'updated' : 'created'} successfully.`, 'success');
                resetAlertForm();
                fetchAndDisplayAlerts();
            } else {
                throw new Error(res.body.error || `Failed to ${alertId ? 'update' : 'create'} alert. Status: ${res.status}`);
            }
        })
        .catch(error => {
            console.error(`Error ${alertId ? 'updating' : 'creating'} alert:`, error);
            showFeedback(error.message || `An error occurred.`, 'error');
        });
    });

    function handleEditAlert(event) {
        const alertId = event.target.dataset.alertId;
        fetch(`/api/alerts/${alertId}`)
            .then(response => response.json())
            .then(alert => {
                if (alert.error) {
                    throw new Error(alert.error);
                }
                alertIdInput.value = alert.id;
                alertNameInput.value = alert.alert_name;
                serverNameSelect.value = alert.server_name;
                resourceTypeSelect.value = alert.resource_type;
                thresholdInput.value = alert.threshold_percentage;
                timeWindowInput.value = alert.time_window_minutes;
                emailsInput.value = alert.emails;
                // is_enabled is not directly set in the form, but could be if there was a checkbox

                alertFormTitle.textContent = 'Edit Alert';
                saveAlertButton.textContent = 'Update Alert';
                cancelEditAlertButton.style.display = 'inline-block';
                window.scrollTo({ top: alertConfigForm.offsetTop - 20, behavior: 'smooth' }); // Scroll to form
            })
            .catch(error => {
                console.error('Error fetching alert for edit:', error);
                showFeedback(`Error fetching alert details: ${error.message}`, 'error');
            });
    }

    cancelEditAlertButton.addEventListener('click', resetAlertForm);

    function handleDeleteAlert(event) {
        const alertId = event.target.dataset.alertId;
        if (confirm('Are you sure you want to delete this alert?')) {
            fetch(`/api/alerts/${alertId}`, { method: 'DELETE' })
            .then(response => response.json().then(data => ({ ok: response.ok, status: response.status, body: data })))
            .then(res => {
                if (res.ok) {
                    showFeedback('Alert deleted successfully.', 'success');
                    fetchAndDisplayAlerts();
                } else {
                     throw new Error(res.body.error || `Failed to delete alert. Status: ${res.status}`);
                }
            })
            .catch(error => {
                console.error('Error deleting alert:', error);
                showFeedback(`Error deleting alert: ${error.message}`, 'error');
            });
        }
    }

    function handleToggleEnableAlert(event) {
        const alertId = event.target.dataset.alertId;
        const isEnabled = event.target.dataset.isEnabled === 'true';
        const action = isEnabled ? 'disable' : 'enable';

        fetch(`/api/alerts/${alertId}/${action}`, { method: 'POST' })
        .then(response => response.json().then(data => ({ ok: response.ok, status: response.status, body: data })))
        .then(res => {
            if (res.ok) {
                showFeedback(`Alert ${action}d successfully.`, 'success');
                fetchAndDisplayAlerts(); // Refresh the table to show new status
            } else {
                throw new Error(res.body.error || `Failed to ${action} alert. Status: ${res.status}`);
            }
        })
        .catch(error => {
            console.error(`Error ${action}ing alert:`, error);
            showFeedback(`Error ${action}ing alert: ${error.message}`, 'error');
        });
    }


    // --- Initialization ---
    setInitialTheme();
    applyThemeToVisuals(); // Initial call, might not do much if no server selected

    // Navigation event listeners
    navServerList.addEventListener('click', (e) => { e.preventDefault(); showView('serverListView'); });
    navAlertConfig.addEventListener('click', (e) => { e.preventDefault(); showView('alertConfigView'); });

    fetchServerList(); // Initial server list load for the default view
    showView('serverListView'); // Start with the server list view by default

    setInterval(fetchServerList, SERVER_LIST_REFRESH_INTERVAL);
});
