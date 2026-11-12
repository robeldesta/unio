import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE = { draft: "To confirm", running: "Depreciating", done: "Fully depreciated", disposed: "Disposed", expensed: "Expensed when bought" };

/** Fixed assets: register, an asset with its schedule, the depreciation run, disposal, control against the ledger, classes. */
export class FixedAssetsApp extends Component {
    static template = "sonador_assets.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.state = useState({ ctx: null, page: "list", data: null, f: { state: "", category_id: "", search: "" }, asset: null,
            edit: null, dispose: null, runMonth: "", catEdit: null, busy: false, control: null, settings: null, link: {}, tax: null, cap: null, capF: { date_from: "", date_to: "", search: "", min_amount: "" }, capSel: {}, capCat: "" });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.assets", "ctx", []);
            this.state.runMonth = this.state.ctx.last_period_end;
            this.state.settings = { frequency: this.state.ctx.frequency, threshold: this.state.ctx.threshold || 0 };
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    money(v) {
        return v === null || v === undefined ? "–" : (Math.round(Number(v)) || 0).toLocaleString("en-US");
    }
    stateLabel(s) {
        return STATE[s] || s;
    }
    async load() {
        this.state.busy = true;
        try {
            this.state.data = await this.orm.call("sb.assets", "overview", [this.state.f]);
        } finally {
            this.state.busy = false;
        }
    }
    async setF(k, v) {
        this.state.f[k] = v;
        await this.load();
    }
    async open(id) {
        this.state.asset = await this.orm.call("sb.assets", "asset", [id]);
        this.state.page = "asset";
        this.state.edit = null;
        this.state.dispose = null;
    }
    back() {
        this.state.page = "list";
        this.state.asset = null;
        this.load();
    }
    newAsset() {
        const c = this.ctx.categories[0] || {};
        this.state.page = "asset";
        this.state.asset = null;
        this.state.edit = { id: false, name: "", category_id: c.id || "", cost: "", salvage: 0, date_acquired: this.ctx.today, date_start: this.ctx.today,
            method: c.method || "straight", life_months: c.life_months || 60, rate: c.rate || 0, opening_depreciation: 0, serial: "", location: "",
            tax_method: c.tax_method || c.method || "straight", tax_rate: c.tax_rate || 0, tax_life_months: c.tax_life_months || 0, tax_opening: 0,
            custodian_id: "", note: "" };
    }
    editAsset() {
        const a = this.state.asset;
        this.state.edit = { id: a.id, name: a.name, category_id: a.category_id, cost: a.cost, salvage: a.salvage, date_acquired: a.acquired,
            date_start: a.start, method: a.method, life_months: a.life_months, rate: a.rate, opening_depreciation: a.opening_depreciation,
            serial: a.serial, location: a.location, custodian_id: a.custodian_id, note: a.note, tax_method: a.tax_method, tax_rate: a.tax_rate,
            tax_life_months: a.tax_life_months, tax_opening: a.tax_opening };
    }
    setE(k, v) {
        const e = this.state.edit;
        e[k] = v;
        if (k === "category_id") {
            const c = this.ctx.categories.find((x) => x.id === Number(v)) || {};
            Object.assign(e, { method: c.method, life_months: c.life_months, rate: c.rate, tax_method: c.tax_method, tax_rate: c.tax_rate,
                tax_life_months: c.tax_life_months });
        }
    }
    async saveAsset() {
        const e = this.state.edit;
        this.state.asset = await this.orm.call("sb.assets", "save", [e.id || false, e]);
        this.state.edit = null;
        this.notification.add(`${this.state.asset.code} saved.`, { type: "success" });
    }
    async confirm(ids) {
        await this.orm.call("sb.assets", "confirm", [ids]);
        this.notification.add(ids.length > 1 ? `${ids.length} assets confirmed.` : "Confirmed: depreciation starts.", { type: "success" });
        if (this.state.asset) {
            await this.open(this.state.asset.id);
        } else {
            await this.load();
        }
    }
    async confirmAll() {
        await this.confirm(this.state.data.rows.filter((r) => r.state === "draft").map((r) => r.id));
    }
    async run() {
        try {
            const r = await this.orm.call("sb.assets", "run", [this.state.runMonth]);
            this.notification.add(`${r.voucher}: depreciation ${this.money(r.amount)} on ${r.assets} assets posted.`, { type: "success" });
            this.state.ctx = await this.orm.call("sb.assets", "ctx", []);
            await this.load();
        } catch (e) {
            throw e;
        }
    }
    startDispose() {
        this.state.dispose = { day: this.ctx.today, amount: "", account_id: "" };
    }
    async doDispose() {
        const d = this.state.dispose;
        this.state.asset = await this.orm.call("sb.assets", "dispose", [this.state.asset.id, d.day, d.amount, d.account_id || false]);
        this.state.dispose = null;
        this.notification.add("Disposed: the entry is posted.", { type: "success" });
    }
    openSource() {
        this.action.doAction("sonador_books.action_sb_report_center");
    }
    accountsOf(type) {
        return this.ctx.accounts.filter((a) => !type || a.type === type || (Array.isArray(type) && type.includes(a.type)));
    }
    async expenseIt() {
        this.state.asset = await this.orm.call("sb.assets", "expense_it", [this.state.asset.id]);
        this.notification.add("Expensed: the cost moved to the expensed items account; it stays on the register.", { type: "success" });
    }
    async showControl() {
        this.state.page = "control";
        this.state.control = await this.orm.call("sb.assets", "control", []);
    }
    async fromLine(id) {
        this.state.control = await this.orm.call("sb.assets", "from_line", [id]);
        this.notification.add("On the register: confirm it there.", { type: "success" });
    }
    async linkLine(id) {
        const ids = this.state.link[id] || [];
        if (!ids.length) {
            return;
        }
        this.state.control = await this.orm.call("sb.assets", "link_line", [id, ids]);
        this.state.link[id] = [];
    }
    pickLink(id, ev) {
        this.state.link[id] = Array.from(ev.target.selectedOptions).map((o) => Number(o.value));
    }
    openVoucher(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.voucher", res_id: id, views: [[false, "form"]] });
    }
    async showTax() {
        this.state.page = "tax";
        this.state.tax = await this.orm.call("sb.assets", "book_vs_tax", []);
    }
    async postDeferred() {
        const r = await this.orm.call("sb.assets", "post_deferred_tax", [this.state.tax.date]);
        this.state.tax = r;
        this.notification.add(`${r.posted}: deferred tax posted.`, { type: "success" });
    }
    async showCap() {
        this.state.page = "capitalise";
        this.state.capSel = {};
        this.state.capCat = this.state.capCat || (this.ctx.categories[0] || {}).id || "";
        this.state.cap = await this.orm.call("sb.assets", "capitalise_candidates", [this.state.capF]);
        if (!this.state.capF.min_amount) {
            this.state.capF.min_amount = this.state.cap.min_amount;
        }
    }
    async setCapF(k, v) {
        this.state.capF[k] = v;
        this.state.cap = await this.orm.call("sb.assets", "capitalise_candidates", [this.state.capF]);
    }
    get capIds() {
        return Object.keys(this.state.capSel).filter((k) => this.state.capSel[k]).map(Number);
    }
    async doCapitalise() {
        const r = await this.orm.call("sb.assets", "capitalise", [this.capIds, Number(this.state.capCat)]);
        this.notification.add(`${r.assets.length} capitalised: on the register and depreciating.`, { type: "success" });
        await this.showCap();
    }
    async saveSettings() {
        this.state.ctx = await this.orm.call("sb.assets", "save_settings", [this.state.settings]);
        this.notification.add("Saved.", { type: "success" });
    }
    editCat(c) {
        this.state.catEdit = c ? { ...c } : { id: false, name: "", asset_id: "", dep_id: "", exp_id: "", expensed_id: "", method: "straight", life_months: 60, rate: 0, salvage_pct: 0,
            tax_method: "declining", tax_rate: 20, tax_life_months: 0 };
        this.state.page = "categories";
    }
    async saveCat() {
        this.state.ctx = await this.orm.call("sb.assets", "save_category", [this.state.catEdit]);
        this.state.catEdit = null;
    }
}

registry.category("actions").add("sonador_assets.app", FixedAssetsApp);
