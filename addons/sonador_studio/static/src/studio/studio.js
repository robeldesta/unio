/** @odoo-module **/
import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const DEFAULT_LABEL = {
    text: "Text", longtext: "Notes", richtext: "Description", number: "Number", decimal: "Quantity", money: "Amount",
    date: "Date", datetime: "Date and time", checkbox: "Done", choice: "Kind", stage: "Stage", link: "Customer",
    tags: "Tags", lines: "Lines", file: "File", image: "Photo", formula: "Total",
};
const TYPE_ICON = {
    text: "fa-font", longtext: "fa-align-left", richtext: "fa-paragraph", number: "fa-hashtag", decimal: "fa-calculator",
    money: "fa-money", date: "fa-calendar", datetime: "fa-clock-o", checkbox: "fa-check-square-o", choice: "fa-list-ul",
    stage: "fa-step-forward", link: "fa-link", tags: "fa-tags", lines: "fa-table", file: "fa-paperclip", image: "fa-picture-o",
    formula: "fa-superscript",
};
const NUMERIC = ["number", "decimal", "money", "formula"];
const COLORS = ["#2D6BFF", "#1FD1B5", "#4CD07D", "#FDB52F", "#F8642A", "#E8475B", "#9B4F96", "#24304A"];

function slug(label) {
    const s = (label || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30);
    return /^[0-9]/.test(s) ? "n" + s : s || "field";
}
function clone(o) {
    return JSON.parse(JSON.stringify(o));
}
function optValue(label) {
    return slug(label);
}

export class Studio extends Component {
    static template = "sonador_studio.Studio";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.COLORS = COLORS;
        this.markup = markup;
        this.TYPE_ICON = TYPE_ICON;
        this.state = useState({
            screen: "home", home: null, busy: false,
            // new app
            newApp: null, describe: "", draft: null,
            // editor
            m: null, spec: null, saved: null, dirty: [], view: "form", panel: "add", sel: null, confirmDelete: false,
            auto: null, history: false,
        });
        this.drag = null;
        onWillStart(async () => {
            await this.loadHome();
            const model = this.props.action?.context?.studio_model;
            if (model) {
                await this.open(model);
            }
        });
    }

    // ------------------------------------------------------------------ home
    async loadHome() {
        this.state.home = await this.orm.call("sb.studio.api", "home", []);
    }
    async call(method, args) {
        this.state.busy = true;
        try {
            return await this.orm.call("sb.studio.api", method, args);
        } finally {
            this.state.busy = false;
        }
    }
    startNewApp(template) {
        const t = template && this.state.home.templates.find((x) => x.key === template);
        this.state.newApp = { name: t ? t.name : "", icon: t ? t.icon : "box", color: t ? t.color : COLORS[0], record: "", template: template || null };
        this.state.draft = null;
    }
    async createApp() {
        const n = this.state.newApp;
        const res = await this.call("app_create", [n.name, n.icon, n.color, n.template, n.record || n.name]);
        this.notification.add(`${n.name} is ready.`, { type: "success" });
        this.state.newApp = null;
        await this.loadHome();
        await this.open(res.model);
    }
    async draftApp() {
        this.state.draft = await this.call("draft", [this.state.describe]);
        this.state.newApp = null;
    }
    draftFieldCount(spec) {
        return spec.fields.length;
    }
    typeLabel(t) {
        return (this.state.home?.types || this.state.m?.types || []).find((x) => x.key === t)?.label || t;
    }
    async createDraft() {
        const d = this.state.draft;
        const res = await this.call("app_create", [d.name, d.icon, d.color, null, null, d.description, d.models]);
        this.notification.add(`${d.name} is ready.`, { type: "success" });
        this.state.draft = null;
        this.state.describe = "";
        await this.loadHome();
        await this.open(res.model);
    }
    async deleteApp(app) {
        if (this.state.confirmDelete !== app.id) {
            this.state.confirmDelete = app.id;
            return;
        }
        this.state.home = await this.call("app_delete", [app.id]);
        this.state.confirmDelete = false;
        this.notification.add(`${app.name} was deleted.`, { type: "warning" });
    }
    async openApp(app) {
        if (app.models.length) {
            await this.open(app.models[0].model);
        }
    }

    // ------------------------------------------------------------------ editor
    async open(model) {
        const m = await this.call("load", [model]);
        this.setModel(m);
        this.state.screen = "editor";
    }
    setModel(m) {
        this.state.m = m;
        const spec = clone(m.spec);
        // every part the editor works on is there, even when a spec leaves it out
        spec.fields = spec.fields || [];
        spec.list = spec.list || [];
        if (m.own) {
            spec.form = spec.form || {};
            spec.form.sections = spec.form.sections || [];
            spec.form.tabs = spec.form.tabs || [];
            spec.form.sections.forEach((sec) => (sec.columns = sec.columns && sec.columns.length ? sec.columns : [[]]));
            spec.kanban = spec.kanban || { title: "x_name", fields: [] };
            spec.kanban.fields = spec.kanban.fields || [];
        } else {
            spec.place = spec.place || [];
            spec.hide = spec.hide || [];
            spec.relabel = spec.relabel || {};
        }
        for (const f of spec.fields) {
            if (f.type === "lines") {
                f.lines = f.lines || {};
                f.lines.fields = f.lines.fields || [];
            }
        }
        this.state.spec = spec;
        this.state.saved = new Set(m.spec.fields.map((f) => f.name));
        this.state.dirty = [];
        this.state.sel = null;
        this.state.view = "form";
        this.state.panel = "add";
        this.state.auto = null;
    }
    get own() {
        return this.state.m?.own;
    }
    get appModels() {
        const app = this.state.home?.apps.find((a) => a.id === this.state.m?.app_id);
        return app ? app.models : [];
    }
    changed(what) {
        if (!this.state.dirty.includes(what)) {
            this.state.dirty.push(what);
        }
    }
    async save() {
        const summary = this.state.dirty.slice(0, 4).join(", ") + (this.state.dirty.length > 4 ? ` and ${this.state.dirty.length - 4} more` : "");
        try {
            const m = await this.call("save", [this.state.m.model, this.state.spec, summary || "Changed"]);
            const keepSel = this.state.sel;
            this.setModel(m);
            this.state.sel = keepSel && m.spec.fields.find((f) => f.name === keepSel) ? keepSel : null;
            this.notification.add("Saved. The app is updated.", { type: "success" });
            await this.loadHome();
        } catch (e) {
            throw e;
        }
    }
    discard() {
        this.setModel(this.state.m);
    }
    async back() {
        this.state.screen = "home";
        this.state.m = null;
        await this.loadHome();
    }
    openRecords() {
        if (this.state.m.action_id) {
            this.action.doAction(this.state.m.action_id);
        } else {
            this.action.doAction({ type: "ir.actions.act_window", res_model: this.state.m.model, views: [[false, "list"], [false, "form"]] });
        }
    }
    exportApp() {
        window.location.href = `/sonador_studio/export/${this.state.m.app_id}`;
    }
    async addModel() {
        const label = (this.state.newModel || "").trim();
        if (!label) {
            return;
        }
        const model = await this.call("model_add", [this.state.m.app_id, label]);
        this.state.newModel = "";
        await this.loadHome();
        await this.open(model);
    }

    // fields
    field(name) {
        return this.state.spec.fields.find((f) => f.name === name);
    }
    existing(name) {
        return (this.state.m.existing || []).find((f) => f.name === name);
    }
    newName(label) {
        const taken = new Set(this.state.spec.fields.map((f) => f.name).concat((this.state.m.existing || []).map((f) => f.name)));
        const base = (this.own ? "x_" : "x_studio_") + slug(label);
        let name = base;
        let n = 2;
        while (taken.has(name) || ["x_active", "x_currency_id", "x_parent_id", "x_sequence"].includes(name)) {
            name = `${base}_${n++}`;
        }
        return name;
    }
    makeField(type) {
        const label = DEFAULT_LABEL[type] || "Field";
        const f = { name: this.newName(label), label, type };
        if (type === "choice") {
            f.options = [{ value: "option_1", label: "Option 1" }, { value: "option_2", label: "Option 2" }];
        }
        if (type === "stage") {
            f.options = ["New", "In progress", "Done"].map((l) => ({ value: optValue(l), label: l }));
        }
        if (type === "link" || type === "tags") {
            f.relation = "res.partner";
        }
        if (type === "lines") {
            f.lines = { fields: [{ name: "x_name", label: "Description", type: "text", required: true }, { name: "x_qty", label: "Quantity", type: "decimal" }] };
        }
        if (type === "formula") {
            f.formula = "";
        }
        return f;
    }
    addField(type, target) {
        const spec = this.state.spec;
        const f = this.makeField(type);
        spec.fields.push(f);
        if (this.own) {
            if (type === "stage" && !spec.form.status) {
                spec.form.status = f.name;
            } else {
                this.placeOwn(f.name, target);
            }
            if (spec.list.length < 7 && !["lines", "richtext", "file", "image", "longtext"].includes(type)) {
                spec.list.push(f.name);
            }
        } else {
            spec.place = spec.place || [];
            if (target?.after) {
                spec.place.push({ field: f.name, after: target.after });
            }
            spec.list = spec.list || [];
        }
        this.state.sel = f.name;
        this.state.panel = "field";
        this.changed(`added ${f.label}`);
    }
    placeOwn(name, target) {
        const form = this.state.spec.form;
        form.sections = form.sections || [];
        form.tabs = form.tabs || [];
        this.unplace(name);
        if (target?.tab !== undefined) {
            form.tabs[target.tab].fields.splice(target.index ?? form.tabs[target.tab].fields.length, 0, name);
            return;
        }
        if (!form.sections.length) {
            form.sections.push({ label: "", columns: [[], []] });
        }
        const s = target?.section ?? form.sections.length - 1;
        const c = target?.column ?? (form.sections[s].columns[0].length <= (form.sections[s].columns[1] || []).length ? 0 : form.sections[s].columns.length - 1);
        const col = form.sections[s].columns[c];
        col.splice(target?.index ?? col.length, 0, name);
    }
    unplace(name) {
        const form = this.state.spec.form;
        for (const s of form.sections || []) {
            for (const col of s.columns) {
                const i = col.indexOf(name);
                if (i >= 0) {
                    col.splice(i, 1);
                }
            }
        }
        for (const t of form.tabs || []) {
            const i = t.fields.indexOf(name);
            if (i >= 0) {
                t.fields.splice(i, 1);
            }
        }
        if (form.status === name) {
            form.status = null;
        }
        if (form.image === name) {
            form.image = null;
        }
    }
    removeField(name) {
        const spec = this.state.spec;
        const f = this.field(name);
        spec.fields = spec.fields.filter((x) => x.name !== name);
        if (this.own) {
            this.unplace(name);
        } else {
            spec.place = (spec.place || []).filter((p) => p.field !== name);
        }
        spec.list = (spec.list || []).filter((n) => n !== name);
        if (spec.kanban?.fields) {
            spec.kanban.fields = spec.kanban.fields.filter((n) => n !== name);
        }
        if (spec.calendar === name) {
            spec.calendar = null;
        }
        this.state.sel = null;
        this.state.panel = "add";
        this.changed(`removed ${f.label}`);
    }
    renameIfNew(f, label) {
        f.label = label;
        if (this.state.saved.has(f.name) || !label.trim()) {
            return;
        }
        // not saved yet: the technical name follows the label
        const old = f.name;
        f.name = "";
        const name = this.newName(label);
        f.name = name;
        const swap = (arr) => arr && arr.forEach((n, i) => n === old && (arr[i] = name));
        const form = this.state.spec.form || {};
        (form.sections || []).forEach((s) => s.columns.forEach(swap));
        (form.tabs || []).forEach((t) => swap(t.fields));
        swap(this.state.spec.list);
        (this.state.spec.place || []).forEach((p) => p.field === old && (p.field = name));
        if (form.status === old) form.status = name;
        if (form.image === old) form.image = name;
        if (this.state.sel === old) this.state.sel = name;
    }
    onLabel(ev) {
        const f = this.field(this.state.sel);
        this.renameIfNew(f, ev.target.value);
        this.changed(`renamed ${f.label}`);
    }
    setProp(key, value) {
        const f = this.field(this.state.sel);
        f[key] = value;
        this.changed(`changed ${f.label}`);
    }
    // choices
    addOption(f) {
        const n = f.options.length + 1;
        f.options.push({ value: `option_${n}_${Date.now() % 1000}`, label: `Option ${n}` });
        this.changed(`changed ${f.label}`);
    }
    setOption(f, o, label) {
        o.label = label;
        if (!this.state.saved.has(f.name)) {
            o.value = optValue(label) || o.value;
        }
        this.changed(`changed ${f.label}`);
    }
    moveOption(f, i, d) {
        const j = i + d;
        if (j < 0 || j >= f.options.length) return;
        [f.options[i], f.options[j]] = [f.options[j], f.options[i]];
        this.changed(`changed ${f.label}`);
    }
    removeOption(f, i) {
        f.options.splice(i, 1);
        this.changed(`changed ${f.label}`);
    }
    // table columns
    addColumn(f, type) {
        const cols = f.lines.fields;
        const label = DEFAULT_LABEL[type] || "Column";
        let name = "x_" + slug(label);
        let n = 2;
        while (cols.some((c) => c.name === name)) name = `x_${slug(label)}_${n++}`;
        const c = { name, label, type };
        if (type === "formula") c.formula = "";
        if (type === "choice") c.options = [{ value: "option_1", label: "Option 1" }];
        if (type === "link") c.relation = "product.product";
        cols.push(c);
        this.changed(`changed ${f.label}`);
    }
    removeColumn(f, i) {
        f.lines.fields.splice(i, 1);
        this.changed(`changed ${f.label}`);
    }
    setColumnLabel(f, c, label) {
        c.label = label;
        if (!this.state.saved.has(f.name) && c.name !== "x_name") {
            const base = "x_" + slug(label);
            let name = base;
            let n = 2;
            while (f.lines.fields.some((x) => x !== c && x.name === name)) name = `${base}_${n++}`;
            f.lines.fields.forEach((x) => x.formula && (x.formula = x.formula.replace(new RegExp(`\\b${c.name}\\b`, "g"), name)));
            c.name = name;
        }
        this.changed(`changed ${f.label}`);
    }
    numericOf(fields) {
        return fields.filter((x) => NUMERIC.includes(x.type));
    }
    formulaChips(f) {
        const spec = this.state.spec;
        const chips = this.numericOf(spec.fields.filter((x) => x.name !== f.name)).map((x) => ({ label: x.label, code: x.name }));
        for (const l of spec.fields.filter((x) => x.type === "lines")) {
            for (const c of this.numericOf(l.lines?.fields || [])) {
                chips.push({ label: `sum of ${l.label} › ${c.label}`, code: `sum(${l.name}.${c.name})` });
            }
        }
        return chips;
    }
    insertFormula(target, code) {
        target.formula = ((target.formula || "") + " " + code).trim();
        this.changed("changed a formula");
    }
    formulaText(formula, fields) {
        // show the formula with labels instead of technical names
        let text = formula || "";
        for (const l of fields.filter((x) => x.type === "lines")) {
            for (const c of (l.lines && l.lines.fields) || []) {
                text = text.replace(new RegExp(`\\b${l.name}\\.${c.name}\\b`, "g"), `[${l.label} › ${c.label}]`);
            }
        }
        for (const x of fields) {
            text = text.replace(new RegExp(`\\b${x.name}\\b`, "g"), `[${x.label}]`);
        }
        return text;
    }
    // form layout
    addSection() {
        this.state.spec.form.sections.push({ label: "New section", columns: [[], []] });
        this.changed("added a section");
    }
    removeSection(i) {
        const s = this.state.spec.form.sections[i];
        const loose = s.columns.flat();
        this.state.spec.form.sections.splice(i, 1);
        loose.forEach((n) => this.placeOwn(n));
        this.changed("removed a section");
    }
    addTab() {
        this.state.spec.form.tabs.push({ label: "New tab", fields: [] });
        this.changed("added a tab");
    }
    removeTab(i) {
        const loose = this.state.spec.form.tabs[i].fields;
        this.state.spec.form.tabs.splice(i, 1);
        loose.forEach((n) => this.placeOwn(n));
        this.changed("removed a tab");
    }
    setColumns(s, n) {
        const sec = this.state.spec.form.sections[s];
        const all = sec.columns.flat();
        sec.columns = Array.from({ length: n }, () => []);
        all.forEach((name, i) => sec.columns[i % n].push(name));
        this.changed("changed the layout");
    }
    // drag and drop
    dragStart(payload) {
        this.drag = payload;
    }
    drop(target, ev) {
        ev.preventDefault();
        const d = this.drag;
        this.drag = null;
        this.state.over = null;
        if (!d) return;
        if (d.type) {
            this.addField(d.type, target);
        } else if (d.field) {
            if (this.own) {
                this.placeOwn(d.field, target);
            } else {
                const p = (this.state.spec.place || []).find((x) => x.field === d.field);
                if (p) {
                    p.after = target.after;
                } else {
                    this.state.spec.place.push({ field: d.field, after: target.after });
                }
            }
            this.changed(`moved ${this.field(d.field)?.label || "a field"}`);
        }
    }
    over(key, ev) {
        ev.preventDefault();
        this.state.over = key;
    }
    select(name) {
        this.state.sel = name;
        this.state.panel = "field";
    }
    // existing models: hide / rename their fields
    isHidden(name) {
        return (this.state.spec.hide || []).includes(name);
    }
    toggleHide(name) {
        const spec = this.state.spec;
        spec.hide = spec.hide || [];
        const i = spec.hide.indexOf(name);
        if (i >= 0) spec.hide.splice(i, 1);
        else spec.hide.push(name);
        this.changed(i >= 0 ? `showed ${name}` : `hid ${name}`);
    }
    relabel(name, label) {
        const spec = this.state.spec;
        spec.relabel = spec.relabel || {};
        if (label) spec.relabel[name] = label;
        else delete spec.relabel[name];
        this.changed(`renamed ${name}`);
    }
    placedAfter(name) {
        return (this.state.spec.place || []).filter((p) => p.after === name).map((p) => this.field(p.field)).filter(Boolean);
    }
    get unplacedExtra() {
        const placed = new Set((this.state.spec.place || []).map((p) => p.field));
        return this.state.spec.fields.filter((f) => !placed.has(f.name));
    }
    // list and board
    toggleList(name) {
        const l = this.state.spec.list;
        const i = l.indexOf(name);
        if (i >= 0) l.splice(i, 1);
        else l.push(name);
        this.changed("changed the list");
    }
    moveList(i, d) {
        const l = this.state.spec.list;
        const j = i + d;
        if (j < 0 || j >= l.length) return;
        [l[i], l[j]] = [l[j], l[i]];
        this.changed("changed the list");
    }
    listable() {
        return this.state.spec.fields.filter((f) => !["lines", "richtext", "file", "image"].includes(f.type));
    }
    toggleKanban(name) {
        const k = (this.state.spec.kanban = this.state.spec.kanban || { title: "x_name", fields: [] });
        k.fields = k.fields || [];
        const i = k.fields.indexOf(name);
        if (i >= 0) k.fields.splice(i, 1);
        else k.fields.push(name);
        this.changed("changed the board");
    }
    boardColumns() {
        const st = this.state.spec.form.status && this.field(this.state.spec.form.status);
        return st ? st.options : [{ label: "All", value: "all" }];
    }
    kanbanFields() {
        return (this.state.spec.kanban && this.state.spec.kanban.fields) || [];
    }
    stepLabel(kind) {
        return { set: "Set a field", activity: "Remind someone", note: "Post a note", email: "Send an email" }[kind];
    }
    setSpec(key, value) {
        this.state.spec[key] = value;
        this.changed("changed the screens");
    }
    setForm(key, value) {
        if (value) this.unplace(value);
        this.state.spec.form[key] = value || null;
        this.changed("changed the form");
    }
    fieldsOfType(...types) {
        return this.state.spec.fields.filter((f) => types.includes(f.type));
    }
    sampleValue(f) {
        // what the field will hold, in words, so the form reads like the real one
        if (f.type === "link" || f.type === "tags") {
            const l = (this.state.m.linkable || []).find((x) => x.model === f.relation);
            return l ? l.label.split(" (")[0] : "a record";
        }
        return { text: "text", longtext: "a few lines", number: "12", decimal: "3.50", money: "1,250.00", date: "20/10/2026",
            datetime: "20/10/2026 09:30", checkbox: "yes / no", choice: (f.options || []).map((o) => o.label).join(" / "),
            stage: (f.options || [])[0]?.label, formula: "calculated", file: "a file", image: "a picture", lines: "", richtext: "" }[f.type];
    }

    // ------------------------------------------------------------------ automations
    newAutomation() {
        const status = this.state.spec.form?.status;
        this.state.auto = { id: false, spec: { name: "", trigger: status ? "stage" : "create", field: status || "", value: "", days: 1, when: "before", conditions: [], actions: [] } };
        this.state.panel = "auto";
    }
    editAutomation(a) {
        this.state.auto = { id: a.id, spec: clone(a.spec) };
    }
    autoField(name) {
        return this.state.spec.fields.find((f) => f.name === name) || (this.state.m.existing || []).find((f) => f.name === name);
    }
    allFields() {
        return this.own ? this.state.spec.fields : (this.state.m.existing || []).concat(this.state.spec.fields);
    }
    userFields() {
        return this.state.spec.fields.filter((f) => f.type === "link" && f.relation === "res.users");
    }
    contactFields() {
        return this.state.spec.fields.filter((f) => f.type === "link" && ["res.partner", "res.users"].includes(f.relation));
    }
    addStep(kind) {
        const s = { do: kind };
        if (kind === "set") Object.assign(s, { field: "", value: "" });
        if (kind === "activity") Object.assign(s, { user_field: this.userFields()[0]?.name || "", summary: "", days: 0 });
        if (kind === "note") s.body = "";
        if (kind === "email") Object.assign(s, { to_field: this.contactFields()[0]?.name || "", subject: "", body: "" });
        this.state.auto.spec.actions.push(s);
    }
    async saveAutomation() {
        const a = this.state.auto;
        if (!a.spec.name) a.spec.name = "Automation";
        this.state.m.automations = await this.call("save_automation", [this.state.m.model, a.spec, a.id]);
        this.state.auto = null;
        this.notification.add("Automation saved.", { type: "success" });
    }
    async removeAutomation(a) {
        this.state.m.automations = await this.call("remove_automation", [this.state.m.model, a.id]);
    }
    describeAutomation(spec) {
        const f = this.autoField(spec.field);
        const when = { create: "When one is created", change: `When ${f?.label || "?"} changes`,
            stage: `When ${f?.label || "?"} becomes ${(f?.options || []).find((o) => o.value === spec.value)?.label || spec.value}`,
            date: `${spec.days} day(s) ${spec.when} ${f?.label || "?"}` }[spec.trigger];
        const steps = (spec.actions || []).map((s) => ({ set: `set ${this.autoField(s.field)?.label || s.field}`, activity: "remind someone",
            note: "post a note", email: "send an email" }[s.do])).join(", ");
        return `${when}: ${steps || "nothing yet"}`;
    }

    // ------------------------------------------------------------------ history
    async revert(v) {
        if (this.state.confirmRevert !== v.id) {
            this.state.confirmRevert = v.id;
            return;
        }
        const m = await this.call("revert", [v.id]);
        this.state.confirmRevert = null;
        this.setModel(m);
        this.state.panel = "history";
        this.notification.add("Back to that version.", { type: "success" });
    }
    async undo() {
        const prev = this.state.m.versions[1];
        if (prev) {
            this.state.confirmRevert = prev.id;
            await this.revert(prev);
        }
    }
}

registry.category("actions").add("sonador_studio.studio", Studio);
