import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

// the daily record in the farm's own language (a farm hand records in the language they read best)
const WORDS = {
    en: { title: "Today's record", alive: "Birds alive", dead: "Died", culled: "Culled", out: "Sold / moved out", feed: "Feed given (kg)",
        water: "Water (litres)", eggs: "Eggs collected", broken: "Broken / cracked", weight: "Average weight (g)", manure: "Manure (kg)",
        note: "Note", save: "Save", yesterday: "Before", saved: "Saved: the feed is issued and the eggs are in the store.", stock: "in the store" },
    am: { title: "የዛሬ መዝገብ", alive: "በሕይወት ያሉ ዶሮዎች", dead: "የሞቱ", culled: "የተወገዱ", out: "የተሸጡ / የወጡ", feed: "የተሰጠ መኖ (ኪ.ግ)",
        water: "ውሃ (ሊትር)", eggs: "የተሰበሰበ እንቁላል", broken: "የተሰበሩ / የተሰነጠቁ", weight: "አማካይ ክብደት (ግራም)", manure: "ፍግ (ኪ.ግ)",
        note: "ማስታወሻ", save: "አስቀምጥ", yesterday: "ቀደም", saved: "ተቀምጧል፦ መኖው ወጪ ሆኗል፣ እንቁላሉ መጋዘን ገብቷል።", stock: "በመጋዘን" },
    om: { title: "Galmee har'aa", alive: "Lukkuuwwan lubbuun jiran", dead: "Kan du'an", culled: "Kan baafaman", out: "Kan gurguraman / ba'an",
        feed: "Nyaata kenname (kg)", water: "Bishaan (liitira)", eggs: "Hanqaaquu walitti qabame", broken: "Kan cabe / tarsaa'e",
        weight: "Ulfaatina giddugaleessaa (giraamii)", manure: "Dikee (kg)", note: "Yaadannoo", save: "Olkaa'i", yesterday: "Kanaan dura",
        saved: "Olkaa'ameera: nyaanni ba'eera, hanqaaquun kuusaa seeneera.", stock: "kuusaa keessa" },
};

const fmt = (v, d = 0) => (v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));

/** Farm: farms and their flocks with alerts, the daily record, a flock's figures and money, setup. */
export class FarmApp extends Component {
    static template = "sonador_farm.App";
    static props = ["*"];
    static components = { SbChart };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        let lang = "en";
        try {
            lang = window.localStorage.getItem("sb_farm_lang") || "en";
        } catch {
            // private window: English
        }
        this.state = useState({ ctx: null, page: p.page || "home", busy: false, home: null, flock: null, rec: null, recVals: null,
            set: null, edit: null, lang, tab: "days", health: null, tpl: null, confirmClose: false,
            start: { name: "", location: "", houses: 1, capacity: "" } });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.farm.service", "ctx", []);
            // a farm hand with one flock lands on today's record: nothing to find
            if (!this.state.ctx.is_manager && this.state.ctx.my_flocks.length === 1 && this.state.page === "home") {
                await this.record(this.state.ctx.my_flocks[0]);
                return;
            }
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    get W() {
        return WORDS[this.state.lang] || WORDS.en;
    }
    fmt(v, d) {
        return fmt(v, d);
    }
    async busy(fn) {
        this.state.busy = true;
        try {
            return await fn();
        } finally {
            this.state.busy = false;
        }
    }
    async load() {
        await this.busy(async () => {
            if (this.state.page === "home") {
                this.state.home = await this.orm.call("sb.farm.service", "home", []);
            } else if (this.state.page === "setup") {
                this.state.set = await this.orm.call("sb.farm.service", "setup", []);
            }
        });
    }
    async go(page) {
        this.state.page = page;
        this.state.edit = null;
        await this.load();
    }
    setLang(l) {
        this.state.lang = l;
        try {
            window.localStorage.setItem("sb_farm_lang", l);
        } catch {
            // not kept: fine
        }
    }

    // ------------------------------------------------------------------ flock
    async openFlock(id) {
        await this.busy(async () => {
            this.state.flock = await this.orm.call("sb.farm.service", "flock", [id]);
        });
        this.state.page = "flock";
        this.state.tab = "days";
    }
    kindLabel(k) {
        return { layer: "Layers", broiler: "Broilers", dual: "Dual purpose" }[k] || k;
    }
    get layChart() {
        const s = this.state.flock.series;
        if (this.state.flock.kind === "layer") {
            return { labels: s.map((x) => x.label), series: [{ label: "Laying %", data: s.map((x) => x.hen_day) }, { label: "Breed target", data: s.map((x) => x.target_hen_day) }] };
        }
        const w = s.filter((x) => x.weight);
        return { labels: w.map((x) => x.label), series: [{ label: "Weight (g)", data: w.map((x) => x.weight) }, { label: "Breed target", data: w.map((x) => x.target_weight) }] };
    }
    get deadChart() {
        const s = this.state.flock.series;
        return { labels: s.map((x) => x.label), series: [{ label: "Birds lost", data: s.map((x) => x.dead) }] };
    }
    async healthDone(h) {
        const qty = h.dose || "";
        this.state.flock = await this.orm.call("sb.farm.service", "health_done", [h.id, qty]);
        this.notification.add(h.product ? `${h.name}: given, ${h.product} issued from the store.` : `${h.name}: given.`, { type: "success" });
    }
    async healthSkip(h) {
        this.state.flock = await this.orm.call("sb.farm.service", "health_skip", [h.id, ""]);
    }
    newHealth() {
        this.state.health = { name: "", date: this.ctx.today, method: "", withdrawal: 0 };
    }
    async saveHealth() {
        this.state.flock = await this.orm.call("sb.farm.service", "health_add", [this.state.flock.id, this.state.health]);
        this.state.health = null;
    }
    async sell(credit) {
        const act = await this.orm.call("sb.farm.service", "sell", [this.state.flock.id, credit]);
        await this.action.doAction(act);
    }
    async askFeed() {
        const k = this.state.flock.k;
        const weekly = Math.max(1, Math.round((k.feed_g_bird || 110) * k.alive * 7 / 1000));
        const act = await this.orm.call("sb.farm.service", "request_feed", [this.state.flock.id, weekly]);
        await this.action.doAction(act);
    }
    async depreciate() {
        this.state.flock = await this.orm.call("sb.farm.service", "depreciate", [this.state.flock.id]);
        this.notification.add("The birds' cost worn off to yesterday is posted.", { type: "success" });
    }
    async closeFlock() {
        if (!this.state.confirmClose) {
            this.state.confirmClose = true;
            return;
        }
        this.state.confirmClose = false;
        this.state.flock = await this.orm.call("sb.farm.service", "flock_close", [this.state.flock.id, this.ctx.today]);
        this.notification.add("Flock closed.", { type: "success" });
    }
    openDoc(model, id) {
        if (model && id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]], target: "new" });
        }
    }
    openJob(id) {
        if (id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.job", res_id: id, views: [[false, "form"]] });
        }
    }

    // ------------------------------------------------------------------ the daily record
    async record(flockId, day) {
        await this.busy(async () => {
            const r = await this.orm.call("sb.farm.service", "record_form", [flockId, day || this.ctx.today]);
            this.state.rec = r;
            const v = r.values || {};
            this.state.recVals = { dead: v.dead || "", culled: v.culled || "", out: v.out || "", feed: v.feed || "", water: v.water || "",
                weight: v.weight || "", broken: v.broken || "", manure: v.manure || "", note: v.note || "", eggs: { ...(v.eggs || {}) } };
        });
        this.state.page = "record";
    }
    before(key) {
        const b = this.state.rec && this.state.rec.before;
        return b ? b[key] : "";
    }
    beforeEgg(id) {
        const b = this.state.rec && this.state.rec.before;
        return b && b.eggs ? b.eggs[String(id)] || 0 : "";
    }
    get eggTotal() {
        const e = (this.state.recVals && this.state.recVals.eggs) || {};
        return Object.values(e).reduce((a, x) => a + (Number(x) || 0), 0);
    }
    async saveRecord() {
        const r = this.state.rec;
        const res = await this.orm.call("sb.farm.service", "save_record", [r.flock.id, r.date, this.state.recVals]);
        this.state.rec = res.record;
        this.notification.add(res.record.store_skipped ? "Recorded." : this.W.saved, { type: "success" });
    }
    async recordDay(delta) {
        const d = new Date(this.state.rec.date);
        d.setDate(d.getDate() + delta);
        const iso = d.toISOString().slice(0, 10);
        if (iso > this.ctx.today) {
            return;
        }
        await this.record(this.state.rec.flock.id, iso);
    }

    // ------------------------------------------------------------------ first farm, new flock
    async quickStart() {
        this.state.ctx = await this.orm.call("sb.farm.service", "quick_start", [this.state.start]);
        this.newFlock();
    }
    newFlock(farm) {
        const f = farm || (this.ctx.farms[0] || {});
        const unit = (f.units || [])[0] || {};
        this.state.edit = { id: false, name: this.flockName("layer", unit), farm_id: f.id || "", unit_id: unit.id || "", kind: "layer", breed_id: "", date_placed: this.ctx.today,
            age_placed: 112, birds_placed: unit.capacity || "", feed_id: this.ctx.last_feed || "", lay_until: "", note: "" };
        this.state.page = "newflock";
    }
    flockName(kind, unit) {
        // a name to start from: what, where, when (Layers · House 1 · Tikimt 2019)
        const month = (this.ctx.today_ec || "").replace(/ \d+,/, "");
        return [kind === "layer" ? "Layers" : "Broilers", unit && unit.name, month].filter(Boolean).join(" · ");
    }
    setKind(kind) {
        // pullets usually come at 16 weeks, broiler chicks at a day old
        const auto = this.state.edit.name === this.flockName(this.state.edit.kind, this.unitsOfFarm.find((u) => u.id === Number(this.state.edit.unit_id)));
        if (auto || !this.state.edit.name) {
            this.state.edit.name = this.flockName(kind, this.unitsOfFarm.find((u) => u.id === Number(this.state.edit.unit_id)));
        }
        this.state.edit.kind = kind;
        this.state.edit.age_placed = kind === "layer" ? 112 : 1;
        this.state.edit.breed_id = "";
    }
    get breedsOfKind() {
        const k = this.state.edit.kind;
        return this.ctx.breeds.filter((b) => b.kind === k || (k === "layer" && b.kind === "dual"));
    }
    editFlock() {
        const f = this.state.flock;
        this.state.edit = { id: f.id, name: f.name, farm_id: f.farm_id, unit_id: f.unit_id || "", kind: f.kind, breed_id: f.breed_id || "",
            date_placed: f.placed, age_placed: f.age_placed, birds_placed: f.k.placed, feed_id: f.feed_id || "", lay_until: f.lay_until || "", note: f.note };
        this.state.page = "newflock";
    }
    get unitsOfFarm() {
        const f = this.ctx.farms.find((x) => x.id === Number(this.state.edit.farm_id));
        return f ? f.units : [];
    }
    async saveFlock() {
        const id = await this.orm.call("sb.farm.service", "flock_save", [this.state.edit.id, this.state.edit]);
        this.state.ctx = await this.orm.call("sb.farm.service", "ctx", []);
        await this.openFlock(id);
    }

    // ------------------------------------------------------------------ setup
    async makeDefaults() {
        this.state.set = await this.orm.call("sb.farm.service", "make_defaults", []);
        this.state.ctx = await this.orm.call("sb.farm.service", "ctx", []);
        this.notification.add("The farm accounts and items are ready.", { type: "success" });
    }
    editFarm(f) {
        this.state.edit = f ? { ...f, units: f.units.map((u) => ({ ...u })), worker_ids: [...f.worker_ids], make_setup_job: false }
            : { id: false, name: "", location: "", manager_id: "", warehouse_id: (this.state.set.warehouses[0] || {}).id || "", worker_ids: [],
                units: [{ name: "House 1", kind: "house", capacity: "" }], make_setup_job: true };
    }
    addUnit() {
        this.state.edit.units.push({ name: `House ${this.state.edit.units.length + 1}`, kind: "house", capacity: "" });
    }
    toggleWorker(id) {
        const w = this.state.edit.worker_ids;
        const i = w.indexOf(id);
        if (i >= 0) {
            w.splice(i, 1);
        } else {
            w.push(id);
        }
    }
    async saveFarm() {
        this.state.set = await this.orm.call("sb.farm.service", "farm_save", [this.state.edit.id, this.state.edit]);
        this.state.ctx = await this.orm.call("sb.farm.service", "ctx", []);
        this.state.edit = null;
    }
    editTemplate(t) {
        this.state.tpl = t ? { ...t } : { id: false, kind: "layer", name: "", age: 1, repeat: 0, method: "", withdrawal: 0 };
    }
    async saveTemplate() {
        this.state.set = await this.orm.call("sb.farm.service", "template_save", [this.state.tpl.id, this.state.tpl]);
        this.state.tpl = null;
    }
    async deleteTemplate(t) {
        this.state.set = await this.orm.call("sb.farm.service", "template_delete", [t.id]);
    }
}

registry.category("actions").add("sonador_farm.app", FarmApp);
