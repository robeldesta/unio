import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const KIND = { fixed: "Fixed price", tm: "Time & materials", construction: "Construction (BOQ)", internal: "Internal" };
const STATE = { draft: "Tender / quote", active: "Running", hold: "On hold", done: "Completed", cancel: "Lost / cancelled" };
const TASK = { todo: "To do", doing: "In progress", blocked: "Blocked", done: "Done" };
const BILL = { milestone: "Progress invoice", tm: "Time & materials", ipc: "Payment certificate (IPC)", retention: "Retention release" };

/** Projects and job costing: portfolio, job (overview, plan, budget, BOQ, billing, costs, subcontracts), timesheets. */
export class ProjectsApp extends Component {
    static template = "sonador_project.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "portfolio", busy: false,
            pf: null, f: { state: "open", kind: "", search: "" },
            job: null, jobId: null, tab: "overview", jedit: null, custHits: [],
            phaseEdit: null, taskEdit: null, budget: null, boq: null, bill: null, subEdit: null,
            ts: null, tsWeek: false, tsRows: [], appr: null, rejectNote: "",
            set: null, codeEdit: null,
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.projects", "ctx", []);
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    money(v) {
        return v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }
    num(v, d = 2) {
        return v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });
    }
    kindLabel(k) {
        return KIND[k] || k;
    }
    stateLabel(s) {
        return STATE[s] || s;
    }
    taskLabel(s) {
        return TASK[s] || s;
    }
    billLabel(k) {
        return BILL[k] || k;
    }
    codeName(id) {
        const c = this.ctx.cost_codes.find((x) => x.id === Number(id));
        return c ? `${c.code} ${c.name}` : "";
    }
    pctOf(a, b) {
        return b ? Math.min(100, Math.round((100 * a) / b)) : 0;
    }
    async go(page, extra) {
        Object.assign(this.state, { page }, extra || {});
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "portfolio") {
                s.pf = await this.orm.call("sb.projects", "portfolio", [s.f]);
            } else if (s.page === "job" && s.jobId) {
                await this.loadJob();
            } else if (s.page === "timesheet") {
                s.ts = await this.orm.call("sb.projects", "week", [s.tsWeek || false]);
                s.tsRows = (s.ts.rows || []).map((r) => ({ ...r, hours: { ...r.hours } }));
            } else if (s.page === "approvals") {
                s.appr = await this.orm.call("sb.projects", "approvals", []);
            } else if (s.page === "settings") {
                s.set = await this.orm.call("sb.projects", "settings", []);
            }
        } finally {
            s.busy = false;
        }
    }
    async loadJob(data) {
        const s = this.state;
        s.job = data || (await this.orm.call("sb.projects", "job", [s.jobId]));
        s.budget = s.job.budget.map((b) => ({ ...b }));
        s.boq = s.job.boq.map((b) => ({ ...b }));
        s.bill = null;
    }
    async setF(k, v) {
        this.state.f[k] = v;
        await this.load();
    }
    async openJob(id, tab) {
        this.state.jobId = id;
        this.state.tab = tab || "overview";
        this.state.jedit = null;
        await this.go("job");
    }
    setTab(t) {
        this.state.tab = t;
        this.state.bill = null;
    }

    // ------------------------------------------------------------------ job header
    newJob() {
        this.state.page = "job";
        this.state.jobId = null;
        this.state.job = null;
        this.state.jedit = { name: "", partner_id: "", partner: "", kind: "fixed", state: "active", manager_id: this.ctx.me || "", member_ids: [],
            site: "", date_start: this.ctx.today, date_end: "", contract_value: "", vat: true, retention_pct: 5, advance_amount: "",
            advance_recovery_pct: "", bill_rate: "", description: "" };
    }
    editJob() {
        const j = this.state.job;
        this.state.jedit = { name: j.name, partner_id: j.partner_id || "", partner: j.customer, kind: j.kind, state: j.state, manager_id: j.manager_id || "",
            member_ids: [...j.member_ids], site: j.site, date_start: j.date_start || "", date_end: j.date_end || "", contract_value: j.contract_value,
            vat: j.vat, retention_pct: j.retention_pct, advance_amount: j.advance_amount, advance_recovery_pct: j.advance_recovery_pct,
            bill_rate: j.bill_rate, description: j.description };
    }
    setJ(k, v) {
        this.state.jedit[k] = v;
    }
    async findCust(v) {
        this.state.jedit.partner = v;
        this.state.jedit.partner_id = "";
        this.state.custHits = v.length > 1 ? await this.orm.call("sb.projects", "customers", [v]) : [];
    }
    pickCust(c) {
        Object.assign(this.state.jedit, { partner_id: c.id, partner: c.name });
        this.state.custHits = [];
    }
    toggleMember(id) {
        const m = this.state.jedit.member_ids;
        const i = m.indexOf(id);
        if (i >= 0) {
            m.splice(i, 1);
        } else {
            m.push(id);
        }
    }
    async saveJob() {
        const e = { ...this.state.jedit };
        delete e.partner;
        const j = await this.orm.call("sb.projects", "save_job", [this.state.jobId || false, e]);
        this.state.jobId = j.id;
        this.state.jedit = null;
        await this.loadJob(j);
        this.notification.add(`${j.code} saved.`, { type: "success" });
    }
    cancelJob() {
        if (!this.state.jobId) {
            this.go("portfolio");
        }
        this.state.jedit = null;
    }

    // ------------------------------------------------------------------ plan
    editPhase(p) {
        this.state.phaseEdit = p ? { ...p } : { id: false, name: "", start: "", end: "", progress: 0 };
    }
    async savePhase() {
        await this.loadJob(await this.orm.call("sb.projects", "save_phase", [this.state.jobId, this.state.phaseEdit]));
        this.state.phaseEdit = null;
    }
    async removePhase(p) {
        await this.loadJob(await this.orm.call("sb.projects", "remove_phase", [p.id]));
    }
    editTask(t, phaseId) {
        this.state.taskEdit = t ? { ...t } : { id: false, name: "", phase_id: phaseId || "", employee_id: "", start: "", due: "", state: "todo",
            hours: "", priority: false, milestone: false, description: "" };
    }
    setT(k, v) {
        this.state.taskEdit[k] = v;
    }
    async saveTask() {
        await this.loadJob(await this.orm.call("sb.projects", "save_task", [this.state.jobId, this.state.taskEdit]));
        this.state.taskEdit = null;
    }
    async moveTask(t, st) {
        await this.loadJob(await this.orm.call("sb.projects", "save_task", [this.state.jobId, { id: t.id, state: st }]));
    }
    async removeTask(t) {
        await this.loadJob(await this.orm.call("sb.projects", "remove_task", [t.id]));
        this.state.taskEdit = null;
    }
    tasksIn(st) {
        return this.state.job.tasks.filter((t) => t.state === st);
    }

    // ------------------------------------------------------------------ budget / BOQ
    addBudget() {
        this.state.budget.push({ id: false, phase_id: "", cost_code_id: (this.ctx.cost_codes[0] || {}).id || "", name: "", qty: 1, unit: "", rate: 0, amount: 0 });
    }
    setB(r, k, v) {
        r[k] = v;
        r.amount = (Number(r.qty) || 0) * (Number(r.rate) || 0);
    }
    get budgetTotal() {
        return this.state.budget.reduce((a, r) => a + (Number(r.qty) || 0) * (Number(r.rate) || 0), 0);
    }
    async saveBudget() {
        await this.loadJob(await this.orm.call("sb.projects", "save_budget", [this.state.jobId, this.state.budget]));
        this.notification.add("Budget saved.", { type: "success" });
    }
    addBoq() {
        this.state.boq.push({ id: false, section: "", item_no: "", name: "", unit: "", qty: 0, rate: 0, amount: 0, done: 0 });
    }
    setQ(r, k, v) {
        r[k] = v;
        r.amount = (Number(r.qty) || 0) * (Number(r.rate) || 0);
    }
    get boqTotal() {
        return this.state.boq.reduce((a, r) => a + (Number(r.qty) || 0) * (Number(r.rate) || 0), 0);
    }
    async saveBoq() {
        await this.loadJob(await this.orm.call("sb.projects", "save_boq", [this.state.jobId, this.state.boq]));
        this.notification.add("Bill of quantities saved.", { type: "success" });
    }
    removeRow(list, i) {
        this.state[list].splice(i, 1);
    }
    pickExcel(kind) {
        const inp = document.createElement("input");
        inp.type = "file";
        inp.accept = ".xlsx";
        inp.onchange = () => {
            const file = inp.files[0];
            if (!file) {
                return;
            }
            const rd = new FileReader();
            rd.onload = async () => {
                const rows = await this.orm.call("sb.projects", "read_excel", [String(rd.result).split(",")[1], kind]);
                if (kind === "boq") {
                    for (const r of rows) {
                        this.state.boq.push({ id: false, done: 0, ...r, amount: r.qty * r.rate });
                    }
                } else {
                    for (const r of rows) {
                        const code = this.ctx.cost_codes.find((c) => r.item_no && c.code.toLowerCase() === r.item_no.toLowerCase()) || this.ctx.cost_codes[0];
                        this.state.budget.push({ id: false, phase_id: "", cost_code_id: code ? code.id : "", name: r.name, qty: r.qty, unit: r.unit, rate: r.rate, amount: r.qty * r.rate });
                    }
                }
                this.notification.add(`${rows.length} rows read: check them and press Save.`, { type: "info" });
            };
            rd.readAsDataURL(file);
        };
        inp.click();
    }
    async template(kind) {
        const f = await this.orm.call("sb.projects", "excel_template", [kind]);
        const bin = atob(f.data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) {
            bytes[i] = bin.charCodeAt(i);
        }
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([bytes]));
        a.download = f.name;
        a.click();
    }

    // ------------------------------------------------------------------ billing
    async newBill(kind) {
        this.state.bill = await this.orm.call("sb.projects", "new_bill", [this.state.jobId, kind]);
        this.state.job.bills.unshift(this.state.bill);
    }
    openBill(b) {
        this.state.bill = JSON.parse(JSON.stringify(b));
    }
    setBillLine(l, v) {
        l.to_date = v;
    }
    async saveBill() {
        const b = this.state.bill;
        this.state.bill = await this.orm.call("sb.projects", "save_bill", [b.id, { name: b.name, date: b.date, gross: b.gross, note: b.note,
            lines: b.lines.map((l) => ({ id: l.id, to_date: l.to_date })) }]);
        this.notification.add("Recalculated.", { type: "info" });
    }
    async approveBill() {
        await this.saveBill();
        const b = this.state.bill;
        if (!window.confirm(`Approve ${b.number} for ${this.money(b.total)} ETB? A draft sales invoice goes to Accounting.`)) {
            return;
        }
        await this.loadJob(await this.orm.call("sb.projects", "approve_bill", [b.id]));
        this.notification.add(`${b.number} approved: draft sales invoice in Accounting.`, { type: "success" });
    }
    async deleteBill(b) {
        if (!window.confirm(`Delete ${b.number}?`)) {
            return;
        }
        await this.loadJob(await this.orm.call("sb.projects", "delete_bill", [b.id]));
    }
    openVoucher(v) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.voucher", res_id: v.id, views: [[false, "form"]] });
    }
    openLedger(l) {
        if (l.model) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: l.model, res_id: l.res_id, views: [[false, "form"]] });
        }
    }
    editSub(s) {
        this.state.subEdit = s ? { ...s } : { id: false, partner_id: "", partner: "", name: "", value: "", retention_pct: 5, phase_id: "", note: "" };
    }
    async findSub(v) {
        this.state.subEdit.partner = v;
        this.state.custHits = v.length > 1 ? await this.orm.call("sb.projects", "customers", [v]) : [];
    }
    pickSub(c) {
        Object.assign(this.state.subEdit, { partner_id: c.id, partner: c.name });
        this.state.custHits = [];
    }
    async saveSub() {
        const e = { ...this.state.subEdit };
        delete e.partner;
        await this.loadJob(await this.orm.call("sb.projects", "save_sub", [this.state.jobId, e]));
        this.state.subEdit = null;
    }
    get costChart() {
        const c = this.state.job.fin.by_code;
        return { labels: c.map((x) => x.name), series: [{ label: "Budget", data: c.map((x) => x.budget) }, { label: "Actual", data: c.map((x) => x.actual) },
            { label: "Forecast at completion", data: c.map((x) => x.eac) }] };
    }

    // ------------------------------------------------------------------ timesheet
    async shiftWeek(dir) {
        this.state.tsWeek = dir < 0 ? this.state.ts.prev : this.state.ts.next;
        await this.load();
    }
    addTsRow() {
        const j = this.state.ts.jobs[0];
        this.state.tsRows.push({ job_id: j ? j.id : "", task_id: "", hours: {}, states: {}, notes: {} });
    }
    tasksOf(jobId) {
        const j = this.state.ts.jobs.find((x) => x.id === Number(jobId));
        return j ? j.tasks : [];
    }
    setTs(r, day, v) {
        r.hours[day] = v;
    }
    setTsRow(r, k, v) {
        r[k] = v;
    }
    rowTotal(r) {
        return Object.values(r.hours).reduce((a, v) => a + (Number(v) || 0), 0);
    }
    dayTotal(d) {
        return this.state.tsRows.reduce((a, r) => a + (Number(r.hours[d]) || 0), 0);
    }
    get weekTotal() {
        return this.state.tsRows.reduce((a, r) => a + this.rowTotal(r), 0);
    }
    cellLocked(r, d) {
        return r.states && (r.states[d] === "submitted" || r.states[d] === "approved");
    }
    async saveTs(submit) {
        this.state.ts = await this.orm.call("sb.projects", "save_week", [this.state.ts.week, this.state.tsRows, submit]);
        this.state.tsRows = this.state.ts.rows.map((r) => ({ ...r, hours: { ...r.hours } }));
        this.notification.add(submit ? "Week sent for approval." : "Saved.", { type: "success" });
    }
    async decide(g, approve) {
        this.state.appr = await this.orm.call("sb.projects", "decide_hours", [g.ids, approve, this.state.rejectNote]);
        this.state.rejectNote = "";
    }

    // ------------------------------------------------------------------ settings
    setS(k, v) {
        this.state.set[k] = v;
    }
    async saveSettings() {
        this.state.set = await this.orm.call("sb.projects", "save_settings", [this.state.set]);
        this.notification.add("Saved.", { type: "success" });
    }
    async saveCode() {
        this.state.set = await this.orm.call("sb.projects", "save_code", [this.state.codeEdit]);
        this.state.ctx = await this.orm.call("sb.projects", "ctx", []);
        this.state.codeEdit = null;
    }
    exportCsv() {
        const rows = this.state.pf.rows;
        const head = ["Code", "Job", "Customer", "Type", "Status", "Contract", "Budget", "Actual cost", "Forecast at completion", "Margin at completion", "% complete", "Certified", "Over/under billed", "Retention held"];
        const data = rows.map((r) => [r.code, r.name, r.customer, this.kindLabel(r.kind), this.stateLabel(r.state), r.contract, r.budget, r.cost, r.eac, r.margin_eac, r.pct, r.certified, r.over_under, r.retention]);
        const csv = [head, ...data].map((l) => l.map((v) => `"${String(v === null || v === undefined ? "" : v).replace(/"/g, '""')}"`).join(",")).join("\n");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        a.download = "jobs.csv";
        a.click();
    }
}

registry.category("actions").add("sonador_project.app", ProjectsApp);
