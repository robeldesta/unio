import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Onboarding and offboarding checklists, my tasks, policies, set-up. */
export class OnboardingApp extends Component {
    static template = "sonador_onboarding.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "overview", busy: false,
            kind: "", list: [], cur: null, notes: {}, iv: null,
            start: null, mine: [], pol: null, polEdit: null, tpl: null, tplEdit: null, owners: null,
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.onboarding", "ctx", []);
            if (!this.state.ctx.is_hr && this.state.page === "overview") {
                this.state.page = "mine";
            }
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    async go(page) {
        this.state.page = page;
        this.state.cur = null;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "overview") {
                s.list = await this.orm.call("sb.onboarding", "overview", [s.kind || false]);
            } else if (s.page === "mine") {
                s.mine = await this.orm.call("sb.onboarding", "my_tasks", []);
                s.pol = this.withHtml(await this.orm.call("sb.onboarding", "policies", []));
            } else if (s.page === "policies") {
                s.pol = this.withHtml(await this.orm.call("sb.onboarding", "policies", []));
            } else if (s.page === "setup") {
                s.tpl = await this.orm.call("sb.onboarding", "templates", []);
                s.owners = { ...s.ctx.owners };
            }
        } finally {
            s.busy = false;
        }
    }
    withHtml(pol) {
        for (const p of pol.policies) {
            p.html = markup(p.body || "");
        }
        return pol;
    }
    async setKind(k) {
        this.state.kind = k;
        await this.load();
    }
    async open(id) {
        const j = await this.orm.call("sb.onboarding", "journey", [id]);
        this.state.cur = j;
        this.state.iv = { ...j.interview };
        this.state.page = "journey";
    }
    setNote(id, v) {
        this.state.notes[id] = v;
    }
    async toggle(t) {
        const res = await this.orm.call("sb.onboarding", "task_done", [t.id, !t.done, this.state.notes[t.id] || ""]);
        if (this.state.page === "journey") {
            this.state.cur = res;
        } else {
            await this.load();
        }
        if (!t.done && t.action === "settlement") {
            this.notification.add("Open Payroll > Final settlement to review the amounts.", { type: "info" });
        }
    }
    certificate(employeeId) {
        window.open(`/report/pdf/sonador_onboarding.certificate/${employeeId}`, "_blank");
    }
    async saveInterview() {
        this.state.cur = await this.orm.call("sb.onboarding", "interview_save", [this.state.cur.id, this.state.iv]);
        this.notification.add("Exit interview saved.", { type: "success" });
    }
    openProfile(id) {
        return this.action.doAction({ type: "ir.actions.client", tag: "sonador_people.app", params: { page: "profile", employee_id: id } });
    }
    newStart() {
        this.state.start = { employee_id: "", kind: "onboarding", day: "" };
    }
    async doStart() {
        const s = this.state.start;
        const id = await this.orm.call("sb.onboarding", "start_manual", [s.employee_id, s.kind, s.day || false]);
        this.state.start = null;
        await this.open(id);
    }
    roleIcon(r) {
        return { hr: "fa-id-badge", manager: "fa-user-circle", it: "fa-laptop", finance: "fa-money", store: "fa-cubes", employee: "fa-user" }[r] || "fa-circle";
    }

    // policies
    async ack(p) {
        this.state.pol = this.withHtml(await this.orm.call("sb.onboarding", "acknowledge", [p.id]));
        this.notification.add(`"${p.name}" acknowledged.`, { type: "success" });
    }
    editPolicy(p) {
        this.state.polEdit = p ? { id: p.id, name: p.name, version: p.version, body: p.body } : { id: false, name: "", version: "1", body: "" };
    }
    onFile(ev) {
        const file = ev.target.files[0];
        if (!file) {
            return;
        }
        const r = new FileReader();
        r.onload = () => {
            this.state.polEdit.file_data = String(r.result).split(",")[1];
            this.state.polEdit.file_name = file.name;
        };
        r.readAsDataURL(file);
    }
    async savePolicy() {
        this.state.pol = this.withHtml(await this.orm.call("sb.onboarding", "policy_save", [this.state.polEdit]));
        this.state.polEdit = null;
        this.notification.add("Policy published. Changing the version asks everyone to acknowledge it again.", { type: "success" });
    }

    // set-up
    editTpl(t) {
        this.state.tplEdit = { ...t, tasks: t.tasks.map((x) => ({ ...x })) };
    }
    addTask() {
        this.state.tplEdit.tasks.push({ name: "", role: "hr", offset: 0, action: "", legal_ref: "" });
    }
    removeTask(i) {
        this.state.tplEdit.tasks.splice(i, 1);
    }
    async saveTpl() {
        this.state.tpl = await this.orm.call("sb.onboarding", "template_save", [this.state.tplEdit]);
        this.state.tplEdit = null;
        this.notification.add("Checklist saved (applies to new onboardings and offboardings).", { type: "success" });
    }
    setOwner(role, value) {
        this.state.owners[role] = value;
    }
    async saveOwners() {
        this.state.ctx = await this.orm.call("sb.onboarding", "owners_save", [this.state.owners]);
        this.notification.add("Task owners saved.", { type: "success" });
    }
}

registry.category("actions").add("sonador_onboarding.app", OnboardingApp);
