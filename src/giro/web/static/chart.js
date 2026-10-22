// Desenha o gráfico de barras a partir do JSON embutido em <script id="sales-data">.
document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("sales-chart");
  const data = document.getElementById("sales-data");
  if (!canvas || !data || typeof Chart === "undefined") return;

  const { labels, values } = JSON.parse(data.textContent);
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue("--accent").trim();
  const muted = styles.getPropertyValue("--muted").trim();
  const line = styles.getPropertyValue("--line").trim();

  new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "Saídas", data: values, backgroundColor: accent, borderRadius: 3 }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: muted, maxRotation: 0, autoSkip: true }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: muted }, grid: { color: line } },
      },
    },
  });
});
