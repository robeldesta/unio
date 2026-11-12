import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const money = (v) =>
    v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** One node of the org chart (recursive). */
class OrgNode extends Component {
    static template = "sonador_people.OrgNode";
    static components = {};
    static props = ["node", "children", "open", "toggle", "openProfile", "depth"];
    joinTxt(...parts) {
        return parts.filter((x) => x).join(" · ");
    }
    get kids() {
        return this.props.children[this.props.node.id] || [];
    }
    get isOpen() {
        return this.props.open[this.props.node.id] ?? this.props.depth < 2;
    }
    count(id) {
        const kids = this.props.children[id] || [];
        return kids.reduce((n, k) => n + 1 + this.count(k.id), 0);
    }
}
OrgNode.components = { OrgNode };

/**
 * People: home, directory, org chart and the employee profile (BambooHR-style), on Odoo employees.
 * HR officers change everything; every employee sees the directory and their own profile.
 */
export class PeopleApp extends Component {
    static template = "sonador_people.App";
    static components = { SbChart, OrgNode };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "home", employeeId: p.employee_id || null, busy: false,
            home: null,
            dir: { rows: [], search: "", department: "", type: "", status: "active", view: "cards", ids: null, label: "" },
            org: { nodes: [], open: {}, search: "" },
            prof: null, tab: "overview", edit: null, panel: null, form: {}, rows: null, reasons: [], login: null,
            hire: {},
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.people", "ctx", []);
            if (this.state.page === "me") {
                if (this.state.ctx.me) {
                    this.state.employeeId = this.state.ctx.me;
                    this.state.page = "profile";
                }
            }
            await this.load();
        });
    }

    get ctx() {
        return this.state.ctx;
    }
    money(v) {
        return money(v);
    }
    joinTxt(...parts) {
        return parts.filter((x) => x).join(" · ");
    }

    // ------------------------------------------------------------------ navigation
    async go(page, extra = {}) {
        Object.assign(this.state, { page, edit: null, panel: null, rows: null }, extra);
        if (page === "directory" && !("ids" in extra)) {
            this.state.dir.ids = null;
            this.state.dir.label = "";
        }
        await this.load();
    }
    async openProfile(id) {
        await this.go("profile", { employeeId: id, tab: "overview" });
    }
    async myProfile() {
        if (this.ctx.me) {
            await this.openProfile(this.ctx.me);
        } else {
            this.state.page = "noprofile";
        }
    }

    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "home") {
                s.home = await this.orm.call("sb.people", "home", []);
            } else if (s.page === "directory") {
                await this.loadDirectory();
            } else if (s.page === "org") {
                s.org.nodes = (await this.orm.call("sb.people", "org_chart", [])).nodes;
            } else if (s.page === "profile") {
                s.prof = await this.orm.call("sb.people", "profile", [s.employeeId]);
                s.login = s.prof.level === "hr" ? await this.orm.call("sb.people", "login_info", [s.prof.card.id]) : null;
            } else if (s.page === "hire") {
                if (!s.org.nodes.length) {
                    s.org.nodes = (await this.orm.call("sb.people", "org_chart", [])).nodes;
                }
                s.hire = { name: "", job: "", department_id: "", parent_id: "", start: this.ctx.today, type: "employee",
                    wage: "", house: "", position: "", cash: "", taxable: "", nontaxable: "", email: "", phone: "", contract_end: "" };
            }
        } finally {
            s.busy = false;
        }
    }

    // ------------------------------------------------------------------ home
    get deptChart() {
        const d = (this.state.home && this.state.home.by_dept) || [];
        return { labels: d.map((x) => x[0]), series: [{ label: "People", data: d.map((x) => x[1]) }] };
    }
    get typeChart() {
        const d = (this.state.home && this.state.home.by_type) || [];
        return { labels: d.map((x) => x[0]), series: [{ label: "People", data: d.map((x) => x[1]) }] };
    }
    showMissing(m) {
        this.state.dir.ids = m.ids;
        this.state.dir.label = `Missing: ${m.label}`;
        this.go("directory", { ids: m.ids });
    }
    dayLabel(iso) {
        if (!iso) {
            return "";
        }
        const d = new Date(iso + "T00:00:00");
        return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    }
    eventIcon(kind) {
        return { birthday: "fa-birthday-cake", anniversary: "fa-trophy", probation: "fa-hourglass-half", contract: "fa-file-text-o",
            permit: "fa-id-card-o", document: "fa-file-o" }[kind] || "fa-circle";
    }

    // ------------------------------------------------------------------ directory
    async loadDirectory() {
        const d = this.state.dir;
        const res = await this.orm.call("sb.people", "directory", [], {
            search: d.search, department_id: d.department || null, emp_type: d.type || null, status: d.status, ids: d.ids,
        });
        d.rows = res.rows;
    }
    async onDirFilter() {
        await this.loadDirectory();
    }
    clearIds() {
        this.state.dir.ids = null;
        this.state.dir.label = "";
        this.loadDirectory();
    }

    // ------------------------------------------------------------------ org chart
    get orgChildren() {
        const map = {};
        for (const n of this.state.org.nodes) {
            const k = n.parent_id || "root";
            (map[k] = map[k] || []).push(n);
        }
        return map;
    }
    get orgRoots() {
        // the tree: people at the top who have a team
        const ch = this.orgChildren;
        return (ch.root || []).filter((n) => (ch[n.id] || []).length);
    }
    get orgUnplaced() {
        // no manager and no team: not in the reporting tree yet
        const ch = this.orgChildren;
        return (ch.root || []).filter((n) => !(ch[n.id] || []).length);
    }
    toggleOrg(id, open) {
        this.state.org.open[id] = !open;
    }
    expandAll(v) {
        for (const n of this.state.org.nodes) {
            this.state.org.open[n.id] = v;
        }
    }

    // ------------------------------------------------------------------ profile
    get prof() {
        return this.state.prof;
    }
    get canEdit() {
        return this.prof && (this.prof.level === "hr" || this.prof.level === "self");
    }
    get isHR() {
        return this.prof && this.prof.level === "hr";
    }
    get tabs() {
        const t = [["overview", "Overview", "fa-user"]];
        if (this.prof.job) {
            t.push(["job", "Job & Pay", "fa-briefcase"]);
        }
        if (this.prof.contacts) {
            t.push(["contacts", "Emergency Contacts", "fa-phone"], ["documents", "Documents", "fa-folder-open-o"],
                ["assets", "Assets", "fa-laptop"], ["timeline", "Timeline", "fa-history"]);
        }
        if (this.prof.reports.length) {
            t.push(["team", `Team (${this.prof.reports.length})`, "fa-sitemap"]);
        }
        return t;
    }
    setTab(t) {
        this.state.tab = t;
        this.state.edit = null;
        this.state.panel = null;
        this.state.rows = null;
    }
    label(list, id) {
        const x = (list || []).find((r) => String(r.id) === String(id));
        return x ? x.name : "";
    }
    regionLabel(id) {
        return this.label(this.ctx.regions, id);
    }

    async setLogin(userId) {
        this.state.login = await this.orm.call("sb.people", "set_login", [this.prof.card.id, userId ? Number(userId) : false]);
        this.notification.add(userId ? "Login linked." : "Login removed.", { type: "success" });
    }
    async newLogin() {
        this.state.login = await this.orm.call("sb.people", "new_login", [this.prof.card.id]);
        this.notification.add(`Login ${this.state.login.user.login} created. Set its password in Settings › Users.`, { type: "success", sticky: true });
    }
    gross(c) {
        return ["wage", "house", "position", "cash", "taxable", "nontaxable"].reduce((a, k) => a + (Number(c[k]) || 0), 0);
    }
    startEdit() {
        this.state.edit = { ...this.prof.personal };
        const b = this.prof.personal.bank || {};
        this.state.edit.bank_acc = b.acc || "";
        this.state.edit.bank_name = b.bank || "";
    }
    setEdit(key, value) {
        this.state.edit[key] = value;
    }
    async saveEdit() {
        const p = this.prof.personal;
        const e = this.state.edit;
        const vals = {};
        const keys = this.isHR
            ? Object.keys(e).filter((k) => !["bank", "birthday_ec", "age", "bank_acc", "bank_name"].includes(k))
            : ["private_phone", "private_email", "sb_region", "sb_subcity", "sb_woreda", "sb_kebele", "sb_house_no", "marital",
                "children", "mobile_phone"];
        for (const k of keys) {
            if ((e[k] ?? "") !== (p[k] ?? "")) {
                vals[k] = k === "children" ? parseInt(e[k] || 0) : e[k];
            }
        }
        if (this.isHR && ((p.bank && p.bank.acc) || "") !== (e.bank_acc || "")) {
            vals.bank_acc = e.bank_acc;
            vals.bank_name = e.bank_name;
        }
        this.state.busy = true;
        try {
            this.state.prof = await this.orm.call("sb.people", "save_personal", [this.prof.card.id, vals]);
            this.state.edit = null;
            this.notification.add("Saved.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }

    async openPanel(kind) {
        this.state.panel = kind;
        const j = this.prof.job || {};
        if (kind === "change") {
            const cur = this.prof.history.find((h) => h.current) || {};
            if (!this.state.org.nodes.length) {
                this.state.org.nodes = (await this.orm.call("sb.people", "org_chart", [])).nodes;
            }
            this.state.form = { date: this.ctx.today, job: j.job || "", department_id: j.department_id || "", parent_id: j.parent_id || "",
                wage: cur.wage || 0, house: cur.house || 0, position: cur.position || 0, cash: cur.cash || 0,
                taxable: cur.taxable || 0, nontaxable: cur.nontaxable || 0, reason: "salary", grade: j.grade || "",
                contract_end: j.contract_end || "" };
        } else if (kind === "leave") {
            this.state.reasons = await this.orm.call("sb.people", "departure_reasons", []);
            this.state.form = { date: this.ctx.today, reason_id: "", note: "" };
        }
    }
    openFullRecord() {
        // the standard employee form: tabs other apps add (Telegram link, documents...) live there
        this.action.doAction({ type: "ir.actions.act_window", res_model: "hr.employee", res_id: this.state.employeeId,
                               views: [[false, "form"]], target: "current" });
    }
    async changeJob() {
        this.setTab("job");
        await this.openPanel("change");
    }
    async saveChange() {
        const f = this.state.form;
        this.state.busy = true;
        try {
            this.state.prof = await this.orm.call("sb.people", "job_change", [this.prof.card.id, {
                date: f.date, job: f.job, department_id: f.department_id || false, parent_id: f.parent_id || false,
                wage: f.wage, house: f.house, position: f.position, cash: f.cash, taxable: f.taxable, nontaxable: f.nontaxable,
                reason: f.reason, grade: f.grade,
                contract_end: f.contract_end || false,
            }]);
            this.state.panel = null;
            this.state.ctx = await this.orm.call("sb.people", "ctx", []);
            this.notification.add("Change recorded from " + f.date + ". History kept.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
    async saveLeave() {
        const f = this.state.form;
        this.state.busy = true;
        try {
            this.state.prof = await this.orm.call("sb.people", "depart", [this.prof.card.id, f]);
            this.state.panel = null;
            this.notification.add("Leaving recorded.", { type: "warning" });
        } finally {
            this.state.busy = false;
        }
    }
    async setStart(value) {
        if (!value) {
            return;
        }
        this.state.prof = await this.orm.call("sb.people", "set_start", [this.prof.card.id, value]);
        this.notification.add("Start date saved: tenure, leave, probation and severance count from it.", { type: "success" });
    }
    async confirmProbation() {
        this.state.prof = await this.orm.call("sb.people", "confirm_probation", [this.prof.card.id]);
        this.notification.add("Probation completed: employment confirmed.", { type: "success" });
    }

    // editable lists: contacts, documents, assets
    editRows(kind) {
        this.state.rows = { kind, items: (this.prof[kind] || []).map((r) => ({ ...r })) };
    }
    addRow() {
        const k = this.state.rows.kind;
        const blank = {
            contacts: { name: "", relationship: "relative", phone: "", phone2: "", address: "", primary: false },
            documents: { doc_type: "fayda", name: "", issue_date: "", expiry_date: "", note: "" },
            assets: { name: "", category: "laptop", serial: "", issued_on: this.ctx.today, returned_on: "", value: 0, note: "" },
        }[k];
        this.state.rows.items.push(blank);
    }
    removeRow(i) {
        this.state.rows.items.splice(i, 1);
    }
    onFile(row, ev) {
        const file = ev.target.files[0];
        if (!file) {
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            row.file_data = String(reader.result).split(",")[1];
            row.file_name = file.name;
        };
        reader.readAsDataURL(file);
    }
    async saveRows() {
        const r = this.state.rows;
        this.state.busy = true;
        try {
            this.state.prof = await this.orm.call("sb.people", "save_rows", [this.prof.card.id, r.kind, r.items]);
            this.state.rows = null;
            this.notification.add("Saved.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
    docClass(state) {
        return { expired: "o_pp_bad", expiring: "o_pp_warn", valid: "o_pp_ok", none: "" }[state] || "";
    }

    // ------------------------------------------------------------------ hire
    async saveHire() {
        const h = this.state.hire;
        this.state.busy = true;
        try {
            const id = await this.orm.call("sb.people", "hire", [h]);
            this.state.ctx = await this.orm.call("sb.people", "ctx", []);
            this.notification.add(`${h.name} hired. Probation (60 working days) set.`, { type: "success" });
            await this.openProfile(id);
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("sonador_people.app", PeopleApp);
