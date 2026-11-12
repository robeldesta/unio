import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Currencies: daily rates (fetched or typed) and the month-end revaluation of foreign-currency items. */
export class FxApp extends Component {
    static template = "sonador_books.Fx";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.state = useState({ ctx: null, day: "", reval: null, newRate: {}, busy: false });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.fx", "ctx", []);
            this.state.day = this.state.ctx.month_end;
            await this.preview();
        });
    }
    money(v, d = 0) {
        return Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
    }
    async run(fn) {
        this.state.busy = true;
        try {
            await fn();
        } finally {
            this.state.busy = false;
        }
    }
    async fetchNow() {
        await this.run(async () => {
            this.state.ctx = await this.orm.call("sb.fx", "fetch_now", []);
            this.notification.add("Rates updated", { type: "success" });
        });
    }
    async toggleAuto(ev) {
        this.state.ctx = await this.orm.call("sb.fx", "set_auto", [ev.target.checked]);
    }
    async saveRate(c) {
        const r = this.state.newRate[c.id] || {};
        this.state.ctx = await this.orm.call("sb.fx", "set_rate", [c.id, r.date || this.state.ctx.today, r.value]);
        this.state.newRate[c.id] = {};
        this.notification.add(`${c.name} rate saved`, { type: "success" });
    }
    async preview() {
        await this.run(async () => {
            this.state.reval = await this.orm.call("sb.fx", "revaluation", [this.state.day]);
        });
    }
    async post() {
        await this.run(async () => {
            this.state.reval = await this.orm.call("sb.fx", "post_revaluation", [this.state.day]);
            this.notification.add("Revaluation posted; it reverses on the next day", { type: "success" });
        });
    }
    openVoucher(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: id, views: [[false, "form"]] });
    }
}

registry.category("actions").add("sonador_books.fx", FxApp);
