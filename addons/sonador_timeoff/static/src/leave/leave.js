import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const num = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));

/** Leave Requests: my leave and balances, request, approvals, team calendar, HR balances and leave liability, holidays. */
export class TimeOffApp extends Component {
    static template = "sonador_timeoff.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const p = (this.props.action && this.props.action.params) || {};
        const today = new Date();
        this.state = useState({
            ctx: null, page: p.page || "mine", busy: false,
            who: p.employee_id || null, ov: null,
            form: null, preview: null,
            approvals: [], refuse: {},
            cal: { month: new Date(today.getFullYear(), today.getMonth(), 1), data: null, scope: "team" },
            bal: null, adj: { days: "", date: "", note: "" },
            hol: { year: today.getFullYear(), list: [], move: {} },
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.leave.service", "ctx", []);
            this.state.who = this.state.who || this.state.ctx.me || (this.state.ctx.people[0] && this.state.ctx.people[0].id) || null;
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    num(v, d) {
        return num(v, d);
    }
    stateLabel(s) {
        return { draft: "Draft", submitted: "Waiting for the manager", manager: "Manager approved, waiting for HR", approved: "Approved", refused: "Refused", cancelled: "Cancelled" }[s] || s;
    }
    iso(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    async go(page) {
        this.state.page = page;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "mine") {
                s.ov = await this.orm.call("sb.leave.service", "overview", [s.who || false]);
            } else if (s.page === "approvals") {
                s.approvals = await this.orm.call("sb.leave.service", "approvals", []);
            } else if (s.page === "calendar") {
                s.cal.data = await this.orm.call("sb.leave.service", "calendar", [this.iso(s.cal.month), s.cal.scope]);
            } else if (s.page === "balances") {
                s.bal = await this.orm.call("sb.leave.service", "balances", []);
            } else if (s.page === "holidays") {
                s.hol.list = await this.orm.call("sb.leave.service", "holidays", [s.hol.year]);
            }
        } finally {
            s.busy = false;
        }
    }
    async pickWho() {
        await this.load();
    }
    async openPerson(id) {
        this.state.who = id;
        await this.go("mine");
    }

    // ------------------------------------------------------------------ request
    get myTypes() {
        return this.ctx.types.filter((t) => t.mine || this.ctx.is_hr);
    }
    newRequest() {
        const t = this.myTypes[0];
        this.state.form = { employee_id: this.state.who, type_id: t ? t.id : "", date_from: this.ctx.today, date_to: this.ctx.today, reason: "",
            file_data: false, file_name: "" };
        this.state.preview = null;
        this.refresh();
    }
    get formType() {
        const f = this.state.form;
        return f && this.ctx.types.find((t) => String(t.id) === String(f.type_id));
    }
    async refresh() {
        const f = this.state.form;
        if (!f || !f.type_id || !f.date_from || !f.date_to) {
            return;
        }
        this.state.preview = await this.orm.call("sb.leave.service", "preview", [f]);
    }
    onFile(ev) {
        const file = ev.target.files[0];
        if (!file) {
            return;
        }
        const r = new FileReader();
        r.onload = () => {
            this.state.form.file_data = String(r.result).split(",")[1];
            this.state.form.file_name = file.name;
        };
        r.readAsDataURL(file);
    }
    async submit(approve = false) {
        await this.orm.call("sb.leave.service", "request", [{ ...this.state.form, approve }]);
        this.state.form = null;
        this.notification.add(approve ? "Leave recorded and approved." : "Request sent for approval.", { type: "success" });
        await this.load();
    }
    async cancel(l) {
        await this.orm.call("sb.leave.service", "cancel", [l.id]);
        await this.load();
    }

    // ------------------------------------------------------------------ approvals
    async decide(l, approve) {
        await this.orm.call("sb.leave.service", "decide", [l.id, approve, this.state.refuse[l.id] || ""]);
        this.notification.add(approve ? "Approved." : "Refused.", { type: approve ? "success" : "warning" });
        await this.load();
    }
    setRefuse(id, v) {
        this.state.refuse[id] = v;
    }

    // ------------------------------------------------------------------ calendar
    async moveMonth(n) {
        const m = this.state.cal.month;
        this.state.cal.month = new Date(m.getFullYear(), m.getMonth() + n, 1);
        await this.load();
    }
    async setScope(s) {
        this.state.cal.scope = s;
        await this.load();
    }
    get monthLabel() {
        return this.state.cal.month.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
    }

    // ------------------------------------------------------------------ HR
    async saveAdjust() {
        const a = this.state.adj;
        this.state.ov = await this.orm.call("sb.leave.service", "adjust", [this.state.who, a.days, a.date || this.ctx.today, a.note]);
        this.state.adj = { days: "", date: "", note: "" };
        this.notification.add("Annual leave adjusted.", { type: "success" });
    }
    async moveYear(n) {
        this.state.hol.year += n;
        await this.load();
    }
    setMove(id, v) {
        this.state.hol.move[id] = v;
    }
    async confirmHoliday(h) {
        await this.orm.call("sb.holidays", "move", [h.id, this.state.hol.move[h.id] || h.date]);
        this.notification.add("Holiday date confirmed.", { type: "success" });
        await this.load();
    }
}

registry.category("actions").add("sonador_timeoff.app", TimeOffApp);
