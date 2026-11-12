/** @odoo-module **/
import { Component, markup, onWillStart, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { NameAndSignature } from "@web/core/signature/name_and_signature";
import { confirmationPayload, signatureOf, signatureReady } from "./confirm";

/** Cards other apps add to Me: registry.category("sonador_me.sections").add(key, Component), props { data, reload }. */
const sections = registry.category("sonador_me.sections");

/** Me: my day, my leave requests, attendance, pay, goals, and one inbox for everything waiting for my decision. */
export class MeApp extends Component {
    static template = "sonador_me.App";
    static props = ["*"];
    static components = { NameAndSignature };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ d: null, busy: false, refusing: null, note: "", leave: null, preview: null, reading: null,
            tab: "waiting", app: null, center: null, confirm: null, page: null });
        this.markup = markup;
        onWillStart(() => this.load());
    }
    async load() {
        this.state.d = await this.orm.call("sb.me", "data", []);
    }
    get greeting() {
        const h = new Date().getHours();
        const first = (this.state.d.employee ? this.state.d.employee.name : this.state.d.user.name).split(" ")[0];
        return (h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening") + ", " + first;
    }
    money(v) {
        return (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    pct(a, b) {
        return b ? Math.min(100, Math.round((a / b) * 100)) : 0;
    }
    stateLabel(s) {
        return { draft: "Draft", submitted: "Waiting", approved: "Approved", refused: "Refused", cancelled: "Cancelled" }[s] || s;
    }

    // pages
    get page() {
        return this.state.page || (this.state.d.approvals.length ? "approvals" : "home");
    }
    get pageSections() {
        return (this.state.d.sections || []).filter((s) => s.wide && this.sectionComponent(s.key));
    }
    setPage(page) {
        this.state.page = page;
    }

    // approvals
    setTab(tab) {
        this.state.tab = tab;
        if (tab !== "waiting") {
            this.loadCenter();
        }
    }
    async loadCenter() {
        this.state.center = await this.orm.call("sb.me", "approval_center", []);
        this.state.d.approvals = this.state.center.approvals;
    }
    appsOf(items) {
        const n = {};
        for (const i of items) {
            n[i.app] = (n[i.app] || 0) + 1;
        }
        return Object.entries(n).map(([app, k]) => ({ app, n: k }));
    }
    filtered(items) {
        return this.state.app ? items.filter((i) => i.app === this.state.app) : items;
    }
    when(dt) {
        if (!dt) {
            return "";
        }
        const d = new Date(dt.replace(" ", "T") + "Z");
        return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    }
    async decide(item, approve) {
        if (!approve && this.state.refusing !== item.key) {
            this.state.refusing = item.key;
            this.state.note = "";
            return;
        }
        this.state.busy = true;
        try {
            const r = await this.orm.call("sb.me", "decide", [item.key, approve, approve ? "" : this.state.note]);
            this.state.d.approvals = r.approvals;
            this.state.refusing = null;
            this.state.confirm = null;
            this.state.center = null;
            this.notification.add(approve ? (r.steps > 1 ? `Approved: all ${r.steps} steps were yours.` : "Approved.") : "Sent back.", { type: "success" });
        } catch (e) {
            const payload = confirmationPayload(e);
            if (!payload) {
                throw e;
            }
            // the next step of the same document is mine too: the signature just given covers it, no second prompt
            const s = this._signed;
            if (s && s.key === item.key && (s.tries || 0) < 4 && !payload.needs_password && !payload.needs_phrase) {
                s.tries = (s.tries || 0) + 1;
                await this.orm.call("sb.confirmation", "accept", [payload.token, true, "", "", signatureOf(s.sig, s.reuse), s.reuse]);
                this.state.busy = false;
                return this.decide(item, approve);
            }
            this.state.confirm = { key: item.key, item, approve, payload, ticked: false, phrase: "", password: "",
                reuse: !!payload.reuse_signature, sig: { name: "", isSignatureEmpty: true } };
        } finally {
            this.state.busy = false;
        }
    }
    get confirmReady() {
        const c = this.state.confirm;
        if (!c || !c.ticked || !signatureReady(c.sig, c.reuse)) {
            return false;
        }
        if (c.payload.needs_phrase && c.phrase.trim().toUpperCase() !== (c.payload.phrase || "").toUpperCase()) {
            return false;
        }
        return !(c.payload.needs_password && !c.password);
    }
    async acceptConfirm() {
        const c = this.state.confirm;
        this.state.busy = true;
        try {
            await this.orm.call("sb.confirmation", "accept", [c.payload.token, true, c.phrase, c.password, signatureOf(c.sig, c.reuse), c.reuse]);
        } finally {
            this.state.busy = false;
            if (this.state.confirm) {
                this.state.confirm.password = "";
            }
        }
        // the confirmation is recorded: do what was asked; a next step of mine on the same document reuses this signature
        this._signed = { key: c.item.key, sig: c.sig, reuse: c.reuse, tries: 0 };
        this.state.confirm = null;
        try {
            await this.decide(c.item, c.approve);
        } finally {
            this._signed = null;
        }
    }
    async open(key) {
        const act = await this.orm.call("sb.me", "open_doc", [key]);
        if (act) {
            this.action.doAction(act);
        }
    }
    get unread() {
        return (this.state.d.notices || []).filter((n) => !n.read).length;
    }
    async readAll() {
        this.state.d.notices = await this.orm.call("sb.me.notice", "mark_read", []);
    }
    async openNotice(n) {
        if (!n.read) {
            this.state.d.notices = await this.orm.call("sb.me.notice", "mark_read", [[n.id]]);
        }
        if (n.key) {
            return this.open(n.key);
        }
        if (n.model && n.res_id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: n.model, res_id: n.res_id, views: [[false, "form"]] });
        }
    }
    sectionComponent(key) {
        return sections.contains(key) ? sections.get(key) : null;
    }

    // to do
    async done(t) {
        this.state.d = await this.orm.call("sb.me", "todo_done", [t.key]);
    }
    openTodo(t) {
        if (t.body) {
            this.state.reading = this.state.reading === t.key ? null : t.key;
            return;
        }
        if (t.record) {
            return this.action.doAction({ type: "ir.actions.act_window", res_model: t.record.model, res_id: t.record.id, views: [[false, "form"]] });
        }
        if (t.open) {
            this.action.doAction({ type: "ir.actions.client", tag: t.open });
        }
    }

    // leave
    newLeave() {
        const types = this.state.d.timeoff.types;
        this.state.leave = { type_id: types.length ? types[0].id : false, date_from: this.state.d.today, date_to: this.state.d.today, reason: "" };
        this.previewLeave();
    }
    setLeave(k, v) {
        this.state.leave[k] = k === "type_id" ? parseInt(v) : v;
        this.previewLeave();
    }
    async previewLeave() {
        clearTimeout(this._t);
        this._t = setTimeout(async () => {
            this.state.preview = await this.orm.call("sb.me", "leave_preview", [this.state.leave]);
        }, 250);
    }
    async sendLeave() {
        this.state.busy = true;
        try {
            this.state.d = await this.orm.call("sb.me", "leave_request", [this.state.leave]);
            this.state.leave = null;
            this.state.preview = null;
            this.notification.add("Sent to your manager.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
    async cancelLeave(l) {
        this.state.d = await this.orm.call("sb.me", "leave_cancel", [l.id]);
    }

    // attendance
    async clock() {
        let lat = 0, lon = 0, acc = 0;
        try {
            const pos = await new Promise((res, rej) => navigator.geolocation.getCurrentPosition(res, rej, { timeout: 8000 }));
            lat = pos.coords.latitude;
            lon = pos.coords.longitude;
            acc = pos.coords.accuracy;
        } catch {
            /* no location: the company's rules decide */
        }
        this.state.d = await this.orm.call("sb.me", "clock", [lat, lon, acc]);
        this.notification.add(this.state.d.clock.is_in ? "Clocked in." : "Clocked out.", { type: "success" });
    }
    hours(h) {
        const m = Math.round((h || 0) * 60);
        return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}`;
    }

    openApp(xmlid) {
        this.action.doAction(xmlid).catch(() => {});
    }
}
registry.category("actions").add("sonador_me.app", MeApp);

/** The number of things waiting for my decision, in the top bar. */
export class MeSystray extends Component {
    static props = {};
    static template = xml`
        <div class="o_me_tray d-flex align-items-center">
            <button class="btn btn-link px-2 position-relative" title="Waiting for your approval" t-on-click="open">
                <i class="fa fa-inbox"/>
                <span t-if="state.n" class="badge rounded-pill text-bg-danger o_me_badge" t-esc="state.n"/>
            </button>
        </div>`;
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ n: 0 });
        onWillStart(() => this.refresh());
        this._i = setInterval(() => this.refresh(), 120000);
    }
    async refresh() {
        try {
            this.state.n = await this.orm.call("sb.me", "count", []);
        } catch {
            this.state.n = 0;
        }
    }
    open() {
        this.action.doAction("sonador_me.action_me");
    }
}
registry.category("systray").add("sonador_me.tray", { Component: MeSystray }, { sequence: 30 });
