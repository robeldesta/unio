import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

/** Recruitment: openings, pipeline by stage, candidate drawer, interviews and scorecards, hire into People. */
export class RecruitApp extends Component {
    static template = "sonador_recruit.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "openings", busy: false,
            open: null, jobEdit: null, showClosed: false,
            pipe: null, job: null, search: "", archived: false,
            app: null, appEdit: null, iv: null, reject: "", hire: null,
            ivs: [], stats: null,
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.recruit", "ctx", []);
            if (!this.state.ctx.is_hr) {
                this.state.page = "interviews";
            }
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    money(v) {
        return v ? Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "";
    }
    stageLabel(k) {
        return (this.ctx.stages.find((s) => s.id === k) || {}).name || k;
    }
    async go(page) {
        this.state.page = page;
        this.state.app = null;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "openings") {
                s.open = await this.orm.call("sb.recruit", "openings", [s.showClosed]);
            } else if (s.page === "pipeline") {
                s.pipe = await this.orm.call("sb.recruit", "pipeline", [s.job, s.search, s.archived]);
            } else if (s.page === "interviews") {
                s.ivs = await this.orm.call("sb.recruit", "my_interviews", []);
            } else if (s.page === "stats") {
                s.stats = await this.orm.call("sb.recruit", "analytics", []);
            }
        } finally {
            s.busy = false;
        }
    }
    async toggleClosed() {
        this.state.showClosed = !this.state.showClosed;
        await this.load();
    }
    async openJob(id) {
        this.state.job = id;
        this.state.search = "";
        this.state.archived = false;
        await this.go("pipeline");
    }
    editJob(j) {
        this.state.jobEdit = j ? { ...j } : { id: false, name: "", open: true, openings: 1, department_id: "", manager_id: "", close: "",
            salary_from: 0, salary_to: 0, description: "", requirements: "" };
    }
    async saveJob() {
        const id = await this.orm.call("sb.recruit", "opening_save", [this.state.jobEdit]);
        this.state.jobEdit = null;
        this.notification.add("Opening saved.", { type: "success" });
        if (this.state.page === "pipeline") {
            this.state.job = id;
        }
        await this.load();
    }
    async onSearch() {
        await this.load();
    }
    async toggleArchived() {
        this.state.archived = !this.state.archived;
        await this.load();
    }

    // ------------------------------------------------------------------ application drawer
    async openApp(id) {
        this.state.app = await this.orm.call("sb.recruit", "application", [id]);
        this.state.appEdit = null;
        this.state.iv = null;
        this.state.hire = null;
        this.state.reject = "";
    }
    closeApp() {
        this.state.app = null;
    }
    newApp() {
        this.state.app = { id: false, new: true };
        this.state.appEdit = { id: false, job_id: this.state.job || "", expected: 0, proposed: 0, rating: 0, notes: "",
            candidate: { name: "", email: "", phone: "", linkedin: "", education: "", experience_years: 0, city: "", notes: "", source: "" } };
    }
    editApp() {
        const a = this.state.app;
        this.state.appEdit = { id: a.id, job_id: a.job_id || "", expected: a.expected, proposed: a.proposed, rating: a.rating, notes: a.notes,
            candidate: { ...a.candidate } };
    }
    cancelEdit() {
        if (this.state.app && this.state.app.new) {
            this.closeApp();
        } else {
            this.state.appEdit = null;
        }
    }
    onCv(ev) {
        const file = ev.target.files[0];
        if (!file) {
            return;
        }
        const r = new FileReader();
        r.onload = () => {
            this.state.appEdit.candidate.cv_data = String(r.result).split(",")[1];
            this.state.appEdit.candidate.cv_name = file.name;
        };
        r.readAsDataURL(file);
    }
    async saveApp() {
        this.state.app = await this.orm.call("sb.recruit", "application_save", [this.state.appEdit]);
        this.state.appEdit = null;
        this.notification.add("Saved.", { type: "success" });
        await this.load();
    }
    async move(stage) {
        await this.orm.call("sb.recruit", "move", [this.state.app.id, stage]);
        await this.openApp(this.state.app.id);
        await this.load();
    }
    async rate(n) {
        await this.orm.call("sb.recruit", "rate", [this.state.app.id, n]);
        this.state.app.rating = n;
        await this.load();
    }
    async doReject() {
        await this.orm.call("sb.recruit", "reject", [this.state.app.id, this.state.reject]);
        this.notification.add("Moved out of the pipeline.", { type: "warning" });
        this.state.app = null;
        await this.load();
    }
    async doRestore() {
        await this.orm.call("sb.recruit", "restore", [this.state.app.id]);
        await this.openApp(this.state.app.id);
        await this.load();
    }
    newInterview() {
        this.state.iv = { id: false, application_id: this.state.app.id, when: `${this.ctx.today}T10:00`, kind: "panel", place: "", interviewer_ids: [] };
    }
    editInterview(i) {
        this.state.iv = { ...i, interviewer_ids: i.interviewers.map((x) => x.id), when: i.when.replace(" ", "T").slice(0, 16) };
    }
    toggleInterviewer(id) {
        const list = this.state.iv.interviewer_ids;
        const k = list.indexOf(id);
        if (k >= 0) {
            list.splice(k, 1);
        } else {
            list.push(id);
        }
    }
    setScore(key, n) {
        this.state.iv[key] = n;
    }
    async saveInterview() {
        await this.orm.call("sb.recruit", "interview_save", [this.state.iv]);
        this.state.iv = null;
        this.notification.add("Interview saved.", { type: "success" });
        if (this.state.app && this.state.app.id) {
            await this.openApp(this.state.app.id);
        }
        await this.load();
    }
    startHire() {
        const a = this.state.app;
        this.state.hire = { start: this.ctx.today, wage: a.proposed || a.expected || 0, department_id: "", parent_id: "", type: "employee", email: "", contract_end: "" };
    }
    async doHire() {
        const eid = await this.orm.call("sb.recruit", "hire", [this.state.app.id, this.state.hire]);
        this.notification.add(`${this.state.app.name} hired: added to People, onboarding started.`, { type: "success" });
        this.state.hire = null;
        await this.load();
        return this.action.doAction({ type: "ir.actions.client", tag: "sonador_people.app", params: { page: "profile", employee_id: eid } });
    }
    get monthChart() {
        const b = (this.state.stats && this.state.stats.by_month) || { labels: [], values: [] };
        return { labels: b.labels, series: [{ label: "Applications", data: b.values }] };
    }
}

registry.category("actions").add("sonador_recruit.app", RecruitApp);
