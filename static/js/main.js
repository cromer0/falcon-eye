document.addEventListener('DOMContentLoaded', () => {
    const darkModeToggle = document.getElementById('darkModeToggle');
    const body = document.body;

    // --- Dark Mode ---
    const prefersDarkScheme = window.matchMedia("(prefers-color-scheme: dark)");
    const currentTheme = localStorage.getItem("theme");

    if (currentTheme == "dark" || (!currentTheme && prefersDarkScheme.matches)) {
        body.classList.add("dark-mode");
    } else {
        body.classList.remove("dark-mode");
    }

    darkModeToggle.addEventListener('click', () => {
        body.classList.toggle('dark-mode');
        let theme = "light";
        if (body.classList.contains("dark-mode")) {
            theme = "dark";
        }
        localStorage.setItem("theme", theme);
    });

    // --- Tabs ---
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

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
            if (tabId === 'historical' && !historicalChartsInitialized) {
                fetchHistoricalData(); // Load historical data when tab is first clicked
            }
        });
    });

    // --- Chart.js Common Config ---
    const getChartJsThemeOptions = () => {
        const isDarkMode = body.classList.contains('dark-mode');
        const gridColor = isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)';
        const textColor = isDarkMode ? '#e0e0e0' : '#333';
        return {
            scales: {
                x: {
                    ticks: { color: textColor },
                    grid: { color: gridColor }
                },
                y: {
                    ticks: { color: textColor },
                    grid: { color: gridColor },
                    beginAtZero: true,
                    max: 100 // For percentage-based charts
                }
            },
            plugins: {
                legend: { labels: { color: textColor } }
            }
        };
    };


    // --- Current Data Gauges ---
    let cpuGaugeChart, ramGaugeChart, diskGaugeChart;

    function createGauge(canvasId, value, color) {
        const ctx = document.getElementById(canvasId).getContext('2d');
        const isDarkMode = body.classList.contains('dark-mode');
        const trackColor = isDarkMode ? getComputedStyle(document.documentElement).getPropertyValue('--gauge-track-color').trim() : '#e9ecef';

        return new Chart(ctx, {
            type: 'doughnut',
            data: {
                datasets: [{
                    data: [value, 100 - value],
                    backgroundColor: [color, trackColor],
                    borderWidth: 0,
                    circumference: 270, // Makes it a semi-circle or 3/4 gauge
                    rotation: 225      // Starts from bottom-left
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                cutout: '75%', // Thickness of the gauge
                plugins: {
                    tooltip: { enabled: false },
                    legend: { display: false }
                },
                animation: {
                    duration: 500 // Smooth transition
                }
            }
        });
    }

    function updateGauge(chart, value) {
        if (chart) {
            chart.data.datasets[0].data[0] = value;
            chart.data.datasets[0].data[1] = 100 - value;
            chart.update('none'); // 'none' for no animation on update
        }
    }

    // Initialize Gauges (will be updated by fetchData)
    cpuGaugeChart = createGauge('cpuGauge', 0, getComputedStyle(document.documentElement).getPropertyValue('--gauge-cpu-color').trim());
    ramGaugeChart = createGauge('ramGauge', 0, getComputedStyle(document.documentElement).getPropertyValue('--gauge-ram-color').trim());
    diskGaugeChart = createGauge('diskGauge', 0, getComputedStyle(document.documentElement).getPropertyValue('--gauge-disk-color').trim());


    function fetchCurrentData() {
        fetch('/api/current_stats')
            .then(response => response.json())
            .then(data => {
                document.getElementById('cpuValue').textContent = `${data.cpu_percent.toFixed(1)}%`;
                document.getElementById('ramValue').textContent = `${data.ram_percent.toFixed(1)}%`;
                document.getElementById('ramDetail').textContent = `${data.ram_used_gb} GB / ${data.ram_total_gb} GB`;
                document.getElementById('diskValue').textContent = `${data.disk_percent.toFixed(1)}%`;
                document.getElementById('diskDetail').textContent = `${data.disk_used_gb} GB / ${data.disk_total_gb} GB`;

                updateGauge(cpuGaugeChart, data.cpu_percent);
                updateGauge(ramGaugeChart, data.ram_percent);
                updateGauge(diskGaugeChart, data.disk_percent);

                const updateTime = new Date(data.timestamp).toLocaleTimeString();
                document.getElementById('lastUpdated').textContent = updateTime;
            })
            .catch(error => console.error('Error fetching current stats:', error));
    }

    // --- Historical Data Charts ---
    let historicalCpuChart, historicalRamChart, historicalDiskChart;
    let historicalChartsInitialized = false;

    function createHistoricalChart(canvasId, label, data, color) {
        const ctx = document.getElementById(canvasId).getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels.map(ts => new Date(ts)), // Convert ISO strings to Date objects
                datasets: [{
                    label: label,
                    data: data.values,
                    borderColor: color,
                    backgroundColor: color.replace(')', ', 0.2)').replace('rgb', 'rgba'), // semi-transparent fill
                    fill: true,
                    tension: 0.3, // Smooth lines
                    pointRadius: 2,
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                 scales: {
                    x: {
                        type: 'time',
                        time: {
                            unit: 'hour', // Adjust based on data density (minute, hour, day)
                             tooltipFormat: 'MMM d, HH:mm', // Format for tooltips
                            displayFormats: {
                                hour: 'HH:mm' // Format for x-axis labels
                            }
                        },
                        title: { display: true, text: 'Time' },
                        ticks: { color: getChartJsThemeOptions().scales.x.ticks.color },
                        grid: { color: getChartJsThemeOptions().scales.x.grid.color }
                    },
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: { display: true, text: 'Usage (%)' },
                        ticks: { color: getChartJsThemeOptions().scales.y.ticks.color },
                        grid: { color: getChartJsThemeOptions().scales.y.grid.color }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        labels: { color: getChartJsThemeOptions().plugins.legend.labels.color }
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                    }
                }
            }
        });
    }

    function updateHistoricalChartThemes() {
        const themeOptions = getChartJsThemeOptions();
        [historicalCpuChart, historicalRamChart, historicalDiskChart].forEach(chart => {
            if (chart) {
                chart.options.scales.x.ticks.color = themeOptions.scales.x.ticks.color;
                chart.options.scales.x.grid.color = themeOptions.scales.x.grid.color;
                chart.options.scales.y.ticks.color = themeOptions.scales.y.ticks.color;
                chart.options.scales.y.grid.color = themeOptions.scales.y.grid.color;
                chart.options.plugins.legend.labels.color = themeOptions.plugins.legend.labels.color;
                chart.update();
            }
        });
         // Update gauge colors based on theme
        if(cpuGaugeChart) {
            const isDarkMode = body.classList.contains('dark-mode');
            const trackColor = isDarkMode ? getComputedStyle(document.documentElement).getPropertyValue('--gauge-track-color').trim() : '#e9ecef';
            cpuGaugeChart.data.datasets[0].backgroundColor[1] = trackColor;
            ramGaugeChart.data.datasets[0].backgroundColor[1] = trackColor;
            diskGaugeChart.data.datasets[0].backgroundColor[1] = trackColor;
            cpuGaugeChart.update('none');
            ramGaugeChart.update('none');
            diskGaugeChart.update('none');
        }
    }


    function fetchHistoricalData() {
        fetch('/api/historical_stats')
            .then(response => response.json())
            .then(data => {
                if (!historicalChartsInitialized) { // Create charts only once
                    historicalCpuChart = createHistoricalChart('historicalCpuChart', 'CPU Usage',
                        { labels: data.labels, values: data.cpu_data },
                        getComputedStyle(document.documentElement).getPropertyValue('--chart-line-cpu').trim()
                    );
                    historicalRamChart = createHistoricalChart('historicalRamChart', 'RAM Usage',
                        { labels: data.labels, values: data.ram_data },
                        getComputedStyle(document.documentElement).getPropertyValue('--chart-line-ram').trim()
                    );
                    historicalDiskChart = createHistoricalChart('historicalDiskChart', 'Disk Usage',
                        { labels: data.labels, values: data.disk_data },
                        getComputedStyle(document.documentElement).getPropertyValue('--chart-line-disk').trim()
                    );
                    historicalChartsInitialized = true;
                } else { // Update existing charts
                    [historicalCpuChart, historicalRamChart, historicalDiskChart].forEach(chart => {
                        if (chart) {
                            chart.data.labels = data.labels.map(ts => new Date(ts));
                            if (chart === historicalCpuChart) chart.data.datasets[0].data = data.cpu_data;
                            if (chart === historicalRamChart) chart.data.datasets[0].data = data.ram_data;
                            if (chart === historicalDiskChart) chart.data.datasets[0].data = data.disk_data;
                            chart.update();
                        }
                    });
                }
                 updateHistoricalChartThemes(); // Apply theme after creating/updating
            })
            .catch(error => console.error('Error fetching historical stats:', error));
    }

    // Update theme on toggle
    darkModeToggle.addEventListener('click', () => {
        updateHistoricalChartThemes();
        // Re-create gauges with new track color (simple approach)
        // Better: Update track color directly if Chart.js allows easy access
        const currentCpuVal = parseFloat(document.getElementById('cpuValue').textContent) || 0;
        const currentRamVal = parseFloat(document.getElementById('ramValue').textContent) || 0;
        const currentDiskVal = parseFloat(document.getElementById('diskValue').textContent) || 0;

        if(cpuGaugeChart) cpuGaugeChart.destroy();
        if(ramGaugeChart) ramGaugeChart.destroy();
        if(diskGaugeChart) diskGaugeChart.destroy();

        cpuGaugeChart = createGauge('cpuGauge', currentCpuVal, getComputedStyle(document.documentElement).getPropertyValue('--gauge-cpu-color').trim());
        ramGaugeChart = createGauge('ramGauge', currentRamVal, getComputedStyle(document.documentElement).getPropertyValue('--gauge-ram-color').trim());
        diskGaugeChart = createGauge('diskGauge', currentDiskVal, getComputedStyle(document.documentElement).getPropertyValue('--gauge-disk-color').trim());
    });


    // Initial data load
    fetchCurrentData();
    // Load historical data if that tab is active initially (unlikely but good practice)
    if (document.querySelector('.tab-content.active').id === 'historical') {
        fetchHistoricalData();
    }


    // Auto-refresh current data every 2 seconds
    setInterval(fetchCurrentData, 2000);
    // Auto-refresh historical data (less frequently) if tab is active, e.g., every 60 seconds
    setInterval(() => {
        if (document.querySelector('#historical.tab-content.active')) {
            fetchHistoricalData();
        }
    }, HISTORICAL_DATA_COLLECTION_INTERVAL * 1000); // Match server collection interval
});

// Make HISTORICAL_DATA_COLLECTION_INTERVAL available from Python (passed in HTML or hardcoded)
// For simplicity, hardcoding it here to match Python's default.
// In a more complex app, you might pass this from Flask to the template.
const HISTORICAL_DATA_COLLECTION_INTERVAL = 60;