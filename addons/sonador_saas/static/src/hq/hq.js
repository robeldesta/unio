/** @odoo-module **/
import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const fmt = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));

/** Unio HQ: money, sign-ups and leads, every customer's health, support, and the server, on one screen. */
export class Hq extends Component {
    static template = "sonador_saas.Hq";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.fmt = fmt;
        this.markup = markup;
        this.state = useState({ d: null, tab: "overview", busy: false, q: "", sort: "health", answers: {},
            aiKey: "", aiCaps: null, aiClient: { name: "", database: "" }, aiToken: "" });
        onWillStart(() => this.load());
    }
    async load() {
        this.state.d = await this.orm.call("saas.hq", "hq_data", []);
    }
    async measure() {
        this.state.busy = true;
        try {
            await this.orm.call("saas.hq", "measure", []);
            await this.orm.call("saas.hq", "ingest", []);
            await this.load();
            this.notification.add("Measured every company.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
    // charts drawn as plain SVG
    bars(series, key = "value", h = 150) {
        const max = Math.max(1, ...series.map((s) => s[key] || 0));
        const w = 100 / Math.max(series.length, 1);
        return series.map((s, i) => ({ x: i * w + w * 0.15, w: w * 0.7, h: ((s[key] || 0) / max) * (h - 24), y: h - 20 - ((s[key] || 0) / max) * (h - 24), label: s.label, value: s[key] || 0, cx: i * w + w / 2 }));
    }
    line(series, key, h = 120) {
        if (!series.length) return "";
        const max = 100;
        const step = 100 / Math.max(series.length - 1, 1);
        return series.map((s, i) => `${(i * step).toFixed(2)},${(h - ((s[key] || 0) / max) * h).toFixed(2)}`).join(" ");
    }
    healthClass(h) {
        return h === null || h === undefined ? "" : h >= 60 ? "good" : h >= 25 ? "mid" : "bad";
    }
    get customers() {
        const q = this.state.q.toLowerCase();
        const rows = this.state.d.customers.filter((c) => !q || (c.name + " " + c.url + " " + c.industry + " " + c.country).toLowerCase().includes(q));
        const k = this.state.sort;
        return rows.sort((a, b) => (k === "name" ? a.name.localeCompare(b.name) : this.dash(b[k], -1) - this.dash(a[k], -1)));
    }
    funnelPct(i) {
        const f = this.state.d.funnel;
        return i === 0 || !f[0].n ? 100 : Math.round((100 * f[i].n) / f[0].n);
    }
    openTenant(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "saas.tenant", res_id: id, views: [[false, "form"]] });
    }
    openModel(model, name) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, name, views: [[false, "list"], [false, "form"]] });
    }
    async confirmPayment(p) {
        await this.orm.call("saas.payment", "action_confirm", [[p.id]]);
        this.notification.add(`${p.company}: payment confirmed, subscription active.`, { type: "success" });
        await this.load();
    }
    async rejectPayment(p) {
        await this.orm.call("saas.payment", "action_reject", [[p.id]]);
        await this.load();
    }
    async answer(t) {
        const text = this.state.answers[t.id];
        if (!text) return;
        this.state.d.support = await this.orm.call("saas.ticket", "hq_answer", [t.id, text]);
        this.state.answers[t.id] = "";
        this.notification.add("Answer sent: in their help panel and by email.", { type: "success" });
    }
    // ---------------------------------------------------------------- Unio AI
    get aiData() {
        return this.state.d.support.ai;
    }
    aiCapsEdit() {
        const a = this.aiData;
        this.state.aiCaps = { week: a.week_cap, standard: a.caps.standard, pro: a.caps.pro, max: a.caps.max, user_share: a.user_share };
    }
    async aiSaveKey() {
        this.state.d.support.ai = await this.orm.call("saas.ai", "hq_set_key", [this.state.aiKey]);
        this.state.aiKey = "";
        this.notification.add(this.aiData.enabled ? "Key saved. Unio AI is on." : "Key removed. Unio AI is off.", { type: "success" });
    }
    async aiTest() {
        await this.orm.call("saas.ai", "hq_test", []);
        this.notification.add("The key works.", { type: "success" });
        await this.load();
    }
    async aiSaveCaps() {
        const c = this.state.aiCaps;
        this.state.d.support.ai = await this.orm.call("saas.ai", "hq_set_caps", [c.week, { standard: c.standard, pro: c.pro, max: c.max }, c.user_share]);
        this.state.aiCaps = null;
        this.notification.add("Caps saved.", { type: "success" });
    }
    async aiFiles(on) {
        this.state.d.support.ai = await this.orm.call("saas.ai", "hq_set_files", [on]);
        this.notification.add(on ? "Files and photos are on in Unio AI." : "Files and photos are off in Unio AI.", { type: "success" });
    }
    async aiPlan(c, plan) {
        this.state.d.support.ai = await this.orm.call("saas.ai", "hq_set_plan", [c.kind, c.id, plan]);
    }
    async aiAddClient() {
        const n = this.state.aiClient;
        const r = await this.orm.call("saas.ai", "hq_add_client", [n.name, n.database]);
        this.state.d.support.ai = r.summary;
        this.state.aiToken = r.token;
        this.state.aiClient = { name: "", database: "" };
    }
    async copyToken() {
        try {
            await navigator.clipboard.writeText(this.state.aiToken);
            this.notification.add("Copied.", { type: "success" });
        } catch {
            this.notification.add("Select the token and copy it.", { type: "warning" });
        }
    }
    async eventDone(e) {
        await this.orm.call("saas.hq", "event_done", [e.id]);
        e.done = true;
    }
    dash(v, empty = "–") {
        return v === null || v === undefined ? empty : v;
    }
    entries(o) {
        return Object.entries(o || {});
    }
    weeksMax() {
        const w = this.state.d.funnel_weeks;
        return Math.max(1, ...w.map((x) => x.forms), ...w.map((x) => x.systems));
    }
    pct(v) {
        return v === null || v === undefined ? "–" : `${Math.round(v)}%`;
    }
    gaugeClass(v) {
        return v >= 85 ? "bad" : v >= 70 ? "mid" : "good";
    }
}

registry.category("actions").add("sonador_saas.hq", Hq);
