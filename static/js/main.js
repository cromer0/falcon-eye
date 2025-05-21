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

    // Alerts View Elements
    const alertsView = document.getElementById('alertsView');
    const alertForm = document.getElementById('alertForm');
    const alertFormTitle = document.getElementById('alertFormTitle');
    const alertIdStore = document.getElementById('alertIdStore');
    const alertNameInput = document.getElementById('alertName');
    const alertServersSelect = document.getElementById('alertServers');
    const alertResourcesSelect = document.getElementById('alertResources');
    const alertThresholdInput = document.getElementById('alertThreshold');
    const alertTimeFrameInput = document.getElementById('alertTimeFrame');
    const alertChannelSelect = document.getElementById('alertChannel');
    const alertRecipientsTextarea = document.getElementById('alertRecipients');
    const alertIsActiveCheckbox = document.getElementById('alertIsActive');
    const saveAlertButton = document.getElementById('saveAlertButton');
    const cancelAlertEditButton = document.getElementById('cancelAlertEditButton');
    const alertsTableBody = document.querySelector('#alertsTable tbody');
    const navButtons = document.querySelectorAll('.nav-button');


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
        // Hide all views
        document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
        // Show the selected view
        const
        activeView = document.getElementById(viewId);
        if (activeView) {
            activeView.classList.add('active');
        }

        // Update nav button states
        navButtons.forEach(button => {
            if (button.dataset.view === viewId) {
                button.classList.add('active');
            } else {
                button.classList.remove('active');
            }
        });


        if (viewId === 'serverListView') {
            if (detailViewInterval) clearInterval(detailViewInterval);
            selectedServerData = null;
            destroyDetailHistoricalCharts();
            fetchServerList(); // Refresh server list when switching to this view
        } else if (viewId === 'serverDetailView') {
            if (detailViewInterval) clearInterval(detailViewInterval);
            if (selectedServerData) { // Ensure a server is selected before starting interval
                 updateSelectedServerCurrentData(); // Initial call
                 detailViewInterval = setInterval(updateSelectedServerCurrentData, DETAIL_VIEW_REFRESH_INTERVAL);
            } else {
                 showView('serverListView'); // Fallback if no server selected
            }
        } else if (viewId === 'alertsView') {
            if (detailViewInterval) clearInterval(detailViewInterval);
            populateAlertFormServerSelect(); // Populate server list in form
            fetchAndDisplayAlerts(); // Load existing alerts
            resetAlertForm(); // Ensure form is clean
        }
    }
    
    // Initialize Select2 for multi-select dropdowns
    // Ensure jQuery is loaded before this if using jQuery-dependent Select2 version
    // For standalone, ensure script is deferred or run after DOM is ready.
    // $(document).ready(function() { // If using jQuery
        if (typeof $ !== 'undefined' && $.fn && $.fn.select2) { // Check if jQuery and Select2 are loaded
            $('#alertServers').select2({ placeholder: "Select servers", allowClear: true, width: '100%'});
            $('#alertResources').select2({ placeholder: "Select resources", allowClear: true, width: '100%'});
        } else {
            console.warn("Select2 library not loaded or jQuery not available. Multiselects will be standard.");
        }
    // });


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
            .then(response => {
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                return response.json();
            })
            .then(servers => {
                allServersData = servers; // Cache the full data
                if (remoteServersTableBody) {
                    remoteServersTableBody.innerHTML = ''; // Clear existing rows
                    if (servers && servers.length > 0) {
                        servers.forEach(server => {
                            const row = remoteServersTableBody.insertRow();
                            row.insertCell().textContent = server.name;
                            row.insertCell().textContent = server.host;

                            const statusSpan = document.createElement('span');
                            statusSpan.textContent = server.status.charAt(0).toUpperCase() + server.status.slice(1);
                            statusSpan.className = `status-${server.status}`;
                            if (server.error_message) statusSpan.title = server.error_message;
                            row.insertCell().appendChild(statusSpan);

                            const cpuCell = row.insertCell();
                            const ramCell = row.insertCell();
                            const diskCell = row.insertCell();

                            if (server.status === 'online') {
                                cpuCell.appendChild(createProgressBar(server.cpu_percent, 'CPU'));
                                ramCell.appendChild(createProgressBar(server.ram_percent, 'RAM'));
                                diskCell.appendChild(createProgressBar(server.disk_percent, 'Disk'));
                            } else {
                                ['N/A','N/A','N/A'].forEach((text, i) => [cpuCell,ramCell,diskCell][i].textContent = text);
                            }

                            row.dataset.serverHost = server.host;
                            row.dataset.serverName = server.name;
                            row.addEventListener('click', () => handleServerSelect(server.host));
                        });
                    } else {
                         remoteServersTableBody.innerHTML = `<tr><td colspan="6">No servers configured or available.</td></tr>`;
                    }
                }
                if (serverListLastUpdatedSpan) serverListLastUpdatedSpan.textContent = new Date().toLocaleTimeString();
                
                // If alerts view is active, update its server list too (or do it on view switch)
                if (alertsView && alertsView.classList.contains('active')) {
                    populateAlertFormServerSelect();
                }
            })
            .catch(error => {
                console.error('Error fetching server list:', error);
                if (remoteServersTableBody) remoteServersTableBody.innerHTML = `<tr><td colspan="6">Error loading server data. ${error.message}</td></tr>`;
            });
    }
    // ProgressBar for list view
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
        destroyDetailHistoricalCharts(); // Clear previous charts and hide containers
        document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'none');
        historicalDataMessageEl.style.display = 'block'; // Show message area by default

        if (!selectedServerData || !selectedServerData.host) {
            historicalDataMessageEl.textContent = "No server selected or server host is missing.";
            return;
        }

        historicalDataMessageEl.textContent = "Loading historical data...";
        const apiUrl = `/api/historical_stats?server_host=${selectedServerData.host}`;

        fetch(apiUrl)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Network response was not ok: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (data && data.labels && data.labels.length > 0) {
                    historicalDataMessageEl.style.display = 'none'; // Hide message element
                    document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'block'); // Show chart containers

                    detailHistoricalCpuChart = createDetailHistoricalChart(detailHistCpuChartEl, 'CPU Usage', { labels: data.labels, values: data.cpu_data }, getCssVariable('--chart-line-cpu'));
                    detailHistoricalRamChart = createDetailHistoricalChart(detailHistRamChartEl, 'RAM Usage', { labels: data.labels, values: data.ram_data }, getCssVariable('--chart-line-ram'));
                    detailHistoricalDiskChart = createDetailHistoricalChart(detailHistDiskChartEl, 'Disk Usage', { labels: data.labels, values: data.disk_data }, getCssVariable('--chart-line-disk'));
                    
                    historicalChartsInitializedForDetail = true;
                    applyThemeToVisuals(); // Apply theme after creation
                } else {
                    historicalDataMessageEl.textContent = "No historical data available for this server.";
                    // Ensure charts are hidden if they were somehow made visible before this check
                    document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'none');
                }
            })
            .catch(error => {
                console.error(`Error fetching historical data for ${selectedServerData.host}:`, error);
                historicalDataMessageEl.textContent = "Error loading historical data. Check console for details.";
                document.querySelectorAll('#detailHistoricalChartContainer .chart-container').forEach(c => c.style.display = 'none');
            });
    }

    // Detail View Tab Switcher
    if (detailTabButtons) {
        detailTabButtons.forEach(button => {
            button.addEventListener('click', () => {
                detailTabButtons.forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');
                const tabId = button.getAttribute('data-tab');
                detailTabContents.forEach(content => content.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');

                if (tabId === 'detail-historical') {
                    handleHistoricalTabForSelectedServer();
                }
            });
        });
    }

    if (backToServerListButton) {
        backToServerListButton.addEventListener('click', () => showView('serverListView'));
    }

    // --- Alerts Management Logic ---
    function populateAlertFormServerSelect() {
        if (!alertServersSelect) return;
        const currentSelection = $(alertServersSelect).val(); // Preserve selection if using Select2
        
        alertServersSelect.innerHTML = ''; // Clear existing
        if (allServersData && allServersData.length > 0) {
            allServersData.forEach(server => {
                if (server.host) { // Ensure host is valid
                    const option = new Option(`${server.name} (${server.host})`, server.host);
                    alertServersSelect.add(option);
                }
            });
        } else {
            alertServersSelect.innerHTML = '<option value="" disabled>No servers available</option>';
        }
        if (typeof $ !== 'undefined' && $.fn && $.fn.select2) $(alertServersSelect).val(currentSelection).trigger('change'); // Restore selection for Select2
    }

    function resetAlertForm() {
        if (!alertForm) return;
        alertForm.reset();
        alertIdStore.value = '';
        alertFormTitle.textContent = 'Configure Alert';
        saveAlertButton.textContent = 'Save Alert';
        if (typeof $ !== 'undefined' && $.fn && $.fn.select2) { // Reset Select2 fields
            $(alertServersSelect).val(null).trigger('change');
            $(alertResourcesSelect).val(null).trigger('change');
        }
    }

    if (alertForm) {
        alertForm.addEventListener('submit', function(event) {
            event.preventDefault();
            const alertId = alertIdStore.value;
            
            let recipientsArray = [];
            if (alertRecipientsTextarea.value.trim()) {
                recipientsArray = alertRecipientsTextarea.value.split(',').map(email => email.trim()).filter(email => email);
            }
            if (alertChannelSelect.value !== 'email' && recipientsArray.length > 1) {
                 // A simple alert, could be replaced by a more robust notification system in the UI
                alert("For non-email channels, please provide a single recipient ID/token.");
                return;
            }


            const alertData = {
                alert_name: alertNameInput.value,
                server_hosts: $(alertServersSelect).val(), // For Select2
                resource_types: $(alertResourcesSelect).val(), // For Select2
                threshold_percent: parseFloat(alertThresholdInput.value),
                time_frame_minutes: parseInt(alertTimeFrameInput.value, 10),
                communication_channel: alertChannelSelect.value,
                recipients: recipientsArray,
                is_active: alertIsActiveCheckbox.checked
            };

            // Basic validation
            if (!alertData.alert_name || !alertData.server_hosts || alertData.server_hosts.length === 0 ||
                !alertData.resource_types || alertData.resource_types.length === 0 || isNaN(alertData.threshold_percent) ||
                isNaN(alertData.time_frame_minutes) || !alertData.communication_channel || !alertData.recipients || alertData.recipients.length === 0) {
                alert("Please fill all required fields correctly."); // Replace with better UI feedback
                return;
            }

            const method = alertId ? 'PUT' : 'POST';
            const url = alertId ? `/api/alerts/${alertId}` : '/api/alerts';

            fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(alertData)
            })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => { throw new Error(err.error || `HTTP error ${response.status}`) });
                }
                return response.json();
            })
            .then(data => {
                // alert(alertId ? 'Alert updated successfully!' : 'Alert created successfully!'); // Replace with better UI feedback
                console.log(alertId ? 'Alert updated:' : 'Alert created:', data);
                resetAlertForm();
                fetchAndDisplayAlerts();
            })
            .catch(error => {
                console.error('Error saving alert:', error);
                alert(`Error saving alert: ${error.message}`); // Replace with better UI feedback
            });
        });
    }

    if (cancelAlertEditButton) {
        cancelAlertEditButton.addEventListener('click', resetAlertForm);
    }

    function fetchAndDisplayAlerts() {
        if (!alertsTableBody) return;
        fetch('/api/alerts')
            .then(response => response.json())
            .then(alerts => {
                alertsTableBody.innerHTML = ''; // Clear existing
                if (alerts && alerts.length > 0) {
                    alerts.forEach(alert => {
                        const row = alertsTableBody.insertRow();
                        row.insertCell().textContent = alert.alert_name;
                        row.insertCell().textContent = alert.server_hosts.join(', ');
                        row.insertCell().textContent = alert.resource_types.join(', ');
                        row.insertCell().textContent = alert.threshold_percent + '%';
                        row.insertCell().textContent = alert.time_frame_minutes + ' min';
                        row.insertCell().textContent = alert.communication_channel;
                        row.insertCell().textContent = alert.recipients.join(', ');
                        row.insertCell().textContent = alert.is_active ? 'Yes' : 'No';
                        
                        const actionsCell = row.insertCell();
                        const editButton = document.createElement('button');
                        editButton.textContent = 'Edit';
                        editButton.className = 'action-button edit-alert';
                        editButton.addEventListener('click', () => populateAlertFormForEdit(alert));
                        actionsCell.appendChild(editButton);

                        const deleteButton = document.createElement('button');
                        deleteButton.textContent = 'Delete';
                        deleteButton.className = 'action-button delete-alert';
                        deleteButton.addEventListener('click', () => deleteAlert(alert.id));
                        actionsCell.appendChild(deleteButton);
                    });
                } else {
                    alertsTableBody.innerHTML = '<tr><td colspan="9">No alerts configured yet.</td></tr>';
                }
            })
            .catch(error => {
                console.error('Error fetching alerts:', error);
                if (alertsTableBody) alertsTableBody.innerHTML = '<tr><td colspan="9">Error loading alerts.</td></tr>';
            });
    }

    function populateAlertFormForEdit(alert) {
        resetAlertForm(); // Clear first
        alertFormTitle.textContent = 'Edit Alert';
        saveAlertButton.textContent = 'Update Alert';
        alertIdStore.value = alert.id;
        alertNameInput.value = alert.alert_name;
        // For Select2, need to set value and trigger change
        if (typeof $ !== 'undefined' && $.fn && $.fn.select2) {
            $(alertServersSelect).val(alert.server_hosts).trigger('change');
            $(alertResourcesSelect).val(alert.resource_types).trigger('change');
        } else { // Fallback for standard multi-select
            Array.from(alertServersSelect.options).forEach(opt => { opt.selected = alert.server_hosts.includes(opt.value); });
            Array.from(alertResourcesSelect.options).forEach(opt => { opt.selected = alert.resource_types.includes(opt.value); });
        }
        alertThresholdInput.value = alert.threshold_percent;
        alertTimeFrameInput.value = alert.time_frame_minutes;
        alertChannelSelect.value = alert.communication_channel;
        alertRecipientsTextarea.value = alert.recipients.join(', ');
        alertIsActiveCheckbox.checked = alert.is_active;
        alertNameInput.focus(); // Bring focus to the form
        window.scrollTo({ top: alertForm.offsetTop - 20, behavior: 'smooth' });

    }

    function deleteAlert(alertId) {
        if (!confirm('Are you sure you want to delete this alert?')) return;

        fetch(`/api/alerts/${alertId}`, { method: 'DELETE' })
            .then(response => {
                if (!response.ok) {
                     return response.json().then(err => { throw new Error(err.error || `HTTP error ${response.status}`) });
                }
                // No content for 204, so don't try to parse JSON if response.status is 204
                if (response.status === 204) {
                    return null; 
                }
                return response.json();
            })
            .then(() => {
                // alert('Alert deleted successfully!'); // Replace with better UI feedback
                fetchAndDisplayAlerts(); // Refresh list
            })
            .catch(error => {
                console.error('Error deleting alert:', error);
                alert(`Error deleting alert: ${error.message}`); // Replace with better UI feedback
            });
    }
    
    // Main navigation view switcher
    if (navButtons) {
        navButtons.forEach(button => {
            button.addEventListener('click', () => {
                const viewId = button.dataset.view;
                showView(viewId);
            });
        });
    }


    // --- Initialization ---
    setInitialTheme();
    applyThemeToVisuals(); 
    fetchServerList(); // Initial server list load, which also populates allServersData
    showView('serverListView'); // Start with the server list

    setInterval(fetchServerList, SERVER_LIST_REFRESH_INTERVAL);
});
