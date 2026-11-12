import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const KIND_ICON = { note: "fa-sticky-note-o", call: "fa-phone", meeting: "fa-handshake-o", visit: "fa-map-marker", telegram: "fa-paper-plane",
    sms: "fa-commenting-o", email: "fa-envelope-o", task: "fa-check-square-o" };
const LIFE_ORDER = ["prospect", "contacted", "qualified", "customer", "repeat", "inactive"];

/** CRM: dashboard, My day, contacts and companies, contact record with timeline, deals board, reorders, settings. */
export class CrmApp extends Component {
    static template = "sonador_crm.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "home", busy: false, back: null,
            dash: null, dashMonth: "", dashOwner: "",
            tasks: null, taskWho: "me",
            list: null, f: { kind: "all", search: "", lifecycle: "", ctype: "", region: "", owner: "", tag: "", order: "name", offset: 0, quiet: "" },
            sel: {}, bulk: { owner_id: "", lifecycle: "", tag: "" },
            rec: null, recId: p.partner_id || null, edit: null, woredaHits: [], newTag: "",
            log: this.blankLog("note"),
            board: null, pipelineId: null, bf: { owner: "", search: "", region: "" }, drag: null,
            deal: null, dealEdit: null, lostAsk: null, partnerHits: [],
            reorders: null, rf: { owner: "", region: "" }, rsel: {},
            set: { tab: "pipelines", pipe: null, targets: null, month: "", team: null, teamFilter: "" },
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.crm", "ctx", []);
            this.state.pipelineId = (this.ctx.pipelines[0] || {}).id;
            if (p.page === "record" && !this.state.recId) {
                this.state.page = "contacts";
            }
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    blankLog(kind) {
        return { kind, subject: "", body: "", outcome: "", minutes: "", due: "", user_id: "", followup_days: "", deal_id: "" };
    }
    money(v) {
        return Number(v || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }
    short(v) {
        const n = Number(v || 0);
        if (Math.abs(n) >= 1e6) {
            return (n / 1e6).toFixed(1) + "M";
        }
        if (Math.abs(n) >= 1e3) {
            return (n / 1e3).toFixed(0) + "K";
        }
        return n.toFixed(0);
    }
    label(list, id) {
        return ((this.ctx[list] || []).find((x) => x.id === id) || {}).name || "";
    }
    kindIcon(k) {
        return KIND_ICON[k] || "fa-circle";
    }
    lifeIndex(l) {
        return LIFE_ORDER.indexOf(l);
    }
    tel(phone) {
        return "tel:" + (phone || "").replace(/\s/g, "");
    }
    tg(rec) {
        const t = (rec.fields && rec.fields.telegram) || "";
        if (t && !/^\+?\d+$/.test(t)) {
            return "https://t.me/" + t.replace(/^@/, "");
        }
        const ph = (t || (rec.fields && rec.fields.phone) || "").replace(/\D/g, "");
        return ph ? "https://t.me/+" + ph : "";
    }
    async go(page, extra) {
        Object.assign(this.state, { page, deal: null, dealEdit: null }, extra || {});
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "home") {
                s.dash = await this.orm.call("sb.crm", "dashboard", [s.dashMonth || false, s.dashOwner || false]);
            } else if (s.page === "tasks") {
                s.tasks = await this.orm.call("sb.crm", "tasks", [s.taskWho]);
            } else if (s.page === "contacts") {
                s.list = await this.orm.call("sb.crm", "contacts", [s.f]);
            } else if (s.page === "record") {
                s.rec = await this.orm.call("sb.crm", "contact", [s.recId]);
                s.edit = null;
                s.log = this.blankLog(s.log.kind);
            } else if (s.page === "deals") {
                if (s.pipelineId) {
                    s.board = await this.orm.call("sb.crm", "board", [s.pipelineId, s.bf]);
                }
            } else if (s.page === "reorders") {
                s.reorders = await this.orm.call("sb.crm", "reorders", [s.rf]);
                s.rsel = {};
            } else if (s.page === "settings") {
                s.set.targets = await this.orm.call("sb.crm", "targets", [s.set.month || this.ctx.today]);
            }
        } finally {
            s.busy = false;
        }
    }

    noop() {}
    coord(v) {
        return Number(v || 0).toFixed(4);
    }
    get anyR() {
        return Object.values(this.state.rsel).some((x) => x);
    }
    cancelDeal() {
        if (this.state.deal.id) {
            this.state.dealEdit = null;
        } else {
            this.closeDeal();
        }
    }

    // ------------------------------------------------------------------ dashboard
    async shiftDash(n) {
        const base = this.state.dash ? this.state.dash.month : this.ctx.today;
        const d = new Date(base + "T00:00:00");
        d.setDate(d.getDate() + n * 30 + (n > 0 ? 2 : -2));
        this.state.dashMonth = d.toISOString().slice(0, 10);
        await this.load();
    }
    async setDashOwner(v) {
        this.state.dashOwner = v;
        await this.load();
    }
    get stageChart() {
        const st = this.state.dash.stages;
        return { labels: st.map((x) => x.name), series: [{ label: `Open deals (${this.state.ctx.currency})`, data: st.map((x) => x.amount) }] };
    }
    get forecastChart() {
        const f = this.state.dash.forecast;
        return { labels: f.map((x) => x.month), series: [{ label: `Weighted forecast (${this.state.ctx.currency})`, data: f.map((x) => x.amount) }] };
    }
    get regionChart() {
        const r = this.state.dash.regions;
        return { labels: r.map((x) => x.name), series: [{ label: `Sales (${this.state.ctx.currency})`, data: r.map((x) => x.amount) }] };
    }
    pct(a, b) {
        return b ? Math.min(100, Math.round((100 * a) / b)) : 0;
    }

    // ------------------------------------------------------------------ tasks
    async setTaskWho(v) {
        this.state.taskWho = v;
        await this.load();
    }
    async finish(t, outcome) {
        await this.orm.call("sb.crm", "finish", [t.id, outcome || false, ""]);
        this.notification.add("Done.", { type: "success" });
        await this.load();
    }
    async snooze(t, days) {
        await this.orm.call("sb.crm", "reschedule", [t.id, days]);
        await this.load();
    }

    // ------------------------------------------------------------------ contacts
    async setF(k, v) {
        this.state.f[k] = v;
        this.state.f.offset = 0;
        await this.load();
    }
    async page(n) {
        this.state.f.offset = Math.max(0, this.state.f.offset + n * this.state.list.page);
        await this.load();
    }
    async openRecord(id) {
        this.state.back = this.state.page;
        this.state.recId = id;
        this.state.page = "record";
        this.state.deal = null;
        await this.load();
    }
    async goBack() {
        await this.go(this.state.back && this.state.back !== "record" ? this.state.back : "contacts");
    }
    toggleSel(id) {
        this.state.sel[id] = !this.state.sel[id];
    }
    get selIds() {
        return Object.keys(this.state.sel).filter((k) => this.state.sel[k]).map(Number);
    }
    async applyBulk() {
        const b = this.state.bulk;
        const n = await this.orm.call("sb.crm", "bulk", [this.selIds, { owner_id: b.owner_id === "" ? null : b.owner_id, lifecycle: b.lifecycle, tag: b.tag }]);
        this.notification.add(`${n} contacts updated.`, { type: "success" });
        this.state.sel = {};
        this.state.bulk = { owner_id: "", lifecycle: "", tag: "" };
        await this.load();
    }
    newContact(company) {
        this.state.back = this.state.page;
        this.state.page = "record";
        this.state.rec = { id: false, name: company ? "New company" : "New contact", company, fields: { is_company: company, name: "", phone: "",
            email: "", vat: "", lifecycle: "prospect", owner_id: this.ctx.me, ctype: "", channel: "", source: "", language: "",
            woreda_id: "", woreda: "", region: "", zone: "", kebele: "", telegram: "", hectares: 0, crops: "", tags: [], function: "",
            street: "", city: "", parent_id: "", latitude: 0, longitude: 0 }, people: [], deals: [], timeline: [], sales: null, orders: [] };
        this.state.edit = { ...this.state.rec.fields };
    }
    startEdit() {
        this.state.edit = { ...this.state.rec.fields, tags: [...this.state.rec.fields.tags] };
    }
    setE(k, v) {
        this.state.edit[k] = v;
    }
    async findWoreda(v) {
        this.state.edit.woreda = v;
        this.state.woredaHits = v.length > 1 ? await this.orm.call("sb.crm", "woredas", [v]) : [];
    }
    pickWoreda(w) {
        Object.assign(this.state.edit, { woreda_id: w.id, woreda: w.label, region: w.region, zone: w.zone });
        this.state.woredaHits = [];
    }
    toggleTag(id) {
        const t = this.state.edit.tags;
        const i = t.indexOf(id);
        if (i >= 0) {
            t.splice(i, 1);
        } else {
            t.push(id);
        }
    }
    async addTag() {
        if (!this.state.newTag.trim()) {
            return;
        }
        const t = await this.orm.call("sb.crm", "save_tag", [this.state.newTag]);
        if (!this.ctx.tags.find((x) => x.id === t.id)) {
            this.ctx.tags.push(t);
        }
        if (!this.state.edit.tags.includes(t.id)) {
            this.state.edit.tags.push(t.id);
        }
        this.state.newTag = "";
    }
    async here() {
        const pos = await this.position();
        if (pos) {
            this.state.edit.latitude = pos.lat;
            this.state.edit.longitude = pos.lon;
        }
    }
    async saveContact() {
        const r = this.state.rec;
        this.state.rec = await this.orm.call("sb.crm", "save_contact", [r.id || false, this.state.edit]);
        this.state.recId = this.state.rec.id;
        this.state.edit = null;
        this.notification.add("Saved.", { type: "success" });
    }
    cancelEdit() {
        if (!this.state.rec.id) {
            this.goBack();
        } else {
            this.state.edit = null;
        }
    }

    // ------------------------------------------------------------------ logging
    setLogKind(k) {
        this.state.log = this.blankLog(k);
        if (k === "task") {
            const d = new Date();
            d.setDate(d.getDate() + 1);
            this.state.log.due = `${d.toISOString().slice(0, 10)}T09:00`;
        }
    }
    setL(k, v) {
        this.state.log[k] = v;
    }
    position() {
        return new Promise((resolve) => {
            if (!navigator.geolocation) {
                resolve(null);
                return;
            }
            navigator.geolocation.getCurrentPosition((p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }), () => resolve(null),
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
        });
    }
    async submitLog(target) {
        const l = { ...this.state.log };
        if (target === "deal") {
            l.deal_id = this.state.deal.id;
            l.partner_id = this.state.deal.partner_id || false;
        } else {
            l.partner_id = this.state.rec.id;
        }
        if (l.kind === "visit") {
            const pos = await this.position();
            if (pos) {
                l.lat = pos.lat;
                l.lon = pos.lon;
            }
        }
        await this.orm.call("sb.crm", "log", [l]);
        this.notification.add(l.kind === "task" ? "Task added." : "Logged.", { type: "success" });
        this.state.log = this.blankLog(l.kind);
        if (target === "deal") {
            this.state.deal = await this.orm.call("sb.crm", "deal", [this.state.deal.id]);
        } else {
            this.state.rec = await this.orm.call("sb.crm", "contact", [this.state.rec.id]);
        }
    }
    async finishHere(t, target) {
        await this.orm.call("sb.crm", "finish", [t.id, false, ""]);
        if (target === "deal") {
            this.state.deal = await this.orm.call("sb.crm", "deal", [this.state.deal.id]);
        } else {
            this.state.rec = await this.orm.call("sb.crm", "contact", [this.state.rec.id]);
        }
    }
    async removeAct(t, target) {
        await this.orm.call("sb.crm", "remove_activity", [t.id]);
        if (target === "deal") {
            this.state.deal = await this.orm.call("sb.crm", "deal", [this.state.deal.id]);
        } else {
            this.state.rec = await this.orm.call("sb.crm", "contact", [this.state.rec.id]);
        }
    }

    // ------------------------------------------------------------------ deals
    async setPipeline(id) {
        this.state.pipelineId = Number(id);
        await this.load();
    }
    async setBf(k, v) {
        this.state.bf[k] = v;
        await this.load();
    }
    dragStart(d) {
        this.state.drag = d;
    }
    async dropOn(col) {
        const d = this.state.drag;
        this.state.drag = null;
        if (!d || d.stage_id === col.id) {
            return;
        }
        if (col.kind === "lost") {
            this.state.lostAsk = { deal: d, stage_id: col.id, reason: "", note: "" };
            return;
        }
        await this.orm.call("sb.crm", "move_deal", [d.id, col.id]);
        if (col.kind === "won") {
            this.notification.add(`${d.name}: won.`, { type: "success" });
        }
        await this.load();
    }
    async confirmLost() {
        const a = this.state.lostAsk;
        await this.orm.call("sb.crm", "move_deal", [a.deal.id, a.stage_id, a.reason, a.note]);
        this.state.lostAsk = null;
        if (this.state.deal) {
            this.state.deal = await this.orm.call("sb.crm", "deal", [this.state.deal.id]);
        }
        await this.load();
    }
    async openDeal(id) {
        this.state.deal = await this.orm.call("sb.crm", "deal", [id]);
        this.state.dealEdit = null;
        this.state.log = this.blankLog("note");
    }
    newDeal(partner) {
        const pipe = this.ctx.pipelines.find((p) => p.id === this.state.pipelineId) || this.ctx.pipelines[0];
        this.state.deal = { id: false, timeline: [], orders: [], contacts: [] };
        this.state.dealEdit = { name: "", partner_id: partner ? partner.id : "", partner: partner ? partner.name : "", contact_id: "",
            owner_id: this.ctx.me, pipeline_id: pipe.id, stage_id: pipe.stages[0].id, amount: "", quantity: "", close_date: "",
            source: "", note: "", tags: [] };
        this.state.partnerHits = [];
    }
    editDeal() {
        const d = this.state.deal;
        this.state.dealEdit = { name: d.name, partner_id: d.partner_id || "", partner: d.partner, contact_id: d.contact_id || "", owner_id: d.owner_id || "",
            pipeline_id: d.pipeline_id, stage_id: d.stage_id, amount: d.amount, quantity: d.quantity, close_date: d.close_date || "",
            source: d.source, note: d.note, tags: [...d.tags] };
    }
    setD(k, v) {
        this.state.dealEdit[k] = v;
        if (k === "pipeline_id") {
            const p = this.ctx.pipelines.find((x) => x.id === Number(v));
            this.state.dealEdit.stage_id = p ? p.stages[0].id : "";
        }
    }
    async findPartner(v) {
        this.state.dealEdit.partner = v;
        this.state.dealEdit.partner_id = "";
        this.state.partnerHits = v.length > 1 ? (await this.orm.call("sb.crm", "contacts", [{ search: v, crm_only: false }])).rows.slice(0, 8) : [];
    }
    pickPartner(p) {
        Object.assign(this.state.dealEdit, { partner_id: p.id, partner: p.name });
        this.state.partnerHits = [];
    }
    get stagesOfEdit() {
        const p = this.ctx.pipelines.find((x) => x.id === Number(this.state.dealEdit.pipeline_id));
        return p ? p.stages : [];
    }
    async saveDeal() {
        const e = { ...this.state.dealEdit };
        delete e.partner;
        this.state.deal = await this.orm.call("sb.crm", "save_deal", [this.state.deal.id || false, e]);
        this.state.dealEdit = null;
        this.notification.add("Deal saved.", { type: "success" });
        if (this.state.page === "deals") {
            await this.load();
        } else if (this.state.page === "record") {
            this.state.rec = await this.orm.call("sb.crm", "contact", [this.state.rec.id]);
        }
    }
    async closeDeal() {
        this.state.deal = null;
        this.state.dealEdit = null;
        if (this.state.page === "deals") {
            await this.load();
        }
    }
    async moveTo(stageId) {
        const st = this.stagesOf(this.state.deal.pipeline_id).find((s) => s.id === stageId);
        if (st.kind === "lost") {
            this.state.lostAsk = { deal: this.state.deal, stage_id: st.id, reason: "", note: "" };
            return;
        }
        await this.orm.call("sb.crm", "move_deal", [this.state.deal.id, stageId]);
        this.state.deal = await this.orm.call("sb.crm", "deal", [this.state.deal.id]);
    }
    stagesOf(pid) {
        return (this.ctx.pipelines.find((p) => p.id === pid) || { stages: [] }).stages;
    }
    async quote() {
        const act = await this.orm.call("sb.crm", "quote", [this.state.deal.id]);
        await this.action.doAction(act);
    }
    openOrder(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sale.order", res_id: id, views: [[false, "form"]] });
    }

    // ------------------------------------------------------------------ reorders
    async setRf(k, v) {
        this.state.rf[k] = v;
        await this.load();
    }
    toggleR(id) {
        this.state.rsel[id] = !this.state.rsel[id];
    }
    async reorderTasks() {
        const ids = Object.keys(this.state.rsel).filter((k) => this.state.rsel[k]).map(Number);
        const n = await this.orm.call("sb.crm", "reorder_tasks", [ids]);
        this.notification.add(`${n} call tasks created (in each owner's My day).`, { type: "success" });
        await this.load();
    }
    selectAllReorders() {
        const all = this.state.reorders.filter((r) => !r.has_task);
        const on = all.length && all.every((r) => this.state.rsel[r.id]);
        this.state.rsel = {};
        if (!on) {
            for (const r of all) {
                this.state.rsel[r.id] = true;
            }
        }
    }

    // ------------------------------------------------------------------ settings
    editPipe(p) {
        this.state.set.pipe = p ? { id: p.id, name: p.name, stages: p.stages.map((s) => ({ ...s })) } :
            { id: false, name: "", stages: [{ name: "New", probability: 10, kind: "open", rot: 14 }, { name: "Won", probability: 100, kind: "won", rot: 14 },
                { name: "Lost", probability: 0, kind: "lost", rot: 14 }] };
    }
    setStage(s, k, v) {
        s[k] = v;
    }
    addStage() {
        const st = this.state.set.pipe.stages;
        st.splice(Math.max(st.length - 2, 0), 0, { name: "", probability: 50, kind: "open", rot: 14 });
    }
    moveStage(i, n) {
        const st = this.state.set.pipe.stages;
        const j = i + n;
        if (j < 0 || j >= st.length) {
            return;
        }
        [st[i], st[j]] = [st[j], st[i]];
    }
    dropStage(i) {
        this.state.set.pipe.stages.splice(i, 1);
    }
    async savePipe() {
        this.state.ctx = await this.orm.call("sb.crm", "save_pipeline", [this.state.set.pipe]);
        this.state.set.pipe = null;
        this.notification.add("Pipeline saved.", { type: "success" });
    }
    async shiftTargets(n) {
        const d = new Date(this.state.set.targets.month + "T00:00:00");
        d.setDate(d.getDate() + n * 30 + (n > 0 ? 2 : -2));
        this.state.set.month = d.toISOString().slice(0, 10);
        await this.load();
    }
    async openTeam() {
        this.state.set.tab = "team";
        this.state.set.team = await this.orm.call("sb.crm", "team", []);
    }
    get teamRows() {
        const f = (this.state.set.teamFilter || "").toLowerCase();
        const rows = this.state.set.team || [];
        return f ? rows.filter((u) => (u.name + " " + u.login + " " + u.department).toLowerCase().includes(f)) : rows;
    }
    async setAccess(u, level) {
        this.state.set.team = await this.orm.call("sb.crm", "set_access", [u.id, level]);
        this.notification.add(`${u.name}: ${level === "none" ? "no CRM access" : level === "user" ? "CRM rep" : "CRM manager"}.`, { type: "success" });
    }
    async saveTargets() {
        this.state.set.targets = await this.orm.call("sb.crm", "save_targets", [this.state.set.targets.month, this.state.set.targets.rows]);
        this.notification.add("Targets saved.", { type: "success" });
    }
}

registry.category("actions").add("sonador_crm.app", CrmApp);
