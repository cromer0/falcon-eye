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


    // --- State Variables ---
    let detailCpuGaugeChart, detailRamGaugeChart, detailDiskGaugeChart;
    let detailHistoricalCpuChart, detailHistoricalRamChart, detailHistoricalDiskChart;
    let historicalChartsInitializedForDetail = false;
    let allServersData = []; // Cache for server list data
    let selectedServerData = null; // Cache for the currently selected server's full data
    let detailViewInterval; // For auto-refreshing detail view

    // --- Config ---
    const SERVER_LIST_REFRESH_INTERVAL = 15000; // ms
    const DETAIL_VIEW_REFRESH_INTERVAL = 3000; // ms for current stats in detail view

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
     function createDetailHistoricalChart(canvas, label, data, color) { // Takes canvas context directly
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
            options: { /* ... (your existing historical chart options, make sure scales use themeOptions) ... */
                responsive: true, maintainAspectRatio: false,
                scales: { x: { ...themeOptions.scales.x, type: 'time', time: { unit: 'hour', tooltipFormat: 'MMM d, HH:mm:ss', displayFormats: { hour: 'HH:mm' }}, title: { display: true, text: 'Time', color: themeOptions.scales.x.ticks.color, font: {size: 12, weight: 'bold'} } },
                          y: { ...themeOptions.scales.y, title: { display: true, text: 'Usage (%)', color: themeOptions.scales.y.ticks.color, font: {size: 12, weight: 'bold'} } } },
                plugins: { ...themeOptions.plugins, legend: { ...themeOptions.plugins.legend, position: 'top', align: 'end' }}
            }
        });
    }
    function destroyDetailHistoricalCharts() {
        if (detailHistoricalCpuChart) detailHistoricalCpuChart.destroy();
        if (detailHistoricalRamChart) detailHistoricalRamChart.destroy();
        if (detailHistoricalDiskChart) detailHistoricalDiskChart.destroy();
        detailHistoricalCpuChart = null; detailHistoricalRamChart = null; detailHistoricalDiskChart = null;
        historicalChartsInitializedForDetail = false;
        // Hide chart containers
        document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'none');
    }


    // --- View Management ---
    function showView(viewId) {
        document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
        document.getElementById(viewId)?.classList.add('active');

        if (viewId === 'serverListView') {
            if (detailViewInterval) clearInterval(detailViewInterval); // Stop detail refresh
            selectedServerData = null; // Clear selection
            destroyDetailHistoricalCharts(); // Clean up detail charts
        } else if (viewId === 'serverDetailView') {
            // Start interval for selected server's current stats if not already running
            if (detailViewInterval) clearInterval(detailViewInterval);
            detailViewInterval = setInterval(updateSelectedServerCurrentData, DETAIL_VIEW_REFRESH_INTERVAL);
        }
    }

    // --- Dark Mode ---
    // ... (Dark mode logic - same as before, but ensure applyThemeToVisuals is adapted) ...
    function applyThemeToVisuals() {
        // This function will now primarily re-theme the *detail view* gauges and charts
        // as the server list doesn't have complex Chart.js elements by default.
        if (selectedServerData && serverDetailView.classList.contains('active')) {
            // Re-create detail gauges
            if(detailCpuGaugeChart) detailCpuGaugeChart.destroy();
            if(detailRamGaugeChart) detailRamGaugeChart.destroy();
            if(detailDiskGaugeChart) detailDiskGaugeChart.destroy();

            detailCpuGaugeChart = createDetailGauge('detailCpuGauge', selectedServerData.cpu_percent || 0, getCssVariable('--gauge-cpu-color'));
            detailRamGaugeChart = createDetailGauge('detailRamGauge', selectedServerData.ram_percent || 0, getCssVariable('--gauge-ram-color'));
            detailDiskGaugeChart = createDetailGauge('detailDiskGauge', selectedServerData.disk_percent || 0, getCssVariable('--gauge-disk-color'));

            // Re-theme historical charts if they exist
            if (historicalChartsInitializedForDetail) {
                const themeOpts = getChartJsThemeOptions();
                [detailHistoricalCpuChart, detailHistoricalRamChart, detailHistoricalDiskChart].forEach(chart => {
                    if (chart) {
                        // Update chart options based on themeOpts
                        chart.options.scales.x.ticks.color = themeOpts.scales.x.ticks.color;
                        // ... (full theme update for x and y scales, legend)
                        chart.update();
                    }
                });
            }
        }
    }
    // ... (rest of dark mode setup and toggle calling applyThemeToVisuals) ...
    const setInitialTheme = () => { /* ... same ... */ };
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
        if (!selectedServerData) return;

        // Option: Re-fetch specific server data for freshness, or use cached `selectedServerData`
        // For this example, we'll use the cached data and assume server list refresh keeps it reasonably fresh.
        // To re-fetch, you'd make an API call here.
        // For now, let's simulate an update by re-using the selectedServerData
        // In a real scenario, you'd fetch `/api/remote_servers_stats?host={selectedServerData.host}`
        // For simplicity, we'll just re-render from the selectedServerData, assuming it's updated by the main list fetch.
        // This is a slight simplification; ideally, you'd fetch fresh data for the selected server.

        // If you want to fetch live data for the single selected server:
        // fetch(`/api/remote_servers_stats?host=${selectedServerData.host}`) // Hypothetical endpoint
        //  .then(response => response.json())
        //  .then(freshData => {
        //      selectedServerData = freshData; // Update cache
        //      renderCurrentDetailData(freshData);
        //  });
        // For now, just render from existing selectedServerData:
        renderCurrentDetailData(selectedServerData);
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


        if (selectedServerData && selectedServerData.is_local) {
            historicalDataMessageEl.textContent = "Loading historical data...";
            historicalDataMessageEl.style.display = 'block';
            fetch('/api/historical_stats') // This endpoint is for the *local* server's DB
                .then(response => response.json())
                .then(data => {
                    if (data.labels && data.labels.length > 0) {
                        historicalDataMessageEl.style.display = 'none';
                        document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'block');

                        detailHistoricalCpuChart = createDetailHistoricalChart(detailHistCpuChartEl, 'CPU Usage', { labels: data.labels, values: data.cpu_data }, getCssVariable('--chart-line-cpu'));
                        detailHistoricalRamChart = createDetailHistoricalChart(detailHistRamChartEl, 'RAM Usage', { labels: data.labels, values: data.ram_data }, getCssVariable('--chart-line-ram'));
                        detailHistoricalDiskChart = createDetailHistoricalChart(detailHistDiskChartEl, 'Disk Usage', { labels: data.labels, values: data.disk_data }, getCssVariable('--chart-line-disk'));
                        historicalChartsInitializedForDetail = true;
                        applyThemeToVisuals(); // Apply theme after creation
                    } else {
                        historicalDataMessageEl.textContent = "No historical data available for this server.";
                    }
                })
                .catch(error => {
                    console.error("Error fetching historical data:", error);
                    historicalDataMessageEl.textContent = "Error loading historical data.";
                });
        } else {
            historicalDataMessageEl.textContent = "Historical data is only available for the local server instance defined in the configuration.";
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

    // --- Initialization ---
    setInitialTheme();
    applyThemeToVisuals(); // Initial call, might not do much if no server selected
    fetchServerList(); // Initial server list load
    showView('serverListView'); // Start with the server list

    setInterval(fetchServerList, SERVER_LIST_REFRESH_INTERVAL);
});
