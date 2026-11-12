import { Component, onMounted, onPatched, onWillStart, onWillUnmount, toRaw, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";

// Palette (MuK steel/navy + accents)
export const SB_COLORS = ["#5D8DA8", "#243742", "#2A9D8F", "#E9A23B", "#D9634C", "#8E7DBE", "#7FB069", "#C9A227"];
export const SB_AGING = ["#2A9D8F", "#7FB069", "#E9A23B", "#E07A3F", "#D9634C", "#A63D40"];

export function sbMoney(v, digits = 0) {
    if (v === null || v === undefined || v === "") {
        return "";
    }
    const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return v < 0 ? `(${s})` : s;
}

/** Small wrapper around Chart.js: props {type, labels, series, height, stacked, horizontal, palette} */
export class SbChart extends Component {
    static template = "sonador_books.SbChart";
    static props = ["*"];

    setup() {
        this.canvas = useRef("canvas");
        onWillStart(() => loadBundle("web.chartjs_lib"));
        onMounted(() => this.draw());
        onPatched(() => this.draw());
        onWillUnmount(() => this.chart && this.chart.destroy());
    }

    config() {
        // Chart.js tags the arrays it is given; a reactive (OWL) array would then re-render the component, which
        // destroyed the chart mid-animation and left the chart area blank. It gets plain copies.
        const p = JSON.parse(JSON.stringify(toRaw(this.props), (k, v) => (typeof v === "function" ? undefined : v)));
        p.palette = this.props.palette;
        const palette = p.palette || SB_COLORS;
        let type = p.type;
        const stacked = type === "bar_stacked" || p.stacked;
        if (type === "bar_stacked") {
            type = "bar";
        }
        const doughnut = type === "doughnut";
        const datasets = (p.series || []).map((s, i) => {
            const color = palette[i % palette.length];
            const ds = { label: s.label, data: s.data };
            if (doughnut) {
                ds.backgroundColor = s.data.map((_, j) => palette[j % palette.length]);
                ds.borderWidth = 2;
                ds.borderColor = "#fff";
            } else if (type === "line") {
                Object.assign(ds, { borderColor: color, backgroundColor: color + "22", tension: 0.35, fill: i === 0,
                    pointRadius: 3, pointHoverRadius: 5, borderWidth: 2.5 });
            } else {
                Object.assign(ds, { backgroundColor: color, borderRadius: 4, maxBarThickness: 46 });
            }
            return ds;
        });
        const money = (v) => sbMoney(v);
        return {
            type,
            data: { labels: p.labels || [""], datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: p.horizontal ? "y" : "x",
                animation: { duration: 350 },
                plugins: {
                    legend: { display: doughnut || datasets.length > 1, position: doughnut ? "right" : "bottom",
                        labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { size: 11 } } },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label || ctx.label}: ${money(ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed)}` } },
                },
                cutout: doughnut ? "62%" : undefined,
                // the value axis gets short amounts (5M, 120K); the category axis keeps its labels
                scales: doughnut ? {} : (p.horizontal ? {
                    y: { stacked, grid: { display: false }, ticks: { font: { size: 11 } } },
                    x: { stacked, grid: { color: "#eef2f5" }, ticks: { font: { size: 11 }, callback: (v) => this.short(v) } },
                } : {
                    x: { stacked, grid: { display: false }, ticks: { font: { size: 11 } } },
                    y: { stacked, grid: { color: "#eef2f5" }, ticks: { font: { size: 11 }, callback: (v) => this.short(v) } },
                }),
            },
        };
    }

    short(v) {
        const a = Math.abs(v);
        const s = a >= 1e6 ? (a / 1e6).toFixed(1) + "M" : a >= 1e3 ? (a / 1e3).toFixed(0) + "K" : a.toFixed(a < 10 && a % 1 ? 1 : 0);
        return v < 0 ? "-" + s : s;
    }

    draw() {
        if (!this.canvas.el || !window.Chart) {
            return;
        }
        const config = this.config();
        const key = JSON.stringify([config.type, config.data, this.props.horizontal, this.props.stacked]);
        if (this.chart && this.chart.canvas === this.canvas.el && this.chart.config.type === config.type) {
            if (key === this.key) {
                // same data: only the box may have changed (chart / chart and table): resize, keep the drawing
                if (this.height !== this.props.height) {
                    this.height = this.props.height;
                    requestAnimationFrame(() => this.chart && this.chart.resize());
                }
                return;
            }
            // new data on the same canvas: update in place (destroy + new on one canvas could leave it blank)
            this.key = key;
            this.chart.data = config.data;
            this.chart.options = config.options;
            this.chart.update();
            return;
        }
        if (this.chart) {
            this.chart.destroy();
        }
        this.key = key;
        this.height = this.props.height;
        this.chart = new window.Chart(this.canvas.el, config);
    }
}
