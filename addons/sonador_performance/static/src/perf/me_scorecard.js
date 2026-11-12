/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const fmt = (v, d = 0) =>
    v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

/** "My scorecard" card in Me: this period's KPI results, the trend and my key results. */
export class MeScorecard extends Component {
    static template = "sonador_performance.MeScorecard";
    static props = ["*"];

    setup() {
        this.action = useService("action");
        this.fmt = fmt;
    }
    get d() {
        return this.props.data || {};
    }
    pctClass(p) {
        if (p === null || p === undefined) {
            return "text-muted";
        }
        return p >= 85 ? "text-success" : p >= 70 ? "text-warning" : "text-danger";
    }
    bar(p) {
        return `width: ${Math.max(2, Math.min(100, p || 0))}%`;
    }
    open() {
        return this.action.doAction({ type: "ir.actions.client", tag: "sonador_performance.app",
            params: { page: "scorecards", employee_id: this.d.employee_id } });
    }
    openGoals() {
        return this.action.doAction({ type: "ir.actions.client", tag: "sonador_performance.app", params: { page: "goals" } });
    }
}

registry.category("sonador_me.sections").add("scorecard", MeScorecard);
