import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Budgets: a year's budget per account and month, beside last year's and this year's actuals. */
export class BudgetApp extends Component {
    static template = "sonador_budget.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ ctx: null, fy: null, data: null, growth: 10, dirty: false, busy: false, show: "budget" });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.budgets", "ctx", []);
            await this.open(this.state.ctx.fy);
        });
    }
    money(v) {
        return v ? Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "";
    }
    async open(fy) {
        this.state.fy = fy;
        this.state.busy = true;
        try {
            this.state.data = await this.orm.call("sb.budgets", "load", [fy]);
            this.state.dirty = false;
        } finally {
            this.state.busy = false;
        }
    }
    get rows() {
        return this.state.data ? this.state.data.rows : [];
    }
    get locked() {
        return this.state.data && this.state.data.state === "approved";
    }
    rowTotal(r, key = "budget") {
        return r[key].reduce((a, b) => a + (Number(b) || 0), 0);
    }
    colTotal(i, group, key = "budget") {
        return this.rows.filter((r) => r.group === group).reduce((a, r) => a + (Number(r[key][i]) || 0), 0);
    }
    groupTotal(group, key = "budget") {
        return this.rows.filter((r) => r.group === group).reduce((a, r) => a + this.rowTotal(r, key), 0);
    }
    profit(i, key = "budget") {
        return i === null
            ? this.groupTotal("income", key) - this.groupTotal("expense", key)
            : this.colTotal(i, "income", key) - this.colTotal(i, "expense", key);
    }
    edit(r, i, ev) {
        const v = Number(String(ev.target.value).replace(/,/g, "")) || 0;
        r.budget[i] = v;
        this.state.dirty = true;
    }
    spread(r) {
        const total = this.rowTotal(r);
        const each = Math.round(total / 12 / 1000) * 1000;
        r.budget = r.budget.map((_v, i) => (i < 11 ? each : total - each * 11));
        this.state.dirty = true;
    }
    async save() {
        this.state.data = await this.orm.call("sb.budgets", "save", [this.state.fy, this.rows.map((r) => ({ account_id: r.account_id, budget: r.budget })), this.state.data.name]);
        this.state.dirty = false;
        this.notification.add("Budget saved", { type: "success" });
    }
    async fill() {
        this.state.data = await this.orm.call("sb.budgets", "fill", [this.state.fy, this.state.growth]);
        this.notification.add(`Filled from last year's actuals + ${this.state.growth}%`, { type: "success" });
    }
    async setState(s) {
        if (this.state.dirty) {
            await this.save();
        }
        this.state.data = await this.orm.call("sb.budgets", "set_state", [this.state.fy, s]);
    }
}

registry.category("actions").add("sonador_budget.app", BudgetApp);
