import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";

const STATUS = { todo: "Not started", on_track: "On track", blocked: "Blocked", done: "Done" };
const PLAN_STATE = { draft: "Draft", submitted: "With your manager", returned: "Returned for changes", approved: "Approved" };

let keySeq = 1;
function newRow(month, monthEnd, weeks) {
    const w = (weeks || [])[0];
    return { key: "n" + keySeq++, id: false, name: "", done_when: "", target_qty: "", unit: "", date_from: w ? w.from : month, date_to: w ? w.to : monthEnd,
        output: "", category_id: "", goal_id: "", indicator_id: "", role_item_id: "", detail: "", review: "pending", review_note: "", flags: [], more: false };
}

/** Planning: my week (check-ins, report), my month (plan with live checker, Excel), my year, my team, overview, settings. */
export class PlanningApp extends Component {
    static template = "sonador_planning.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "week", busy: false,
            wk: null, day: null, ck: {}, rep: { remarks: "", next_focus: "" }, addTask: null,
            mo: null, rows: [], check: null, dirty: false, checking: false, paste: null, viewing: null,
            yr: null, team: null, review: null, decisions: {}, reviewNote: "",
            ov: null, set: null, catEdit: null, peopleFilter: "", roleEdit: null,
            changeMode: false, changeReason: "", sendBack: null, refuseNote: "",
            next: [], nextDirty: false, reasons: {}, reviewScore: "", reviewReason: "", board: null, boardDay: null,
        });
        this.checkTimer = null;
        onWillUnmount(() => clearTimeout(this.checkTimer));
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.planning", "ctx", []);
            if (!this.ctx.me && ["week", "month", "year"].includes(this.state.page)) {
                this.state.page = this.ctx.is_hr ? "overview" : "none";
            }
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    get gate() {
        return this.ctx.gate || { reasons: [] };
    }
    statusLabel(s) {
        return STATUS[s] || s;
    }
    planState(s) {
        return PLAN_STATE[s] || s;
    }
    async go(page, extra) {
        Object.assign(this.state, { page, review: null }, extra || {});
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "week") {
                s.wk = await this.orm.call("sb.planning", "week", [s.day || false, s.viewing || false]);
                this.afterWeek();
            } else if (s.page === "month") {
                await this.loadMonth(s.mo ? s.mo.month : false);
            } else if (s.page === "year") {
                s.yr = await this.orm.call("sb.planning", "year", [s.viewing || false]);
            } else if (s.page === "team") {
                s.team = await this.orm.call("sb.planning", "team", []);
            } else if (s.page === "overview") {
                s.ov = await this.orm.call("sb.planning", "overview", []);
            } else if (s.page === "settings") {
                s.set = await this.orm.call("sb.planning", "settings", []);
            } else if (s.page === "board") {
                s.board = await this.orm.call("sb.planning", "board", [s.boardDay || false]);
            }
        } finally {
            s.busy = false;
        }
    }
    async refreshGate() {
        this.state.ctx = await this.orm.call("sb.planning", "ctx", []);
    }
    async fixReason(r) {
        if (r.page === "week") {
            this.state.day = r.date || false;
            await this.go("week");
        } else {
            this.state.mo = null;
            await this.go("month");
        }
    }

    // ------------------------------------------------------------------ week
    afterWeek() {
        const W = this.state.wk;
        this.state.rep = { remarks: W.report.remarks, next_focus: W.report.next_focus };
        this.state.next = W.nextweek.rows.map((r) => ({ ...r, key: "x" + (r.id || keySeq++) }));
        this.state.nextDirty = false;
        this.state.reasons = {};
        this.state.reviewScore = W.report.plan_score === null || W.report.plan_score === undefined ? "" : W.report.plan_score;
        this.state.reviewReason = "";
        this.initCk();
    }
    priLabel(p) {
        return { must: "Must", should: "Should", could: "Could" }[p] || p;
    }
    fmt(v) {
        if (v === null || v === undefined || v === "") {
            return "–";
        }
        const n = Number(v);
        return n.toLocaleString(undefined, { maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 });
    }
    bandOf(score) {
        return score === null || score === undefined ? "none" : score >= 85 ? "good" : score >= 65 ? "mid" : "bad";
    }
    reasonOf(l) {
        return l.id in this.state.reasons ? this.state.reasons[l.id] : l.reason || "";
    }
    setReason(l, v) {
        this.state.reasons[l.id] = v;
    }
    setNext(r, k, v) {
        r[k] = v;
        if (k === "measure" && v) {
            const m = this.ctx.measures.find((x) => x.key === v);
            if (m && !r.unit) {
                r.unit = m.unit === "money" ? "Br" : m.unit;
            }
        }
        this.state.nextDirty = true;
    }
    addNext() {
        this.state.next.push({ key: "n" + keySeq++, id: false, name: "", priority: "should", target_qty: "", unit: "", measure: "", done_when: "", standard: false, carried: 0 });
        this.state.nextDirty = true;
    }
    removeNext(r) {
        this.state.next = this.state.next.filter((x) => x !== r);
        this.state.nextDirty = true;
    }
    async saveNext() {
        const must = this.state.next.filter((r) => r.priority === "must" && (r.name || "").trim()).length;
        if (must > 3) {
            this.notification.add(`${must} Must lines: keep Must for the 3 most urgent, make the rest Should.`, { type: "warning" });
        }
        this.state.wk = await this.orm.call("sb.planning", "save_next", [this.state.wk.week, this.state.next]);
        this.afterWeek();
        this.notification.add("Next week's plan saved.", { type: "success" });
    }
    async reviewWeek(agree) {
        const W = this.state.wk;
        this.state.wk = await this.orm.call("sb.planning", "review_week", [W.report.id, agree, this.state.reviewScore, this.state.reviewReason]);
        this.afterWeek();
        this.notification.add(agree ? "Score agreed." : "Score changed; the person sees why.", { type: "success" });
    }
    async shiftBoard(dir) {
        this.state.boardDay = dir < 0 ? this.state.board.prev : this.state.board.next;
        await this.load();
    }
    async shiftWeek(dir) {
        this.state.day = dir < 0 ? this.state.wk.prev : this.state.wk.next;
        await this.load();
    }
    async pickDay(d) {
        this.state.day = d.date;
        await this.load();
    }
    initCk() {
        const ck = {};
        for (const t of this.state.wk.strip) {
            const c = t.checkin;
            ck[t.id] = { status: c ? c.status : "", progress: c ? c.progress : t.last_progress || t.progress || 0,
                note: c ? c.note : "", done_qty: c ? c.done_qty : "", open: false };
        }
        this.state.ck = ck;
    }
    ckOf(t) {
        return this.state.ck[t.id] || { status: "", progress: 0, note: "", done_qty: "", open: false };
    }
    setCk(c, k, v) {
        c[k] = k === "progress" ? Number(v) : v;
    }
    editCat(c) {
        this.state.catEdit = { ...c };
    }
    async tap(t, status) {
        const c = this.ckOf(t);
        c.status = status;
        if (status === "done") {
            c.progress = 100;
        } else if (status === "on_track" && c.progress >= 100) {
            c.progress = 90;
        }
        if (status === "blocked" && !(c.note || "").trim()) {
            c.open = true; // a blocker needs a word on what blocks it
            return;
        }
        await this.sendCheckin(t);
    }
    async sendCheckin(t) {
        const c = this.ckOf(t);
        this.state.wk = await this.orm.call("sb.planning", "checkin", [t.id, this.state.wk.selected, c.status, c.progress, c.note, c.done_qty]);
        this.afterWeek();
        this.notification.add(`${t.name}: ${this.statusLabel(c.status).toLowerCase()}.`, { type: "success" });
        await this.refreshGate();
    }
    async saveReport(confirm) {
        if (this.state.nextDirty) {
            await this.saveNext();
        }
        this.state.wk = await this.orm.call("sb.planning", "save_report", [this.state.wk.week, this.state.rep.remarks, this.state.rep.next_focus, confirm, this.state.reasons]);
        this.afterWeek();
        this.notification.add(confirm ? "Weekly report sent to your manager." : "Saved.", { type: "success" });
        await this.refreshGate();
    }
    startAddTask() {
        this.state.addTask = { name: "", done_when: "", date_from: this.state.wk.selected, date_to: this.state.wk.selected, output: "", category_id: "", goal_id: "" };
    }
    async saveAddTask() {
        this.state.wk = await this.orm.call("sb.planning", "add_week_task", [this.state.addTask]);
        this.afterWeek();
        this.state.addTask = null;
        this.notification.add("Task added. Your manager will see it marked as added.", { type: "success" });
    }
    weekCell(t, d) {
        const c = (t.week_checkins || []).find((x) => x.date === d.date);
        const inSpan = t.date_from <= d.date && d.date <= t.date_to;
        if (!inSpan) {
            return { cls: "out", text: "" };
        }
        if (c) {
            return { cls: c.status, text: c.status === "done" ? "✓" : c.progress + "%" };
        }
        return { cls: d.off ? "off" : d.future ? "future" : "miss", text: d.off || d.future ? "" : "·" };
    }

    // ------------------------------------------------------------------ month
    async loadMonth(month) {
        const s = this.state;
        s.changeMode = false;
        s.changeReason = "";
        s.mo = await this.orm.call("sb.planning", "month", [month || false, s.viewing || false]);
        s.rows = s.mo.tasks.map((t) => ({ ...t }));
        if (s.mo.editable && !s.rows.length) {
            for (let i = 0; i < 4; i++) {
                s.rows.push(newRow(s.mo.month, s.mo.month_end, s.mo.weeks));
            }
        }
        s.dirty = false;
        s.check = null;
        if (s.mo.editable && s.mo.tasks.length) {
            await this.runCheck();
        }
    }
    get canEdit() {
        return this.state.mo && (this.state.mo.editable || this.state.changeMode);
    }
    startChange() {
        this.state.changeMode = true;
        this.state.changeReason = "";
        this.runCheck();
    }
    async cancelChange() {
        await this.loadMonth(this.state.mo.month);
    }
    async sendChange() {
        const s = this.state;
        const rows = s.rows.filter((r) => (r.name || "").trim() || (r.done_when || "").trim());
        const res = await this.orm.call("sb.planning", "propose_change", [s.mo.month, rows, s.changeReason]);
        await this.loadMonth(res.month);
        this.notification.add(`${res.changes.length} change(s) sent to ${res.approver || "your manager"}: nothing changes until they accept.`, { type: "success" });
    }
    addRoleItems() {
        const s = this.state;
        const role = this.ctx.role;
        if (!role) {
            return;
        }
        s.rows = s.rows.filter((r) => (r.name || "").trim());
        let n = 0;
        for (const it of role.items) {
            if (s.rows.some((r) => String(r.role_item_id) === String(it.id))) {
                continue;
            }
            s.rows.push({ ...newRow(s.mo.month, s.mo.month_end, s.mo.weeks), name: it.name, done_when: it.done_when, target_qty: it.target_qty,
                unit: it.unit, output: it.output, indicator_id: it.indicator_id, role_item_id: it.id });
            n++;
        }
        s.dirty = true;
        this.notification.add(n ? `${n} item(s) of the ${role.name} role added: set the week and the number for this month.` : "Your role's items are already in the plan.", { type: "info" });
        this.runCheck();
    }
    toggleMore(r) {
        r.more = !r.more;
    }
    async shiftMonth(dir) {
        await this.loadMonth(dir < 0 ? this.state.mo.prev : this.state.mo.next);
    }
    setCell(r, k, v) {
        r[k] = v;
        if (k === "date_from" && (!r.date_to || r.date_to < v)) {
            r.date_to = v;
        }
        this.state.dirty = true;
        this.scheduleCheck();
    }
    setWeek(r, w) {
        r.date_from = w.from;
        r.date_to = w.to;
        this.state.dirty = true;
        this.scheduleCheck();
    }
    weekOn(r, w) {
        return r.date_from <= w.to && r.date_to >= w.from;
    }
    scheduleCheck() {
        clearTimeout(this.checkTimer);
        this.checkTimer = setTimeout(() => this.runCheck(), 700);
    }
    async runCheck() {
        const s = this.state;
        if (!s.mo) {
            return;
        }
        s.checking = true;
        try {
            const rows = s.rows.filter((r) => (r.name || "").trim() || (r.done_when || "").trim());
            s.check = await this.orm.call("sb.planning", "check", [s.mo.month, rows, s.viewing || false]);
        } finally {
            s.checking = false;
        }
    }
    findings(r) {
        return (this.state.check && this.state.check.tasks[r.key]) || [];
    }
    rowLevel(r) {
        const f = this.findings(r);
        return f.some((x) => x.level === "block") ? "block" : f.length ? "warn" : (r.name || "").trim() ? "ok" : "";
    }
    addRow() {
        this.state.rows.push(newRow(this.state.mo.month, this.state.mo.month_end, this.state.mo.weeks));
    }
    removeRow(i) {
        this.state.rows.splice(i, 1);
        this.state.dirty = true;
        this.scheduleCheck();
    }
    moveRow(i, n) {
        const r = this.state.rows;
        const j = i + n;
        if (j < 0 || j >= r.length) {
            return;
        }
        [r[i], r[j]] = [r[j], r[i]];
        this.state.dirty = true;
    }
    async savePlan(submit) {
        const s = this.state;
        const rows = s.rows.filter((r) => (r.name || "").trim() || (r.done_when || "").trim());
        const wasSubmitted = s.mo.plan && s.mo.plan.state === "submitted";
        const res = await this.orm.call("sb.planning", "save", [s.mo.month, rows, submit]);
        s.mo = res;
        s.rows = res.tasks.map((t) => ({ ...t }));
        s.check = res.check;
        s.dirty = false;
        this.notification.add(wasSubmitted ? `Changes saved; ${res.approver || "your manager"} sees the new version.` : submit ? `Plan sent to ${res.approver || "your manager"}.` : "Saved.", { type: "success" });
        await this.refreshGate();
    }
    async copyLast() {
        const rows = await this.orm.call("sb.planning", "copy_last", [this.state.mo.month]);
        const empty = this.state.rows.filter((r) => !(r.name || "").trim());
        for (const r of empty) {
            this.state.rows.splice(this.state.rows.indexOf(r), 1);
        }
        for (const r of rows) {
            this.state.rows.push({ ...newRow(this.state.mo.month, this.state.mo.month_end, this.state.mo.weeks), ...r });
        }
        this.state.dirty = true;
        this.notification.add(`${rows.length} tasks copied from last month: change what is different this month.`, { type: "info" });
        await this.runCheck();
    }
    get readiness() {
        return this.state.check ? this.state.check.score : null;
    }
    get blocking() {
        return this.state.check ? this.state.check.blocking : false;
    }
    get rowCount() {
        return this.state.rows.filter((r) => (r.name || "").trim()).length;
    }
    async downloadExcel() {
        const f = await this.orm.call("sb.plan.excel", "template", [this.state.mo.month, true]);
        const bin = atob(f.data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) {
            bytes[i] = bin.charCodeAt(i);
        }
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
        a.download = f.name;
        a.click();
    }
    pickExcel() {
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
                const rows = await this.orm.call("sb.plan.excel", "read", [this.state.mo.month, String(rd.result).split(",")[1]]);
                this.mergeRows(rows, "Excel");
            };
            rd.readAsDataURL(file);
        };
        inp.click();
    }
    mergeRows(rows, from) {
        const s = this.state;
        s.rows = s.rows.filter((r) => (r.name || "").trim());
        let upd = 0;
        let add = 0;
        for (const r of rows) {
            const ex = r.id && s.rows.find((x) => x.id === r.id);
            if (ex) {
                Object.assign(ex, r);
                upd++;
            } else {
                s.rows.push({ ...newRow(s.mo.month, s.mo.month_end, s.mo.weeks), ...r, id: false });
                add++;
            }
        }
        s.dirty = true;
        this.notification.add(`${from}: ${add} new, ${upd} updated. Nothing is saved until you press Save.`, { type: "info" });
        this.runCheck();
    }
    startPaste() {
        this.state.paste = { text: "" };
    }
    applyPaste() {
        const lines = (this.state.paste.text || "").split(/\r?\n/).filter((l) => l.trim());
        const m = this.state.mo;
        const parseD = (v) => {
            v = (v || "").trim();
            if (/^\d{4}-\d{2}-\d{2}$/.test(v)) {
                return v;
            }
            const d = new Date(v);
            return isNaN(d) ? "" : d.toISOString().slice(0, 10);
        };
        const rows = lines.map((l) => {
            const c = l.split("\t");
            const looksNumbered = /^\d+\.?$/.test((c[0] || "").trim()) && c.length > 2;
            const x = looksNumbered ? c.slice(1) : c;
            return { name: (x[0] || "").trim(), done_when: (x[1] || "").trim(), date_from: parseD(x[2]) || m.month,
                date_to: parseD(x[3]) || parseD(x[2]) || m.month_end, output: (x[4] || "").trim() };
        }).filter((r) => r.name);
        this.state.paste = null;
        this.mergeRows(rows, "Pasted");
    }

    // ------------------------------------------------------------------ year
    openPerformance() {
        this.action.doAction("sonador_performance.action_perf", {}).catch(() => {});
    }

    // ------------------------------------------------------------------ team and review
    async openReview(planId) {
        const r = await this.orm.call("sb.planning", "review", [planId]);
        this.state.review = r;
        this.state.decisions = {};
        this.state.reviewNote = "";
        for (const t of r.tasks) {
            const f = (r.check.tasks[t.id] || []);
            this.state.decisions[t.id] = { review: t.review === "returned" ? "returned" : (f.some((x) => x.level === "block") ? "returned" : "approved"),
                note: t.review_note || (f.length ? f.map((x) => x.msg).join(" ") : "") };
        }
    }
    reviewFindings(t) {
        return (this.state.review.check.tasks[t.id] || []);
    }
    setDecision(t, review) {
        this.state.decisions[t.id].review = review;
    }
    async sendDecision() {
        const r = this.state.review;
        const d = {};
        for (const t of r.tasks) {
            if (t.review === "pending" || this.state.decisions[t.id].review !== t.review) {
                d[t.id] = this.state.decisions[t.id];
            }
        }
        this.state.review = await this.orm.call("sb.planning", "decide", [r.plan.id, d, this.state.reviewNote]);
        this.notification.add(this.state.review.plan.state === "approved" ? "Plan approved." : "Sent back with your notes.", { type: "success" });
        this.state.team = await this.orm.call("sb.planning", "team", []);
    }
    async acceptChanges() {
        const r = this.state.review;
        this.state.review = await this.orm.call("sb.planning", "decide_changes", [r.plan.id, r.changes.map((c) => c.id), [], ""]);
        this.notification.add("Changes accepted and applied.", { type: "success" });
        this.state.team = await this.orm.call("sb.planning", "team", []);
    }
    async refuseChanges() {
        const r = this.state.review;
        this.state.review = await this.orm.call("sb.planning", "decide_changes", [r.plan.id, [], r.changes.map((c) => c.id), this.state.refuseNote]);
        this.state.refuseNote = "";
        this.notification.add("Changes refused.", { type: "info" });
        this.state.team = await this.orm.call("sb.planning", "team", []);
    }
    async doSendBack() {
        const r = this.state.review;
        this.state.review = await this.orm.call("sb.planning", "send_back", [r.plan.id, this.state.sendBack]);
        this.state.sendBack = null;
        this.notification.add("Plan sent back with your reason; every line is kept.", { type: "success" });
        this.state.team = await this.orm.call("sb.planning", "team", []);
    }
    approveAllClean() {
        for (const t of this.state.review.tasks) {
            if (!this.reviewFindings(t).some((x) => x.level === "block")) {
                this.state.decisions[t.id].review = "approved";
            }
        }
    }
    async viewPerson(id, page) {
        this.state.viewing = id;
        this.state.mo = null;
        this.state.day = false;
        await this.go(page);
    }
    async backToMe() {
        this.state.viewing = null;
        this.state.mo = null;
        await this.load();
    }

    // ------------------------------------------------------------------ settings
    setCfg(k, v) {
        this.state.set.config[k] = v;
    }
    toggleArea(a) {
        const l = this.state.set.config.open_areas;
        const i = l.indexOf(a);
        if (i >= 0) {
            l.splice(i, 1);
        } else {
            l.push(a);
        }
    }
    async saveSettings() {
        this.state.set = await this.orm.call("sb.planning", "save_settings", [this.state.set.config]);
        this.notification.add("Planning settings saved.", { type: "success" });
        await this.refreshGate();
    }
    async setPerson(p, k, v) {
        await this.orm.call("sb.planning", "set_person", [p.id, { [k]: v }]);
        p[k] = v;
        this.notification.add(`${p.name}: saved.`, { type: "success" });
    }
    get peopleRows() {
        const f = (this.state.peopleFilter || "").toLowerCase();
        const ps = this.state.set.people;
        return f ? ps.filter((p) => (p.name + " " + p.department + " " + p.job).toLowerCase().includes(f)) : ps;
    }
    editRole(r) {
        this.state.roleEdit = r ? JSON.parse(JSON.stringify(r)) : { id: false, name: "", description: "", items: [] };
        if (!this.state.roleEdit.items.length) {
            this.state.roleEdit.items.push({ id: false, name: "", done_when: "", target_qty: "", unit: "", mandatory: true });
        }
    }
    addRoleItem() {
        this.state.roleEdit.items.push({ id: false, name: "", done_when: "", target_qty: "", unit: "", mandatory: true });
    }
    async saveRole() {
        this.state.set = await this.orm.call("sb.planning", "save_role", [this.state.roleEdit]);
        this.state.roleEdit = null;
        this.notification.add("Role saved.", { type: "success" });
    }
    async removeRole(r) {
        this.state.set = await this.orm.call("sb.planning", "remove_role", [r.id]);
    }
    async saveCat() {
        this.state.set = await this.orm.call("sb.planning", "save_category", [this.state.catEdit]);
        this.state.catEdit = null;
    }
    async removeCat(c) {
        this.state.set = await this.orm.call("sb.planning", "remove_category", [c.id]);
    }
}

registry.category("actions").add("sonador_planning.app", PlanningApp);

/** The gate on the screen: locked people land on Planning; people who owe something get one reminder per session. */
registry.category("services").add("planning_gate", {
    dependencies: ["action", "notification"],
    start(env, { action, notification }) {
        const g = session.planning_gate;
        if (!g || !(g.reasons || []).length) {
            return;
        }
        setTimeout(() => {
            if (g.locked) {
                action.doAction(g.action, { clearBreadcrumbs: true });
            }
            notification.add((g.locked ? "Planning first: " : "Planning: ") + g.reasons.map((r) => r.msg).join(" "), {
                type: g.locked ? "danger" : "warning", sticky: g.locked,
                buttons: [{ name: "Open Planning", primary: true, onClick: () => action.doAction(g.action) }],
            });
        }, 1200);
    },
});
