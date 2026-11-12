import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE = { draft: "Draft", sent: "With your manager", accepted: "Accepted", returned: "Returned" };

/** Report designer: my reports, the template's sections with my words, report blocks dragged in from the library. */
export class ReportDesigner extends Component {
    static template = "sonador_report_designer.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ home: null, r: null, busy: false, filter: "", dept: "", dragging: null, over: null, note: "", returning: false, saved: true });
        onWillStart(() => this.loadHome());
    }

    // ------------------------------------------------------------------ home
    async loadHome() {
        this.state.home = await this.orm.call("sb.rep.service", "home", []);
        this.state.r = null;
    }
    async start(templateId, kind, from) {
        const id = await this.orm.call("sb.rep.service", "start", [templateId, kind, from]);
        await this.open(id);
    }
    async reopen(x) {
        this.state.home.reopen = await this.orm.call("sb.rep.service", "reopen", [x.template_id, x.employee_id, x.kind, x.from]);
        this.notification.add(`Reopened for ${x.who}.`, { type: "success" });
    }
    async open(id) {
        this.state.busy = true;
        try {
            this.state.r = await this.orm.call("sb.rep.service", "load", [id]);
            this.state.saved = true;
        } finally {
            this.state.busy = false;
        }
    }
    stateLabel(s) {
        return STATE[s] || s;
    }

    // ------------------------------------------------------------------ editing
    get editable() {
        const r = this.state.r;
        return r && r.mine && r.open && ["draft", "returned"].includes(r.state);
    }
    get library() {
        const r = this.state.r;
        const f = this.state.filter.trim().toLowerCase();
        return r.departments.map((d) => ({
            ...d,
            blocks: r.library.filter((b) => b.department === d.code && (!this.state.dept || this.state.dept === d.code)
                && (!f || b.title.toLowerCase().includes(f) || (b.about || "").toLowerCase().includes(f))),
        })).filter((d) => d.blocks.length);
    }
    blockTitle(code) {
        const b = this.state.r.library.find((x) => x.code === code);
        return b ? b.title : code;
    }
    async addBlock(section, code) {
        if (!code || section.blocks.includes(code)) {
            return;
        }
        section.blocks.push(code);
        this.state.saved = false;
        if (!this.state.r.blocks[code]) {
            this.state.r.blocks[code] = await this.orm.call("sb.rep.service", "block", [this.state.r.id, code]);
        }
        await this.save(true);
    }
    removeBlock(section, code) {
        section.blocks.splice(section.blocks.indexOf(code), 1);
        this.state.saved = false;
        this.save(true);
    }
    moveBlock(section, code, step) {
        const i = section.blocks.indexOf(code);
        const j = i + step;
        if (j < 0 || j >= section.blocks.length) {
            return;
        }
        section.blocks.splice(i, 1);
        section.blocks.splice(j, 0, code);
        this.state.saved = false;
        this.save(true);
    }
    setNarrative(section, value) {
        section.narrative = value;
        this.state.saved = false;
    }
    // drag and drop from the library into a section
    onDragStart(ev, code) {
        this.state.dragging = code;
        ev.dataTransfer.setData("text/plain", code);
        ev.dataTransfer.effectAllowed = "copy";
    }
    onDragOver(ev, section) {
        if (!this.editable) {
            return;
        }
        ev.preventDefault();
        this.state.over = section.id;
    }
    onDrop(ev, section) {
        ev.preventDefault();
        const code = ev.dataTransfer.getData("text/plain") || this.state.dragging;
        this.state.over = null;
        this.state.dragging = null;
        this.addBlock(section, code);
    }
    async save(quiet) {
        const r = this.state.r;
        await this.orm.call("sb.rep.service", "save", [r.id, r.sections.map((s) => ({ id: s.id, narrative: s.narrative, blocks: s.blocks }))]);
        this.state.saved = true;
        if (!quiet) {
            this.notification.add("Saved.", { type: "success" });
        }
    }
    async send() {
        await this.save(true);
        await this.orm.call("sb.rep.service", "send", [this.state.r.id]);
        this.notification.add(`Sent to ${this.state.r.manager}.`, { type: "success" });
        await this.open(this.state.r.id);
    }
    async decide(accept) {
        if (!accept && !this.state.returning) {
            this.state.returning = true;
            return;
        }
        await this.orm.call("sb.rep.service", "decide", [this.state.r.id, accept, this.state.note]);
        this.state.returning = false;
        this.state.note = "";
        this.notification.add(accept ? "Accepted." : "Returned with your note.", { type: "success" });
        await this.open(this.state.r.id);
    }
    async exportAs(kind) {
        if (this.editable && !this.state.saved) {
            await this.save(true);
        }
        const url = await this.orm.call("sb.rep.service", "export", [this.state.r.id, kind]);
        window.location.href = url;
    }

    // ------------------------------------------------------------------ showing figures
    fmt(v, money) {
        if (v === null || v === undefined || v === "") {
            return "";
        }
        if (typeof v === "number") {
            return money || !Number.isInteger(v)
                ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                : v.toLocaleString();
        }
        return v;
    }
    isNum(v) {
        return typeof v === "number";
    }
}

registry.category("actions").add("sonador_report_designer.app", ReportDesigner);
