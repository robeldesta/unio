import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const money = (v) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const plus14 = () => {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    return d.toISOString().slice(0, 10);
};
const SEV_HELP = {
    high: "Money at risk, or a control that does not work. Fix now.",
    medium: "Needs fixing this month.",
    low: "Good practice. Fix when convenient.",
};

/** Audit Room: an internal auditor's visit from arrival to report, and management's answers to findings. */
export class AuditRoom extends Component {
    static template = "sonador_audit.Room";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            d: null, tab: "visit", busy: false, open: {}, form: null, report: null,
            end: { verdict: "", summary: "" }, answers: {}, notes: {}, recheckNotes: {}, add: { name: "", email: "" },
            tick: 0,
        });
        this.sevHelp = SEV_HELP;
        onWillStart(() => this.load());
        this.timer = setInterval(() => (this.state.tick += 1), 60000);
        onWillUnmount(() => clearInterval(this.timer));
    }
    money(v) {
        return money(v);
    }
    get d() {
        return this.state.d;
    }
    get v() {
        return this.state.d && this.state.d.visit;
    }
    async call(method, args = [], kw = {}) {
        this.state.busy = true;
        try {
            return await this.orm.call("sb.audit.room", method, args, kw);
        } finally {
            this.state.busy = false;
        }
    }
    async load() {
        this.set(await this.call("home"));
    }
    set(d) {
        this.state.d = d;
        const tabs = this.tabs().map((t) => t.key);
        if (!tabs.includes(this.state.tab)) {
            this.state.tab = tabs[0];
        }
    }
    tabs() {
        const d = this.state.d;
        if (!d) {
            return [];
        }
        const out = [];
        if (d.is_auditor) {
            out.push({ key: "visit", label: d.visit ? "This visit" : "Start" });
        }
        if (d.mine.length || !d.is_auditor) {
            out.push({ key: "mine", label: "For you", badge: d.mine.length });
        }
        out.push({ key: "findings", label: "Open findings", badge: d.findings.length });
        out.push({ key: "visits", label: "Past visits" });
        if (d.is_manager) {
            out.push({ key: "auditors", label: "Auditors" });
        }
        return out;
    }
    elapsed() {
        this.state.tick; // re-render every minute
        if (!this.v) {
            return "";
        }
        const mins = Math.max(0, Math.round((Date.now() - new Date(this.v.started_iso.replace(" ", "T") + "Z")) / 60000));
        const h = Math.floor(mins / 60);
        return h ? `${h}h ${String(mins % 60).padStart(2, "0")}m` : `${mins} min`;
    }
    greeting() {
        const h = new Date().getHours();
        return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    }
    firstName() {
        return (this.d.user || "").split(" ")[0];
    }

    // ---------------------------------------------------------------- visit
    async start() {
        this.set(await this.call("start_visit"));
        this.state.tab = "visit";
        this.notification.add("Visit started. The checks show what changed since the last visit.", { type: "success" });
    }
    async refresh() {
        this.set(await this.call("refresh", [this.v.id]));
        this.notification.add("Checks worked out again from the books.", { type: "info" });
    }
    toggle(id) {
        this.state.open[id] = !this.state.open[id];
    }
    async mark(c, state) {
        const res = await this.call("set_check", [c.id], { state, note: this.state.notes[c.id] ?? c.note });
        Object.assign(c, res);
        if (state === "issue") {
            this.newFinding(c);
        }
    }
    async saveNote(c) {
        const note = this.state.notes[c.id];
        if (note === undefined || note === c.note) {
            return;
        }
        Object.assign(c, await this.call("set_check", [c.id], { note }));
    }
    async count(c, ev) {
        const raw = (ev.target.value || "").replace(/,/g, "").trim();
        if (raw === "") {
            return;
        }
        Object.assign(c, await this.call("set_check", [c.id], { counted: parseFloat(raw) }));
    }
    async look(c) {
        const act = await this.orm.call("sb.audit.room", "open_check", [c.id]);
        this.action.doAction(act);
    }
    checkGroups() {
        const all = this.v.checks;
        return [
            { key: "found", label: "Something to look at", items: all.filter((c) => !c.cash && c.count > 0) },
            { key: "cash", label: "Cash count", items: all.filter((c) => c.cash) },
            { key: "clear", label: "Nothing found", items: all.filter((c) => !c.cash && !c.count) },
        ].filter((g) => g.items.length);
    }
    async allClearOk() {
        for (const c of this.v.checks.filter((x) => !x.cash && !x.count && x.state === "todo")) {
            Object.assign(c, await this.orm.call("sb.audit.room", "set_check", [c.id], { state: "ok" }));
        }
    }

    // ---------------------------------------------------------------- findings
    newFinding(check) {
        const vouchers = check && check.items.length && check.can_open
            ? check.items.slice(0, 10).map((x) => x.split(" · ")[0]).filter((n) => /\d/.test(n)).join(", ") : "";
        this.state.form = {
            id: false, title: check ? check.title : "", area: "other", severity: "medium", found: "",
            recommendation: "", amount: check && check.count ? check.amount : "", vouchers: check && check.key !== "below_zero" && check.key !== "bank_rec" ? vouchers : "",
            owner_id: "", due: plus14(), check_key: check ? check.key : "",
        };
        if (check) {
            const area = { "Cash and bank": "cash", "Purchases and payables": "purchases", "Journals and adjustments": "journals",
                "Controls and access": "controls" }[check.area];
            this.state.form.area = area || "other";
        }
    }
    cashFinding(c) {
        const short = c.diff < 0;
        this.state.form = {
            id: false, title: `${c.title.replace("Count the cash: ", "")} ${short ? "short" : "over"} by ${money(Math.abs(c.diff))}`,
            area: "cash", severity: short ? "high" : "medium",
            found: `Counted ${money(c.counted)} in the box on ${new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}. `
                + `The books show ${money(c.book)}. Difference ${money(c.diff)}.`,
            recommendation: short ? "Find the missing amount with the cashier; record any expense that was not entered." : "Find what the extra cash is for and record it.",
            amount: Math.abs(c.diff), vouchers: "", owner_id: "", due: plus14(), check_key: c.key,
        };
    }
    editFinding(f) {
        this.state.form = { id: f.id, title: f.title, area: f.area_key, severity: f.severity, found: f.found,
            recommendation: f.recommendation, amount: f.amount || "", vouchers: f.vouchers.map((x) => x.name).join(", "),
            owner_id: f.owner_id || "", due: f.due_iso, check_key: "" };
    }
    async saveFinding() {
        const f = this.state.form;
        const res = await this.call("save_finding", [{ ...f, visit_id: this.v.id }]);
        this.state.form = null;
        this.set(res);
        this.notification.add(f.owner_id ? "Finding saved. The person who answers has been asked." : "Finding saved.",
            { type: "success" });
    }
    openFinding(f) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.audit.finding", res_id: f.id,
            views: [[false, "form"]] });
    }
    openVoucher(v) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.voucher", res_id: v.id, views: [[false, "form"]] });
    }
    async recheck(f, result) {
        this.set(await this.call("recheck", [this.v.id, f.id, result], { note: this.state.recheckNotes[f.id] || "" }));
    }

    // ---------------------------------------------------------------- end and report
    todoCount() {
        return this.v.checks.filter((c) => c.state === "todo").length;
    }
    recheckTodo() {
        return this.v.rechecks.filter((r) => !r.recheck).length;
    }
    async endVisit() {
        const res = await this.call("end_visit", [this.v.id, this.state.end.verdict], { summary: this.state.end.summary });
        this.state.end = { verdict: "", summary: "" };
        this.set(res.home);
        this.state.report = res.report;
        this.toTop();
        this.notification.add("Visit ended. Management has the report.", { type: "success" });
    }
    async showReport(id) {
        this.state.report = await this.call("visit_report", [id]);
        this.toTop();
    }
    toTop() {
        const el = document.querySelector(".o_aud");
        if (el) {
            el.scrollTop = 0;
        }
    }
    closeReport() {
        this.state.report = null;
    }
    print() {
        window.print();
    }
    checkResult(c) {
        if (c.cash) {
            return c.counted === null ? "Not counted" : Math.abs(c.diff) < 0.005 ? "Agrees with the books" : `Difference ${money(c.diff)}`;
        }
        return { todo: "Not looked at", ok: "In order", issue: "Issue found", na: "Not applicable" }[c.state];
    }

    // ---------------------------------------------------------------- management
    async answer(f, fixed) {
        this.set(await this.call("answer", [f.id, this.state.answers[f.id] || ""], { fixed }));
        delete this.state.answers[f.id];
        this.notification.add(fixed ? "Sent. The auditor will check it on the next visit." : "Answer sent to the auditor.",
            { type: "success" });
    }
    async addAuditor() {
        const res = await this.call("add_auditor", [this.state.add.name, this.state.add.email]);
        this.set(res);
        this.state.add = { name: "", email: "" };
        const a = res.added;
        this.notification.add(a.sent ? `${a.name} is invited. They get an email to set a password.`
            : `${a.name} is added. Email could not be sent: set a password for ${a.login} under Settings > Users.`,
            { type: a.sent ? "success" : "warning", sticky: !a.sent });
    }
    async removeAuditor(u) {
        this.set(await this.call("remove_auditor", [u.id]));
    }
}
registry.category("actions").add("sonador_audit.room", AuditRoom);
