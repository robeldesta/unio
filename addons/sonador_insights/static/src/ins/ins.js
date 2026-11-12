import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const money = (v) => Number(v || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });

/** HR analytics and pulse surveys. */
export class InsightsApp extends Component {
    static template = "sonador_insights.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({ ctx: null, page: p.page || "analytics", busy: false, d: null, sv: null, edit: null, take: null, answers: {}, res: null });
        onWillStart(async () => {
            this.state.sv = await this.orm.call("sb.insights", "surveys", []);
            if (!this.state.sv.is_hr) {
                this.state.page = "surveys";
            }
            await this.load();
        });
    }
    money(v) {
        return money(v);
    }
    async go(page) {
        this.state.page = page;
        this.state.res = null;
        this.state.take = null;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "analytics") {
                s.d = await this.orm.call("sb.insights", "dashboard", []);
            } else {
                s.sv = await this.orm.call("sb.insights", "surveys", []);
            }
        } finally {
            s.busy = false;
        }
    }
    pairs(rows) {
        return { labels: rows.map((r) => r[0]), series: [{ label: "People", data: rows.map((r) => r[1]) }] };
    }
    get headChart() {
        const t = this.state.d.trend;
        return { labels: t.labels, series: [{ label: "Headcount", data: t.head }] };
    }
    get flowChart() {
        const t = this.state.d.trend;
        return { labels: t.labels, series: [{ label: "Joined", data: t.hires }, { label: "Left", data: t.leavers }] };
    }
    get costChart() {
        const c = this.state.d.cost;
        return { labels: c.labels, series: [{ label: "Payroll cost (gross + employer pension)", data: c.cost }, { label: "Overtime", data: c.overtime }] };
    }

    // surveys
    newSurvey() {
        this.state.edit = { id: false, name: "", close: "" };
    }
    async saveSurvey(state) {
        const e = this.state.edit;
        this.state.sv = await this.orm.call("sb.insights", "survey_save", [{ ...e, ...(state ? { state } : {}) }]);
        this.state.edit = null;
        this.notification.add(state === "open" ? "Survey opened: everyone can answer now." : "Saved.", { type: "success" });
    }
    async setState(sv, state) {
        this.state.sv = await this.orm.call("sb.insights", "survey_save", [{ id: sv.id, name: sv.name, state }]);
    }
    startTake(sv) {
        this.state.take = sv;
        this.state.answers = {};
    }
    setAnswer(qid, v) {
        this.state.answers[qid] = v;
    }
    async submit() {
        this.state.sv = await this.orm.call("sb.insights", "take", [this.state.take.id, this.state.answers]);
        this.state.take = null;
        this.notification.add("Thank you. Your answers are anonymous.", { type: "success" });
    }
    async results(sv) {
        this.state.res = await this.orm.call("sb.insights", "results", [sv.id]);
    }
}

registry.category("actions").add("sonador_insights.app", InsightsApp);
