import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const fmt = (v, d = 0) =>
    v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

/**
 * Performance (Lattice-style): goals and OKRs, KPI scorecards, review cycles with calibration and a 9-box,
 * weekly check-ins, one-to-ones, praise and feedback.
 */
export class PerfApp extends Component {
    static template = "sonador_performance.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "overview", busy: false,
            ov: null,
            goals: { scope: "all", list: [], parents: [], edit: null, open: {} },
            sc: { employee: p.employee_id || null, month: null, data: null, edit: null },
            cyc: { list: [], templates: [], cur: null, review: null, form: null, create: null, tab: "people", raiseDate: "" },
            ci: { data: null, form: null, reply: {} },
            oo: { data: null, cur: null },
            wall: { list: [], form: { to_id: "", kind: "praise", value: "", text: "", visibility: "public" } },
            kpi: { list: [], edit: null },
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.perf", "ctx", []);
            this.state.sc.employee = this.state.sc.employee || this.state.ctx.me || (this.state.ctx.people[0] && this.state.ctx.people[0].id) || null;
            this.state.sc.month = this.state.ctx.months[1] ? this.state.ctx.months[1].key : this.state.ctx.months[0].key;
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    fmt(v, d) {
        return fmt(v, d);
    }
    pctClass(v) {
        if (v === null || v === undefined) {
            return "";
        }
        return v >= 80 ? "o_pf_hi" : v >= 60 ? "o_pf_mid" : "o_pf_lo";
    }
    statusLabel(s) {
        return (this.ctx.status.find((x) => x.id === s) || {}).name || s;
    }
    roleLabel(r) {
        return { self: "Self", manager: "Manager", peer: "Peer" }[r] || r;
    }
    stateLabel(s) {
        return { draft: "To do", submitted: "Submitted", acknowledged: "Acknowledged", open: "In progress",
            calibration: "Calibration", closed: "Closed" }[s] || s;
    }

    async go(page) {
        this.state.page = page;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "overview") {
                s.ov = await this.orm.call("sb.perf", "overview", []);
            } else if (s.page === "goals") {
                const r = await this.orm.call("sb.perf", "goals", [s.goals.scope]);
                s.goals.list = r.goals;
                s.goals.parents = r.parents;
            } else if (s.page === "scorecards") {
                if (s.sc.employee) {
                    s.sc.data = await this.orm.call("sb.perf", "scorecard", [s.sc.employee, s.sc.month]);
                }
            } else if (s.page === "reviews") {
                const r = await this.orm.call("sb.perf", "cycles", []);
                s.cyc.list = r.cycles;
                s.cyc.templates = r.templates;
                s.cyc.cur = null;
                s.cyc.review = null;
            } else if (s.page === "checkins") {
                s.ci.data = await this.orm.call("sb.perf", "checkins", []);
                const m = s.ci.data.mine || {};
                s.ci.form = { mood: m.mood || "4", wins: m.wins || "", blockers: m.blockers || "", priorities: m.priorities || "" };
            } else if (s.page === "one2ones") {
                s.oo.data = await this.orm.call("sb.perf", "one2ones", []);
            } else if (s.page === "praise") {
                s.wall.list = await this.orm.call("sb.perf", "wall", [], { limit: 60 });
            } else if (s.page === "kpis") {
                s.kpi.list = await this.orm.call("sb.perf", "kpis", []);
            }
        } finally {
            s.busy = false;
        }
    }
    openPeople(id) {
        return this.action.doAction({ type: "ir.actions.client", tag: "sonador_people.app", params: { page: "profile", employee_id: id } });
    }

    async openScorecard(id) {
        this.state.sc.employee = id;
        await this.go("scorecards");
    }
    closeCycle() {
        this.state.cyc.cur = null;
    }
    closeReview() {
        this.state.cyc.review = null;
    }
    setReply(id, v) {
        this.state.ci.reply[id] = v;
    }
    addAssign() {
        this.state.kpi.edit.assign.push({ employee_id: "", department_id: "", job_id: "", weight: 1, target: 0 });
    }

    // ------------------------------------------------------------------ goals
    get goalTree() {
        const list = this.state.goals.list.filter((g) => g.status !== "dropped");
        const ids = new Set(list.map((g) => g.id));
        const kids = {};
        for (const g of list) {
            const k = g.parent_id && ids.has(g.parent_id) ? g.parent_id : "root";
            (kids[k] = kids[k] || []).push(g);
        }
        const out = [];
        const walk = (k, depth) => {
            for (const g of kids[k] || []) {
                out.push({ g, depth });
                walk(g.id, depth + 1);
            }
        };
        walk("root", 0);
        return out;
    }
    async setScope(scope) {
        this.state.goals.scope = scope;
        await this.load();
    }
    newGoal(level = "person", parent = null) {
        this.state.goals.edit = { id: false, name: "", description: "", level, employee_id: this.ctx.me || "", department_id: "",
            parent_id: parent || "", date_from: this.ctx.today, date_to: "", status: "on_track", private: false, progress_manual: 0,
            krs: [{ name: "", start_value: 0, target_value: 100, current_value: 0, unit: "" }] };
    }
    editGoal(g) {
        this.state.goals.edit = { ...g, employee_id: g.owner ? g.owner.id : "", krs: g.krs.map((k) => ({ ...k })) };
    }
    addKr() {
        this.state.goals.edit.krs.push({ name: "", start_value: 0, target_value: 100, current_value: 0, unit: "", measure: "manual", kpi_id: "", rollup: "sum" });
    }
    async saveGoal() {
        await this.orm.call("sb.perf", "goal_save", [this.state.goals.edit]);
        this.state.goals.edit = null;
        this.notification.add("Goal saved.", { type: "success" });
        await this.load();
    }
    async dropGoal(g) {
        await this.orm.call("sb.perf", "goal_delete", [g.id]);
        this.state.goals.edit = null;
        await this.load();
    }

    // ------------------------------------------------------------------ scorecards
    async pickScorecard() {
        await this.load();
    }
    editScores() {
        this.state.sc.edit = this.state.sc.data.rows.map((r) => ({ ...r, actual: r.actual === null ? "" : r.actual }));
    }
    async saveScores() {
        const s = this.state.sc;
        s.data = await this.orm.call("sb.perf", "scorecard_save", [s.employee, s.month, s.edit]);
        s.edit = null;
        this.notification.add("Results saved.", { type: "success" });
    }
    async computeAuto(all = false) {
        const s = this.state.sc;
        const n = await this.orm.call("sb.perf", "scorecard_compute", [s.month], all ? {} : { employee_id: s.employee });
        this.notification.add(`Computed from the books for ${n} ${n === 1 ? "person" : "people"}.`, { type: "success" });
        await this.load();
    }
    get trendChart() {
        const t = (this.state.sc.data && this.state.sc.data.trend) || [];
        return { labels: t.map((x) => x.label), series: [{ label: "Score %", data: t.map((x) => x.score || 0) }] };
    }

    // ------------------------------------------------------------------ reviews
    newCycle() {
        const tpl = this.state.cyc.templates[0];
        this.state.cyc.create = { name: "", kind: "bi_annual", date_from: "", date_to: "", deadline: "", template_id: tpl ? tpl.id : "",
            self_review: true, peer_review: false, department_id: "" };
    }
    async createCycle() {
        const c = await this.orm.call("sb.perf", "cycle_create", [this.state.cyc.create]);
        this.state.cyc.create = null;
        this.notification.add("Review cycle created for everyone in scope.", { type: "success" });
        await this.load();
        await this.openCycle(c.id);
    }
    async openCycle(id) {
        this.state.cyc.cur = await this.orm.call("sb.perf", "cycle", [id]);
        this.state.cyc.review = null;
        this.state.cyc.tab = "people";
    }
    async cycleState(state) {
        this.state.cyc.cur = await this.orm.call("sb.perf", "cycle_state", [this.state.cyc.cur.cycle.id, state]);
    }
    async openReview(id) {
        const r = await this.orm.call("sb.perf", "review", [id]);
        this.state.cyc.review = r;
        this.state.cyc.form = { lines: r.lines.map((l) => ({ ...l })), strengths: r.strengths, improvements: r.improvements,
            comment: r.comment, potential: r.potential || "", raise: r.raise || 0, ack: "" };
    }
    setScore(line, v) {
        line.score = v;
    }
    get formPct() {
        const f = this.state.cyc.form;
        if (!f) {
            return null;
        }
        const scored = f.lines.filter((l) => l.score);
        const w = scored.reduce((a, l) => a + l.weight, 0);
        return w ? Math.round((scored.reduce((a, l) => a + l.score * l.weight, 0) / (5 * w)) * 1000) / 10 : null;
    }
    async saveReview(submit) {
        const r = this.state.cyc.review;
        this.state.cyc.review = await this.orm.call("sb.perf", "review_save", [r.id, this.state.cyc.form, submit]);
        this.notification.add(submit ? "Review submitted." : "Saved as draft.", { type: "success" });
        if (this.state.cyc.cur) {
            this.state.cyc.cur = await this.orm.call("sb.perf", "cycle", [this.state.cyc.cur.cycle.id]);
        }
    }
    async acknowledge() {
        const r = this.state.cyc.review;
        this.state.cyc.review = await this.orm.call("sb.perf", "review_acknowledge", [r.id, this.state.cyc.form.ack]);
        this.notification.add("Acknowledged.", { type: "success" });
    }
    async openMyReview(r) {
        this.state.page = "reviews";
        await this.load();
        await this.openCycle(r.cycle_id);
        await this.openReview(r.id);
    }
    nineCell(pot, perf) {
        const c = this.state.cyc.cur && this.state.cyc.cur.nine_box;
        return (c && c[`${pot}${perf}`]) || [];
    }
    nineLabel(pot, perf) {
        return {
            "33": "Star", "32": "High potential", "31": "Enigma", "23": "High performer", "22": "Core player", "21": "Inconsistent",
            "13": "Trusted professional", "12": "Effective", "11": "Under-performer",
        }[`${pot}${perf}`];
    }
    async applyRaises() {
        const c = this.state.cyc;
        if (!c.raiseDate) {
            this.notification.add("Pick the date the raises take effect.", { type: "warning" });
            return;
        }
        const n = await this.orm.call("sb.perf", "apply_raises", [c.cur.cycle.id, c.raiseDate]);
        this.notification.add(`${n} pay changes recorded in People from ${c.raiseDate}.`, { type: "success" });
    }
    get raiseRows() {
        const c = this.state.cyc.cur;
        return c ? c.rows.filter((r) => r.manager && r.manager.raise) : [];
    }

    // ------------------------------------------------------------------ check-ins
    async saveCheckin() {
        this.state.ci.data = await this.orm.call("sb.perf", "checkin_save", [this.state.ci.form]);
        this.notification.add("Check-in sent to your manager.", { type: "success" });
    }
    async reply(ci) {
        const text = this.state.ci.reply[ci.id];
        if (!text) {
            return;
        }
        this.state.ci.data = await this.orm.call("sb.perf", "checkin_reply", [ci.id, text]);
        this.state.ci.reply[ci.id] = "";
    }
    moodIcon(m) {
        return { 1: "😣", 2: "😕", 3: "😐", 4: "🙂", 5: "😄" }[m] || "🙂";
    }

    // ------------------------------------------------------------------ one-to-ones
    newMeeting(person) {
        this.state.oo.cur = { id: false, with_id: person.id, with: person.name, date: this.ctx.today, notes: "", done: false,
            items: [{ kind: "agenda", name: "", done: false }] };
    }
    openMeeting(m) {
        const other = m.employee.id === this.ctx.me ? m.manager : m.employee;
        this.state.oo.cur = { ...m, with: other.name, items: m.items.map((i) => ({ ...i })) };
    }
    addItem(kind) {
        this.state.oo.cur.items.push({ kind, name: "", done: false });
    }
    async saveMeeting() {
        await this.orm.call("sb.perf", "one2one_save", [this.state.oo.cur]);
        this.state.oo.cur = null;
        this.notification.add("One-to-one saved.", { type: "success" });
        await this.load();
    }

    // ------------------------------------------------------------------ praise
    async give() {
        this.state.wall.list = await this.orm.call("sb.perf", "give_feedback", [this.state.wall.form]);
        this.state.wall.form = { to_id: "", kind: "praise", value: "", text: "", visibility: "public" };
        this.notification.add("Sent.", { type: "success" });
    }

    // ------------------------------------------------------------------ KPI setup (HR)
    newKpi() {
        this.state.kpi.edit = { id: false, name: "", code: "", department_id: "", unit: "", target: 100, source: "manual", auto_key: "",
            frequency: "monthly", bands: ">=0.95=5; >=0.85=4; >=0.75=3; >=0.6=2; <0.6=1", higher_is_better: true, active: true,
            levels: [], assign: [{ employee_id: "", department_id: "", job_id: "", weight: 1, target: 0 }] };
    }
    editKpi(k) {
        this.state.kpi.edit = { ...k, assign: k.assign.map((a) => ({ ...a })) };
    }
    openBackend(xmlid) {
        return this.action.doAction(xmlid);
    }
    openKpiForm(id) {
        return this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.perf.kpi", res_id: id, views: [[false, "form"]] });
    }
    async saveKpi() {
        this.state.kpi.list = await this.orm.call("sb.perf", "kpi_save", [this.state.kpi.edit]);
        this.state.kpi.edit = null;
        this.notification.add("KPI saved.", { type: "success" });
    }
}

registry.category("actions").add("sonador_performance.app", PerfApp);
