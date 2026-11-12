import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const STATE = { draft: "Not scheduled", planned: "Planned", running: "Running", paused: "Paused", done: "Done", cancel: "Cancelled" };

/** Production: board (machines × days), production order run from the floor, shortages, analytics, setup. */
export class FactoryApp extends Component {
    static template = "sonador_factory.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        const today = new Date();
        const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        const from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 13);
        this.state = useState({
            ctx: null, page: p.page || "board", busy: false,
            board: null, start: false, drag: null,
            ord: null, edit: null, rec: null, mats: null,
            short: null, ssel: {},
            an: null, af: { from: iso(from), to: iso(today), line: "" },
            set: null, setTab: "lines", setEdit: null,
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.factory", "ctx", []);
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    stateLabel(s) {
        return STATE[s] || s;
    }
    n(v, d = 0) {
        return v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });
    }
    async go(page) {
        this.state.page = page;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "board") {
                s.board = await this.orm.call("sb.factory", "board", [s.start || false, 7]);
            } else if (s.page === "materials") {
                s.short = await this.orm.call("sb.factory", "shortages", []);
                s.ssel = {};
            } else if (s.page === "analytics") {
                s.an = await this.orm.call("sb.factory", "analytics", [s.af.from, s.af.to, s.af.line || false]);
            } else if (s.page === "setup") {
                s.set = await this.orm.call("sb.factory", "setup", []);
            }
        } finally {
            s.busy = false;
        }
    }

    // ------------------------------------------------------------------ board
    async shift(dir) {
        this.state.start = dir < 0 ? this.state.board.prev : this.state.board.next;
        await this.load();
    }
    async thisWeek() {
        this.state.start = false;
        await this.load();
    }
    cell(lineId, date) {
        return this.state.board.orders.filter((o) => o.line_id === lineId && o.date === date);
    }
    get unscheduled() {
        return this.state.board.orders.filter((o) => !o.line_id || !o.date);
    }
    dragStart(o) {
        this.state.drag = o;
    }
    noop() {}
    async dropOn(lineId, date) {
        const o = this.state.drag;
        this.state.drag = null;
        if (!o || (o.line_id === lineId && o.date === date)) {
            return;
        }
        await this.orm.call("sb.factory", "move", [o.id, lineId || false, date || false]);
        await this.load();
    }

    // ------------------------------------------------------------------ order
    async openOrder(id) {
        this.state.ord = await this.orm.call("sb.factory", "order", [id]);
        this.state.edit = null;
        this.resetRec();
        this.state.mats = this.state.ord.materials.map((m) => ({ id: m.id, consumed: m.consumed || "", lots: m.lots }));
    }
    newOrder(lineId, date) {
        const p = this.ctx.products[0];
        this.state.ord = { id: false, materials: [], entries: [], checks: [], summary: {} };
        this.state.edit = { product_id: p ? p.id : "", qty_planned: "", line_id: lineId || "", date_planned: date || this.ctx.today,
            shift_id: "", lot_name: "", priority: false, note: "" };
    }
    editOrder() {
        const o = this.state.ord;
        this.state.edit = { product_id: o.product_id, qty_planned: o.qty, line_id: o.line_id || "", date_planned: o.date || "",
            shift_id: o.shift_id || "", lot_name: o.lot, priority: o.priority, note: o.note };
    }
    setE(k, v) {
        this.state.edit[k] = v;
    }
    async saveOrder() {
        this.state.ord = await this.orm.call("sb.factory", "save_order", [this.state.ord.id || false, this.state.edit]);
        this.state.edit = null;
        this.state.mats = this.state.ord.materials.map((m) => ({ id: m.id, consumed: m.consumed || "", lots: m.lots }));
        this.resetRec();
        this.notification.add(`${this.state.ord.name} saved.`, { type: "success" });
        await this.load();
    }
    cancelEdit() {
        if (!this.state.ord.id) {
            this.state.ord = null;
        }
        this.state.edit = null;
    }
    async closeOrder() {
        this.state.ord = null;
        await this.load();
    }
    async act(action) {
        try {
            this.state.ord = await this.orm.call("sb.factory", "set_state", [this.state.ord.id, action, false]);
        } catch (e) {
            if (action === "start" && e.data && /Start anyway/.test(e.data.message || "")) {
                if (window.confirm(e.data.message + "\n\nStart now?")) {
                    this.state.ord = await this.orm.call("sb.factory", "set_state", [this.state.ord.id, action, true]);
                }
                return;
            }
            throw e;
        }
    }
    resetRec() {
        const shifts = this.ctx.shifts;
        const hour = new Date().getHours();
        const cur = shifts.find((s) => (s.from < s.to ? hour >= s.from && hour < s.to : hour >= s.from || hour < s.to)) || shifts[0];
        this.state.rec = { date: this.ctx.today, shift_id: cur ? cur.id : "", employee_id: "", minutes_run: "", good_qty: "", note: "",
            waste: [{ reason_id: "", qty: "", in_kg: false }], down: [{ reason_id: "", minutes: "" }] };
    }
    setR(k, v) {
        this.state.rec[k] = v;
    }
    setRow(row, k, v) {
        row[k] = v;
        if (k === "reason_id") {
            const r = this.ctx.reasons.find((x) => x.id === Number(v));
            if (r && /\(kg\)/.test(r.name)) {
                row.in_kg = true;
            }
        }
    }
    addLoss(kind) {
        this.state.rec[kind].push(kind === "waste" ? { reason_id: "", qty: "", in_kg: false } : { reason_id: "", minutes: "" });
    }
    get wasteReasons() {
        return this.ctx.reasons.filter((r) => r.kind === "waste");
    }
    get downReasons() {
        return this.ctx.reasons.filter((r) => r.kind === "downtime");
    }
    get autoMinutes() {
        const s = this.ctx.shifts.find((x) => x.id === Number(this.state.rec.shift_id));
        const down = this.state.rec.down.reduce((a, d) => a + (Number(d.minutes) || 0), 0);
        return Math.max((s ? s.minutes : 480) - down, 0);
    }
    async saveRec() {
        const r = this.state.rec;
        const losses = [...r.waste.map((w) => ({ ...w })), ...r.down.map((d) => ({ ...d }))];
        this.state.ord = await this.orm.call("sb.factory", "record", [this.state.ord.id, {
            date: r.date, shift_id: r.shift_id, employee_id: r.employee_id, minutes_run: r.minutes_run === "" ? null : r.minutes_run,
            good_qty: r.good_qty, note: r.note, losses }]);
        this.resetRec();
        this.notification.add("Shift recorded.", { type: "success" });
    }
    async removeEntry(e) {
        this.state.ord = await this.orm.call("sb.factory", "remove_entry", [e.id]);
    }
    setM(i, k, v) {
        this.state.mats[i][k] = v;
    }
    async saveMats() {
        this.state.ord = await this.orm.call("sb.factory", "save_materials", [this.state.ord.id, this.state.mats]);
        this.state.mats = this.state.ord.materials.map((m) => ({ id: m.id, consumed: m.consumed || "", lots: m.lots }));
        this.notification.add("Material use saved.", { type: "success" });
    }
    fillExpected() {
        this.state.ord.materials.forEach((m, i) => {
            if (!this.state.mats[i].consumed) {
                this.state.mats[i].consumed = m.expected;
            }
        });
    }
    async saveCheck(c, val) {
        this.state.ord = await this.orm.call("sb.factory", "save_check", [c.id, c.kind === "number" ? { value: val } : { result: val }]);
    }
    async finish() {
        const o = this.state.ord;
        const noUse = o.materials.length && !o.materials.some((m) => m.consumed);
        let useExpected = false;
        if (noUse) {
            if (!window.confirm("No material use is entered. Post the recipe quantities for the output made?")) {
                return;
            }
            useExpected = true;
        } else if (!window.confirm(`Finish ${o.name} and post ${this.n(o.good)} ${o.uom} to stock and the books?`)) {
            return;
        }
        this.state.ord = await this.orm.call("sb.factory", "finish", [o.id, useExpected]);
        this.notification.add(`${o.name} finished: ${this.state.ord.voucher.name} posted.`, { type: "success" });
    }
    openVoucher() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.stock.voucher", res_id: this.state.ord.voucher.id, views: [[false, "form"]] });
    }
    lineName(id) {
        return (this.ctx.lines.find((l) => l.id === id) || {}).name || "";
    }

    // ------------------------------------------------------------------ materials
    toggleS(id) {
        this.state.ssel[id] = !this.state.ssel[id];
    }
    get sChosen() {
        return Object.keys(this.state.ssel).filter((k) => this.state.ssel[k]).map(Number);
    }
    async buy() {
        const names = await this.orm.call("sb.factory", "request_purchase", [this.sChosen]);
        this.notification.add(`Draft purchase orders: ${names.join(", ")}.`, { type: "success", sticky: true });
        await this.load();
    }

    // ------------------------------------------------------------------ analytics
    async setAf(k, v) {
        this.state.af[k] = v;
        await this.load();
    }
    get dayChart() {
        const d = this.state.an.days;
        return { labels: d.map((x) => x.label), series: [{ label: "Good output", data: d.map((x) => x.good) }] };
    }
    get downChart() {
        const d = this.state.an.downtime.slice(0, 8);
        return { labels: d.map((x) => x.reason), series: [{ label: "Minutes stopped", data: d.map((x) => x.minutes) }] };
    }
    get oeeChart() {
        const l = this.state.an.lines;
        return { labels: l.map((x) => x.name), series: [{ label: "Availability %", data: l.map((x) => x.availability) },
            { label: "Performance %", data: l.map((x) => x.performance) }, { label: "Quality %", data: l.map((x) => x.quality) },
            { label: "OEE %", data: l.map((x) => x.oee) }] };
    }

    // ------------------------------------------------------------------ setup
    editSetup(kind, rec) {
        const blank = { line: { name: "", code: "", rated_speed: "", speed_unit: "", labour_rate: "", overhead_rate: "", crew: 1 },
            shift: { name: "", hour_from: 6, hour_to: 14 }, reason: { name: "", kind: "downtime", planned: false },
            check: { name: "", kind: "number", min_value: "", max_value: "", unit: "", blocking: true, products: [] } }[kind];
        this.state.setEdit = { kind, vals: rec ? { ...rec } : { ...blank } };
    }
    setS(k, v) {
        this.state.setEdit.vals[k] = v;
    }
    toggleCheckProduct(pid) {
        const l = this.state.setEdit.vals.products;
        const i = l.indexOf(pid);
        if (i >= 0) {
            l.splice(i, 1);
        } else {
            l.push(pid);
        }
    }
    async saveSetup() {
        const e = this.state.setEdit;
        this.state.set = await this.orm.call("sb.factory", "save_setup", [e.kind, e.vals]);
        this.state.ctx = await this.orm.call("sb.factory", "ctx", []);
        this.state.setEdit = null;
    }
    async archiveSetup(kind, id) {
        this.state.set = await this.orm.call("sb.factory", "archive_setup", [kind, id]);
        this.state.ctx = await this.orm.call("sb.factory", "ctx", []);
    }
    async setWeight(p, v) {
        await this.orm.call("sb.factory", "set_weight", [p.id, v]);
        p.weight = Number(v);
        this.notification.add(`${p.name}: weight saved.`, { type: "success" });
    }
}

registry.category("actions").add("sonador_factory.app", FactoryApp);
