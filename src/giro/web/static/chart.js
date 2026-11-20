// Desenha gráficos de barras a partir do JSON embutido em <script id="...-data">.
function drawBars(canvasId, dataId, label, money) {
  const canvas = document.getElementById(canvasId);
  const data = document.getElementById(dataId);
  if (!canvas || !data || typeof Chart === "undefined") return;

  const { labels, values } = JSON.parse(data.textContent);
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue("--accent").trim();
  const muted = styles.getPropertyValue("--muted").trim();
  const line = styles.getPropertyValue("--line").trim();
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

  new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label, data: values, backgroundColor: accent, borderRadius: 3 }],
    },
    options: {
      maintainAspectRatio: false,
      animation: reduced ? false : undefined,
      plugins: {
        legend: { display: false },
        tooltip: money ? { callbacks: { label: (c) => brl.format(c.parsed.y) } } : {},
      },
      scales: {
        x: { ticks: { color: muted, maxRotation: 0, autoSkip: true }, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: { color: muted, callback: money ? (v) => brl.format(v) : undefined },
          grid: { color: line },
        },
      },
    },
  });
}

document.addEventListener("DOMContentLoaded", () => {
  drawBars("sales-chart", "sales-data", "Saídas", false);
  drawBars("payments-chart", "payments-data", "Vendas", true);
});
