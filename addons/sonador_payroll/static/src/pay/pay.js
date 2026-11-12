import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const money = (v) =>
    v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Payroll: runs, the run grid, employee pay, final settlement, settings. Employees see their own payslips. */
export class PayrollApp extends Component {
    static template = "sonador_payroll.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "runs", busy: false,
            runs: [], create: null, confirmDrop: null, run: null, edit: {}, pay: { date: "", account: "" },
            who: p.employee_id || null, emp: null, empEdit: null,
            st: { employee_id: "", last_day: "", code: "resign", notice_given: true, pension_eligible: false, res: null },
            set: null,
            f: { q: "", dept: "", show: "all" },
            loans: null, loanTab: "", lr: null, terms: {}, refusing: null, refuseNote: "",
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.payroll.service", "ctx", []);
            if (!this.state.ctx.is_hr && this.state.page !== "loans") {
                this.state.page = "employee";
            }
            this.state.who = this.state.who || this.state.ctx.me;
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    money(v) {
        return money(v);
    }
    stateLabel(s) {
        return { draft: "Draft", posted: "Posted", paid: "Paid" }[s] || s;
    }
    async go(page) {
        this.state.page = page;
        this.state.create = null;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "runs") {
                s.runs = await this.orm.call("sb.payroll.service", "runs", []);
            } else if (s.page === "employee" && s.who) {
                s.emp = await this.orm.call("sb.payroll.service", "employee", [s.who]);
                s.empEdit = null;
            } else if (s.page === "loans") {
                s.loans = await this.orm.call("sb.payroll.service", "loans", []);
                s.terms = {};
                for (const r of s.loans.rows.filter((r) => r.can_hr)) {
                    s.terms[r.id] = { installment: r.installment || "", start: r.start || s.ctx.today };
                }
                if (!s.loanTab) {
                    s.loanTab = s.loans.rows.some((r) => r.can_hr || r.can_gm) ? "mine" : "all";
                }
                if (s.ctx.is_hr && !s.people) {
                    s.people = (await this.orm.call("sb.people", "directory", [], { status: "all" })).rows;
                }
            } else if (s.page === "settings") {
                s.set = await this.orm.call("sb.payroll.service", "settings", []);
            } else if (s.page === "settlement" && !s.people) {
                s.people = (await this.orm.call("sb.people", "directory", [], { status: "all" })).rows;
            }
            if (s.page === "employee" && s.ctx.is_hr && !s.people) {
                s.people = (await this.orm.call("sb.people", "directory", [], { status: "all" })).rows;
            }
        } finally {
            s.busy = false;
        }
    }

    // ------------------------------------------------------------------ run grid filter
    get depts() {
        const run = this.state.run;
        return run ? [...new Set(run.lines.map((l) => l.employee.department || "No department"))].sort() : [];
    }
    get shows() {
        const L = this.state.run ? this.state.run.lines : [];
        const n = (fn) => L.filter(fn).length;
        return [
            ["all", "Everyone", L.length],
            ["joining", "Joining", n((l) => l.status === "joining")],
            ["leaving", "Leaving", n((l) => l.status === "leaving")],
            ["part", "Part month", n((l) => l.pay_days < 30)],
            ["ot", "With overtime", n((l) => l.overtime > 0)],
            ["warn", "Missing details", n((l) => l.warn.length)],
        ].filter((x) => x[0] === "all" || x[2]);
    }
    get lines() {
        const run = this.state.run, f = this.state.f;
        if (!run) {
            return [];
        }
        const q = f.q.trim().toLowerCase();
        const test = { all: () => true, joining: (l) => l.status === "joining", leaving: (l) => l.status === "leaving",
            part: (l) => l.pay_days < 30, ot: (l) => l.overtime > 0, warn: (l) => l.warn.length }[f.show] || (() => true);
        return run.lines.filter((l) => (!q || l.employee.name.toLowerCase().includes(q))
            && (!f.dept || (l.employee.department || "No department") === f.dept) && test(l));
    }
    get filtered() {
        const f = this.state.f;
        return !!(f.q.trim() || f.dept || f.show !== "all");
    }
    get tot() {
        if (!this.filtered) {
            return this.state.run.totals;
        }
        const sum = {};
        for (const l of this.lines) {
            for (const k of Object.keys(this.state.run.totals)) {
                sum[k] = (sum[k] || 0) + (Number(l[k]) || 0);
            }
        }
        return sum;
    }
    clearFilter() {
        Object.assign(this.state.f, { q: "", dept: "", show: "all" });
    }

    // ------------------------------------------------------------------ loans and advances
    get loanTabs() {
        const R = this.state.loans ? this.state.loans.rows : [];
        const n = (fn) => R.filter(fn).length;
        return [
            ["mine", "Waiting for me", n((r) => r.can_hr || r.can_gm)],
            ["wait", "Waiting", n((r) => ["hr", "gm"].includes(r.state))],
            ["open", "Being paid back", n((r) => ["open", "hold"].includes(r.state))],
            ["done", "Paid off", n((r) => r.state === "done")],
            ["all", "All", R.length],
        ].filter((x) => x[0] === "all" || x[2]);
    }
    get loanRows() {
        const R = this.state.loans ? this.state.loans.rows : [];
        const t = this.state.loanTab;
        const f = { mine: (r) => r.can_hr || r.can_gm, wait: (r) => ["hr", "gm"].includes(r.state),
            open: (r) => ["open", "hold"].includes(r.state), done: (r) => r.state === "done" }[t];
        return f ? R.filter(f) : R;
    }
    terms(x) {
        return this.state.terms[x.id] || { installment: x.installment || "", start: x.start || this.ctx.today };
    }
    instOf(x) {
        return x.can_hr ? Number(this.terms(x).installment || 0) : x.installment;
    }
    termMonths(x) {
        const i = this.instOf(x);
        return i > 0 ? Math.ceil(x.amount / i - 0.000001) : 0;
    }
    paidPct(x) {
        return Math.min(100, x.amount ? (x.recovered / x.amount) * 100 : 0);
    }
    async openCpv(x) {
        const act = await this.orm.call("sb.payroll.service", "loan_voucher", [x.id]);
        if (act) {
            this.action.doAction(act);
        }
    }
    askRefuse(x) {
        this.state.refusing = x.id;
        this.state.refuseNote = "";
    }
    newLoan() {
        this.state.lr = { employee_id: this.ctx.is_hr ? "" : this.ctx.me, kind: "loan", amount: "", months: 10, note: "" };
    }
    async sendLoan() {
        await this.orm.call("sb.payroll.service", "loan_request", [this.state.lr]);
        this.state.lr = null;
        this.state.loanTab = "";
        this.state.ctx = await this.orm.call("sb.payroll.service", "ctx", []);
        await this.load();
        this.notification.add("Sent to HR.", { type: "success" });
    }
    async decideLoan(x, approve) {
        if (approve && x.can_hr) {
            await this.orm.call("sb.payroll.service", "loan_terms", [x.id, this.terms(x)]);
        }
        await this.orm.call("sb.payroll.service", "loan_decide", [x.id, approve, approve ? "" : this.state.refuseNote]);
        this.state.refusing = null;
        this.state.ctx = await this.orm.call("sb.payroll.service", "ctx", []);
        await this.load();
        this.notification.add(approve ? (x.can_hr ? "Terms approved: sent to the general manager." : "Approved: payroll deducts it from the first month.") : "Refused.",
            { type: approve ? "success" : "warning" });
    }

    // ------------------------------------------------------------------ runs
    newRun() {
        this.state.create = { period: this.ctx.default_period, label: "", kind: "regular", depts: [] };
    }
    async createRun() {
        const c = this.state.create;
        const id = await this.orm.call("sb.payroll.service", "run_create", [c.period, c.label, c.kind, c.depts]);
        this.state.create = null;
        await this.openRun(id);
    }
    editRun() {
        const r = this.state.run.run;
        this.state.create = { id: r.id, period: r.period, label: r.label, kind: r.kind, depts: [...r.department_ids] };
    }
    async saveRun() {
        const c = this.state.create;
        const res = await this.orm.call("sb.payroll.service", "run_save", [c.id, { period: c.period, label: c.label, kind: c.kind, department_ids: c.depts }]);
        this.state.create = null;
        this.state.run = res;
        this.state.edit = {};
        this.notification.add(res.removed ? `Saved. ${res.removed} people outside it now were taken off.` : "Saved.", { type: "success" });
    }
    async removeLine(line) {
        if (this.state.confirmDrop !== line.id) {
            this.state.confirmDrop = line.id;
            return;
        }
        this.state.confirmDrop = null;
        this.state.run = await this.orm.call("sb.payroll.service", "line_remove", [line.id]);
        delete this.state.edit[line.id];
    }
    toggleDept(id) {
        const d = this.state.create.depts;
        const i = d.indexOf(id);
        if (i >= 0) {
            d.splice(i, 1);
        } else {
            d.push(id);
        }
    }
    async openRun(id) {
        this.state.imported = null;
        this.state.create = null;
        this.state.run = await this.orm.call("sb.payroll.service", "run", [id]);
        this.state.edit = {};
        this.state.page = "run";
        const r = this.state.run.run;
        this.state.pay = { date: r.pay_date || this.ctx.today, account: r.pay_account_id || (this.ctx.cash[0] && this.ctx.cash[0].id) || "" };
    }
    async act(action) {
        const r = this.state.run.run;
        const res = await this.orm.call("sb.payroll.service", "run_action", [r.id, action, { pay_date: this.state.pay.date, pay_account_id: this.state.pay.account }]);
        if (!res) {
            await this.go("runs");
            return;
        }
        this.state.run = res;
        this.notification.add({ load: "Employees loaded.", refill: "Recalculated from People, Leave Requests and loans.", post: "Posted to the books.",
            pay: "Salary payment recorded.", reset: "Back to draft.", ot_reset: "Overtime taken from the attendance records again." }[action] || "Done.", { type: "success" });
    }
    async toggleLeaving(line) {
        this.state.run = await this.orm.call("sb.payroll.service", "line_leaving", [line.id, line.status !== "leaving"]);
        this.notification.add(line.status !== "leaving" ? `${line.employee.name}: leaving after this payroll.` : `${line.employee.name}: staying.`,
            { type: "info" });
    }
    setEdit(line, key, value) {
        this.state.edit[line.id] = { ...(this.state.edit[line.id] || {}), [key]: value };
    }
    async saveLine(line) {
        const vals = this.state.edit[line.id];
        if (!vals) {
            return;
        }
        this.state.run = await this.orm.call("sb.payroll.service", "line_save", [line.id, vals]);
        delete this.state.edit[line.id];
    }
    payslips() {
        window.open(`/sonador_books/payslips/${this.state.run.run.id}`, "_blank");
    }
    file(kind) {
        const url = `/sonador_payroll/file/${this.state.run.run.id}/${kind}`;
        if (kind === "letter") {
            if (!this.ctx.letter_ready) {
                this.notification.add("Enter the bank and our account number for the letter in Payroll > Settings first.", { type: "warning" });
                return;
            }
            window.open(url, "_blank");
        } else {
            window.location = url;
        }
    }
    pickOt() {
        document.querySelector(".o_py_otfile").click();
    }
    async importOt(ev) {
        const f = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!f) {
            return;
        }
        const data = await new Promise((ok, bad) => {
            const rd = new FileReader();
            rd.onload = () => ok(String(rd.result).split(",")[1]);
            rd.onerror = bad;
            rd.readAsDataURL(f);
        });
        this.state.busy = true;
        try {
            const res = await this.orm.call("sb.payroll.service", "ot_import", [this.state.run.run.id, data, f.name]);
            this.state.imported = res.imported;
            delete res.imported;
            this.state.run = res;
            this.notification.add(`Overtime imported for ${this.state.imported.done} people.`, { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
    payslip(id) {
        window.open(`/sonador_payroll/payslip/${id}`, "_blank");
    }

    // ------------------------------------------------------------------ employee
    async pickWho() {
        await this.load();
    }
    editEmp() {
        const e = this.state.emp;
        this.state.empEdit = { profile_id: e.profile_id || "", staff_account_id: e.staff_account_id || "",
            benefits: e.benefits.map((b) => ({ ...b })), loans: e.loans.map((l) => ({ ...l })) };
    }
    addBenefit() {
        this.state.empEdit.benefits.push({ name: "", kind: "allowance", amount: 0, date_from: "", date_to: "", account_id: "", note: "" });
    }
    addLoan() {
        this.state.empEdit.loans.push({ kind: "loan", date: this.ctx.today, amount: 0, installment: 0, start: this.ctx.today, state: "open", note: "" });
    }
    removeBenefit(i) {
        this.state.empEdit.benefits.splice(i, 1);
    }
    async saveEmp() {
        this.state.emp = await this.orm.call("sb.payroll.service", "employee_save", [this.state.who, this.state.empEdit]);
        this.state.empEdit = null;
        this.notification.add("Saved. It applies from the next payroll fill.", { type: "success" });
    }

    // ------------------------------------------------------------------ settlement
    async previewSettlement() {
        const t = this.state.st;
        if (!t.employee_id || !t.last_day) {
            return;
        }
        t.res = await this.orm.call("sb.payroll.service", "settlement", [t.employee_id, t.last_day, t.code, t.notice_given, t.pension_eligible]);
    }
    async makeSettlement() {
        const t = this.state.st;
        const r = await this.orm.call("sb.payroll.service", "settlement_make", [t.employee_id, t.last_day, t.code, t.notice_given, t.pension_eligible]);
        this.notification.add("Leaving recorded and final settlement prepared (draft payroll).", { type: "success" });
        await this.openRun(r.run_id);
    }

    // ------------------------------------------------------------------ settings
    addProfile() {
        this.state.set.profiles.push({ id: false, name: "" });
    }
    async saveSettings() {
        this.state.set = await this.orm.call("sb.payroll.service", "settings_save", [this.state.set]);
        this.state.ctx = await this.orm.call("sb.payroll.service", "ctx", []);
        this.notification.add("Payroll settings saved.", { type: "success" });
    }
    setKey(obj, key, value) {
        obj[key] = value;
    }
    keyLabel(k) {
        return { salary: "Basic salary", overtime: "Overtime", house: "House allowance", position: "Position allowance", allowance: "Other taxable",
            cash_indemnity: "Cash indemnity", transport: "Transport / non-taxable", commission: "Commission", bonus: "Bonus", pension: "Employer pension",
            settlement: "Severance / leave pay-out" }[k] || k;
    }
}

registry.category("actions").add("sonador_payroll.app", PayrollApp);
