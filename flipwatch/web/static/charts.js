// Draws the item price and volume charts and the backtest equity chart from JSON
// that the page embeds in <script type="application/json"> tags.
(function () {
  const styles = getComputedStyle(document.documentElement);
  const colour = function (name) { return styles.getPropertyValue(name).trim(); };

  Chart.defaults.color = colour("--muted");
  Chart.defaults.borderColor = colour("--border");
  Chart.defaults.font.family = styles.fontFamily;

  const coins = new Intl.NumberFormat("en-GB");
  const tooltip = {
    callbacks: {
      label: function (context) {
        const value = context.parsed.y;
        return context.dataset.label + ": " + (value === null ? "no trades" : coins.format(value));
      },
    },
  };

  function readData(id) {
    const element = document.getElementById(id);
    return element ? JSON.parse(element.textContent) : null;
  }

  function line(label, data, colourName) {
    return {
      label: label,
      data: data,
      borderColor: colour(colourName),
      backgroundColor: colour(colourName),
      borderWidth: 1.5,
      pointRadius: 0,
      spanGaps: true,
      tension: 0.2,
    };
  }

  const prices = readData("price-data");
  if (prices) {
    new Chart(document.getElementById("price-chart"), {
      type: "line",
      data: {
        labels: prices.labels,
        datasets: [
          line("Average high", prices.high, "--high"),
          line("Average low", prices.low, "--low"),
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { tooltip: tooltip },
        scales: {
          x: { ticks: { maxTicksLimit: 8 } },
          y: { ticks: { callback: function (value) { return coins.format(value); } } },
        },
      },
    });

    new Chart(document.getElementById("volume-chart"), {
      type: "bar",
      data: {
        labels: prices.labels,
        datasets: [
          { label: "High volume", data: prices.highVolume, backgroundColor: colour("--high") },
          { label: "Low volume", data: prices.lowVolume, backgroundColor: colour("--low") },
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: tooltip },
        scales: {
          x: { stacked: true, ticks: { display: false } },
          y: { stacked: true, ticks: { maxTicksLimit: 4 } },
        },
      },
    });
  }

  const equity = readData("equity-data");
  if (equity) {
    new Chart(document.getElementById("equity-chart"), {
      type: "line",
      data: { labels: equity.labels, datasets: [line("Equity", equity.equity, "--accent")] },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: tooltip },
        scales: {
          x: { ticks: { maxTicksLimit: 8 } },
          y: { ticks: { callback: function (value) { return coins.format(value); } } },
        },
      },
    });
  }
})();
