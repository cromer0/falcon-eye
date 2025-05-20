document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const darkModeToggle = document.getElementById('darkModeToggle');
    const body = document.body;
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

    const cpuValueEl = document.getElementById('cpuValue');
    const ramValueEl = document.getElementById('ramValue');
    const ramDetailEl = document.getElementById('ramDetail');
    const diskValueEl = document.getElementById('diskValue');
    const diskDetailEl = document.getElementById('diskDetail');
    const lastUpdatedEl = document.getElementById('lastUpdated');

    const remoteServersTableBody = document.querySelector('#remoteServersTable tbody');
    const remoteLastUpdatedSpan = document.getElementById('remoteLastUpdated');

    // --- State Variables ---
    let cpuGaugeChart, ramGaugeChart, diskGaugeChart;
    let historicalCpuChart, historicalRamChart, historicalDiskChart;
    let historicalChartsInitialized = false;

    // --- Configuration ---
    const CURRENT_STATS_REFRESH_INTERVAL = 2000; // ms
    const OTHER_TABS_REFRESH_INTERVAL = 5000; // ms for historical/remote if active

    // --- Theme Utility ---
    const getCssVariable = (variable) => getComputedStyle(document.documentElement).getPropertyValue(variable).trim();

    // --- Chart.js Common Theme Options ---
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

    // --- Gauge Creation & Update ---
    function createGauge(canvasId, value, color) {
        const ctx = document.getElementById(canvasId).getContext('2d');
        const trackColor = getCssVariable(body.classList.contains('dark-mode') ? '--gauge-track-color' : '--gauge-track-color'); // Re-fetch based on current mode

        return new Chart(ctx, {
            type: 'doughnut',
            data: {
                datasets: [{
                    data: [value, 100 - value],
                    backgroundColor: [color, trackColor],
                    borderWidth: 0,
                    circumference: 270,
                    rotation: 225
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                cutout: '75%',
                plugins: {
                    tooltip: { enabled: false },
                    legend: { display: false }
                },
                animation: {
                    duration: 200 // Slightly faster for gauges
                }
            }
        });
    }

    function updateGauge(chart, value) {
        if (chart && chart.data) {
            chart.data.datasets[0].data[0] = value;
            chart.data.datasets[0].data[1] = 100 - value;
            chart.update('none'); // Use 'none' for instant update without animation if preferred during polling
        }
    }

    // --- Current Data Fetch & Display ---
    function fetchCurrentData() {
        fetch('/api/current_stats')
            .then(response => response.json())
            .then(data => {
                if (cpuValueEl) cpuValueEl.textContent = `${data.cpu_percent.toFixed(1)}%`;
                if (ramValueEl) ramValueEl.textContent = `${data.ram_percent.toFixed(1)}%`;
                if (ramDetailEl) ramDetailEl.textContent = `${data.ram_used_gb} GB / ${data.ram_total_gb} GB`;
                if (diskValueEl) diskValueEl.textContent = `${data.disk_percent.toFixed(1)}%`;
                if (diskDetailEl) diskDetailEl.textContent = `${data.disk_used_gb} GB / ${data.disk_total_gb} GB`;

                updateGauge(cpuGaugeChart, data.cpu_percent);
                updateGauge(ramGaugeChart, data.ram_percent);
                updateGauge(diskGaugeChart, data.disk_percent);

                if (lastUpdatedEl) lastUpdatedEl.textContent = new Date(data.timestamp).toLocaleTimeString();
            })
            .catch(error => console.error('Error fetching current stats:', error));
    }

    // --- Historical Data Chart Creation & Update ---
    function createHistoricalChart(canvasId, label, data, color) {
        const ctx = document.getElementById(canvasId).getContext('2d');
        const themeOptions = getChartJsThemeOptions();
        return new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels.map(ts => new Date(ts)),
                datasets: [{
                    label: label,
                    data: data.values,
                    borderColor: color,
                    backgroundColor: color.replace(')', ', 0.15)').replace('rgb', 'rgba'), // Slightly more subtle fill
                    fill: true,
                    tension: 0.4, // Smoother lines
                    pointRadius: 0, // No points by default
                    pointHoverRadius: 5,
                    borderWidth: 1.5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false, // Important for sizing within container
                scales: {
                    x: {
                        ...themeOptions.scales.x,
                        type: 'time',
                        time: {
                            unit: 'hour',
                            tooltipFormat: 'MMM d, HH:mm:ss',
                            displayFormats: { hour: 'HH:mm' }
                        },
                        title: { display: true, text: 'Time', color: themeOptions.scales.x.ticks.color, font: {size: 12, weight: 'bold'} }
                    },
                    y: {
                        ...themeOptions.scales.y,
                        title: { display: true, text: 'Usage (%)', color: themeOptions.scales.y.ticks.color, font: {size: 12, weight: 'bold'} }
                    }
                },
                plugins: {
                    ...themeOptions.plugins,
                    legend: {
                        ...themeOptions.plugins.legend,
                        position: 'top',
                        align: 'end'
                    }
                }
            }
        });
    }

    function updateHistoricalChartsTheme() {
        const themeOptions = getChartJsThemeOptions();
        [historicalCpuChart, historicalRamChart, historicalDiskChart].forEach(chart => {
            if (chart) {
                chart.options.scales.x.ticks.color = themeOptions.scales.x.ticks.color;
                chart.options.scales.x.grid.color = themeOptions.scales.x.grid.color;
                chart.options.scales.x.title.color = themeOptions.scales.x.ticks.color;
                chart.options.scales.y.ticks.color = themeOptions.scales.y.ticks.color;
                chart.options.scales.y.grid.color = themeOptions.scales.y.grid.color;
                chart.options.scales.y.title.color = themeOptions.scales.y.ticks.color;
                chart.options.plugins.legend.labels.color = themeOptions.plugins.legend.labels.color;
                chart.update();
            }
        });
    }

    function fetchHistoricalData() {
        fetch('/api/historical_stats')
            .then(response => response.json())
            .then(data => {
                const newLabels = data.labels.map(ts => new Date(ts));
                if (!historicalChartsInitialized) {
                    historicalCpuChart = createHistoricalChart('historicalCpuChart', 'CPU Usage', { labels: newLabels, values: data.cpu_data }, getCssVariable('--chart-line-cpu'));
                    historicalRamChart = createHistoricalChart('historicalRamChart', 'RAM Usage', { labels: newLabels, values: data.ram_data }, getCssVariable('--chart-line-ram'));
                    historicalDiskChart = createHistoricalChart('historicalDiskChart', 'Disk Usage', { labels: newLabels, values: data.disk_data }, getCssVariable('--chart-line-disk'));
                    historicalChartsInitialized = true;
                } else {
                    [historicalCpuChart, historicalRamChart, historicalDiskChart].forEach(chart => {
                        if (chart) {
                            chart.data.labels = newLabels;
                            if (chart === historicalCpuChart) chart.data.datasets[0].data = data.cpu_data;
                            if (chart === historicalRamChart) chart.data.datasets[0].data = data.ram_data;
                            if (chart === historicalDiskChart) chart.data.datasets[0].data = data.disk_data;
                            chart.update(); // Chart.js will handle smooth transitions if animation duration > 0
                        }
                    });
                }
                updateHistoricalChartsTheme(); // Apply current theme options after creation/update
            })
            .catch(error => console.error('Error fetching historical stats:', error));
    }

    // --- Remote Server Data Fetch & Display ---
function createProgressBar(percentage, type) {
        const container = document.createElement('div');
        container.className = 'progress-bar-container';
        container.title = `${type.toUpperCase()}: ${percentage.toFixed(1)}%`;

        const bar = document.createElement('div');
        bar.className = 'progress-bar';

        let barColor;
        if (percentage <= 70) {
            barColor = getCssVariable('--progress-bar-green');
        } else if (percentage <= 90) {
            barColor = getCssVariable('--progress-bar-yellow');
        } else {
            barColor = getCssVariable('--progress-bar-red');
        }
        bar.style.backgroundColor = barColor;
        bar.style.width = `${Math.max(0, Math.min(100, percentage)).toFixed(1)}%`;

        const textOverlay = document.createElement('div');
        textOverlay.className = 'progress-bar-text';
        textOverlay.textContent = `${percentage.toFixed(1)}%`;

        container.appendChild(bar);
        container.appendChild(textOverlay);
        return container;
    }

    function fetchRemoteServersData() {
        if (!remoteServersTableBody) return;

        fetch('/api/remote_servers_stats')
            .then(response => response.json())
            .then(servers => {
                remoteServersTableBody.innerHTML = ''; // Clear existing rows
                servers.forEach(server => {
                    const row = remoteServersTableBody.insertRow();
                    row.insertCell().textContent = server.name;
                    row.insertCell().textContent = server.host;

                    const statusCell = row.insertCell();
                    const statusSpan = document.createElement('span');
                    statusSpan.textContent = server.status.charAt(0).toUpperCase() + server.status.slice(1);
                    statusSpan.className = `status-${server.status}`;
                    if (server.error_message) { // Add error as tooltip to status
                        statusSpan.title = server.error_message;
                    }
                    statusCell.appendChild(statusSpan);

                    // CPU Cores and Model
                    row.insertCell().textContent = server.status === 'online' ? server.cpu_cores : 'N/A';
                    const modelCell = row.insertCell();
                    modelCell.textContent = server.status === 'online' ? server.cpu_model : 'N/A';
                    modelCell.title = server.cpu_model;

                    // RAM and Disk Usage (GB)
                    row.insertCell().textContent = server.status === 'online' ? `${server.ram_used_gb.toFixed(1)} / ${server.ram_total_gb.toFixed(1)} GB` : 'N/A';
                    row.insertCell().textContent = server.status === 'online' ? `${server.disk_used_gb.toFixed(1)} / ${server.disk_total_gb.toFixed(1)} GB` : 'N/A';

                    // Separate cells for CPU %, RAM %, Disk % progress bars
                    const cpuPercentCell = row.insertCell();
                    const ramPercentCell = row.insertCell();
                    const diskPercentCell = row.insertCell();

                    if (server.status === 'online') {
                        cpuPercentCell.appendChild(createProgressBar(server.cpu_percent, 'CPU'));
                        ramPercentCell.appendChild(createProgressBar(server.ram_percent, 'RAM'));
                        diskPercentCell.appendChild(createProgressBar(server.disk_percent, 'Disk'));
                    } else {
                        cpuPercentCell.textContent = 'N/A';
                        ramPercentCell.textContent = 'N/A';
                        diskPercentCell.textContent = 'N/A';
                    }
                });
                if (remoteLastUpdatedSpan) remoteLastUpdatedSpan.textContent = new Date().toLocaleTimeString();
            })
            .catch(error => {
                console.error('Error fetching remote server stats:', error);
                if (remoteServersTableBody) remoteServersTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:red;">Error loading remote server data. Check console.</td></tr>`; // Adjusted colspan to 10
            });
    }

    // --- Universal Theme Application Function ---
    function applyThemeToVisuals() {
        // Destroy and re-create gauges with current values and new theme colors
        // (Gauge track color is theme-dependent)
        const currentCpuVal = parseFloat(cpuValueEl?.textContent) || 0;
        const currentRamVal = parseFloat(ramValueEl?.textContent) || 0;
        const currentDiskVal = parseFloat(diskValueEl?.textContent) || 0;

        if (cpuGaugeChart) cpuGaugeChart.destroy();
        if (ramGaugeChart) ramGaugeChart.destroy();
        if (diskGaugeChart) diskGaugeChart.destroy();

        cpuGaugeChart = createGauge('cpuGauge', currentCpuVal, getCssVariable('--gauge-cpu-color'));
        ramGaugeChart = createGauge('ramGauge', currentRamVal, getCssVariable('--gauge-ram-color'));
        diskGaugeChart = createGauge('diskGauge', currentDiskVal, getCssVariable('--gauge-disk-color'));

        // Update historical chart themes (axis colors, legend colors, etc.)
        if (historicalChartsInitialized) {
            updateHistoricalChartsTheme();
        }
    }

    // --- Dark Mode Setup & Toggle ---
    function setInitialTheme() {
        const prefersDarkScheme = window.matchMedia("(prefers-color-scheme: dark)");
        const currentTheme = localStorage.getItem("theme");
        if (currentTheme === "dark" || (!currentTheme && prefersDarkScheme.matches)) {
            body.classList.add("dark-mode");
        } else {
            body.classList.remove("dark-mode");
        }
    }

    darkModeToggle.addEventListener('click', () => {
        body.classList.toggle('dark-mode');
        localStorage.setItem("theme", body.classList.contains("dark-mode") ? "dark" : "light");
        applyThemeToVisuals();
    });

    // --- Tab Switching Logic ---
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            const tabId = button.getAttribute('data-tab');
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === tabId) {
                    content.classList.add('active');
                }
            });

            // Fetch data for newly activated tab if needed
            if (tabId === 'historical') {
                if (!historicalChartsInitialized) fetchHistoricalData(); // Initial load
                else updateHistoricalChartsTheme(); // Ensure theme is current if re-visiting
            } else if (tabId === 'remote') {
                fetchRemoteServersData(); // Always fetch fresh on tab activation
            }
        });
    });

    // --- Initialization ---
    setInitialTheme();      // 1. Set dark/light mode class on body
    applyThemeToVisuals();  // 2. Create gauges with correct theme & prepare historical chart theming

    // 3. Initial data fetches for visible/default tab
    fetchCurrentData();
    const activeTab = document.querySelector('.tab-button.active')?.getAttribute('data-tab');
    if (activeTab === 'historical') {
        fetchHistoricalData();
    } else if (activeTab === 'remote') {
        fetchRemoteServersData();
    }

    // --- Auto-Refresh Intervals ---
    setInterval(fetchCurrentData, CURRENT_STATS_REFRESH_INTERVAL);

    setInterval(() => {
        const currentActiveTabId = document.querySelector('.tab-button.active')?.getAttribute('data-tab');
        if (currentActiveTabId === 'historical' && historicalChartsInitialized) {
            fetchHistoricalData(); // Refresh historical data (pulls latest from DB)
        } else if (currentActiveTabId === 'remote') {
            fetchRemoteServersData(); // Refresh remote server stats
        }
    }, OTHER_TABS_REFRESH_INTERVAL);

});