/** @odoo-module **/
import { Component, markup, onWillStart, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";

const SUGGEST = [
    "Who owes us the most, and how overdue?",
    "Sales this month compared with last month",
    "How much VAT do we owe for this month?",
    "Which items are below their reorder level?",
    "Draft a quotation for our biggest customer with last month's items",
];

function esc(s) {
    return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

/** Small markdown: **bold**, lists, line breaks, and links to records (record:MODEL:ID) and lists (list:MODEL:DOMAIN). */
function render(text) {
    let h = esc(text);
    h = h.replace(/\[([^\]]+)\]\((record|list):([^)]+)\)/g, (m, label, kind, rest) =>
        `<a href="#" class="o_ai_link" data-kind="${kind}" data-ref="${encodeURIComponent(rest.replace(/&quot;/g, '"').replace(/&amp;/g, "&"))}">${label}</a>`);
    h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    const lines = h.split("\n");
    let out = "", list = false;
    for (const l of lines) {
        const m = l.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
        if (m) {
            if (!list) { out += "<ul>"; list = true; }
            out += `<li>${m[1]}</li>`;
        } else {
            if (list) { out += "</ul>"; list = false; }
            out += l.trim() ? `<p>${l}</p>` : "";
        }
    }
    if (list) out += "</ul>";
    return markup(out);
}

export class UnioAi extends Component {
    static template = "sonador_ai.Chat";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.render = render;
        this.SUGGEST = SUGGEST;
        this.scroller = useRef("scroller");
        this.state = useState({ on: false, open: !!this.props.action, chat: null, shown: [], proposals: {}, text: "", busy: false, chats: [], history: false, meter: null, files: [], uploading: false });
        this.fileInput = useRef("fileInput");
        // whether Unio AI is on comes with the page; this week's meter loads when the panel opens
        const flags = session.unio || {};
        this.state.on = !!flags.ai;
        this.state.meter = { enabled: this.state.on, files: flags.ai_files !== false };
        if (this.props.action) {
            onWillStart(() => this.loadMeter());
        }
    }
    get inPage() {
        return !!this.props.action;
    }
    toggle() {
        this.state.open = !this.state.open;
        if (this.state.open) {
            this.loadMeter();
        }
    }
    async loadMeter() {
        try {
            this.state.meter = await this.orm.call("sb.ai", "meter", []);
        } catch {
            /* the meter is a convenience */
        }
    }
    scroll() {
        setTimeout(() => {
            const el = this.scroller.el;
            if (el) el.scrollTop = el.scrollHeight;
        }, 50);
    }
    get filesOn() {
        const m = this.state.meter || {};
        return m.files !== false;
    }
    async addFiles(list) {
        const files = Array.from(list || []).filter((f) => f.type.startsWith("image/") || f.type === "application/pdf");
        if (!files.length) return;
        if (this.state.files.length + files.length > 5) {
            this.notification.add("At most 5 files in one message.", { type: "warning" });
            return;
        }
        this.state.uploading = true;
        try {
            for (const f of files) {
                const data = await new Promise((ok, ko) => {
                    const r = new FileReader();
                    r.onload = () => ok(String(r.result).split(",")[1]);
                    r.onerror = ko;
                    r.readAsDataURL(f);
                });
                const att = await this.orm.call("sb.ai", "upload", [f.name || "photo.jpg", f.type, data]);
                this.state.files.push(att);
            }
        } finally {
            this.state.uploading = false;
        }
    }
    onFiles(ev) {
        this.addFiles(ev.target.files);
        ev.target.value = "";
    }
    onPaste(ev) {
        const files = Array.from(ev.clipboardData?.files || []);
        if (files.length && this.filesOn) {
            ev.preventDefault();
            this.addFiles(files);
        }
    }
    dropFile(f) {
        this.state.files = this.state.files.filter((x) => x.id !== f.id);
    }
    async send(text) {
        const files = this.state.files;
        const q = (text || this.state.text).trim();
        if ((!q && !files.length) || this.state.busy) return;
        this.state.text = "";
        this.state.files = [];
        this.state.shown.push({ role: "user", text: q, files: files.map((f) => ({ id: f.id, name: f.name })) });
        this.state.busy = true;
        this.scroll();
        try {
            const r = await this.orm.call("sb.ai", "ask", [this.state.chat, q, files.map((f) => f.id)]);
            this.state.chat = r.id;
            this.state.shown = r.shown;
            this.state.proposals = r.proposals;
        } catch (e) {
            this.state.shown.push({ role: "assistant", text: (e.data && e.data.message) || "Something went wrong. Try again." });
        } finally {
            this.state.busy = false;
            this.scroll();
            this.orm.call("sb.ai", "meter", []).then((m) => (this.state.meter = m)).catch(() => {});
        }
    }
    get used() {
        // the tighter of the company's week and the person's share of it
        const m = this.state.meter || {};
        return Math.max(m.pct || 0, m.user_pct || 0);
    }
    onKey(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send();
        }
    }
    newChat() {
        this.state.chat = null;
        this.state.shown = [];
        this.state.proposals = {};
        this.state.history = false;
    }
    async showHistory() {
        this.state.chats = await this.orm.call("sb.ai", "chats", []);
        this.state.history = true;
    }
    async openChat(c) {
        const r = await this.orm.call("sb.ai", "load_chat", [c.id]);
        this.state.chat = r.id;
        this.state.shown = r.shown;
        this.state.proposals = r.proposals;
        this.state.history = false;
        this.scroll();
    }
    onClick(ev) {
        const a = ev.target.closest(".o_ai_link");
        if (!a) return;
        ev.preventDefault();
        const ref = decodeURIComponent(a.dataset.ref);
        if (a.dataset.kind === "record") {
            const [model, id] = [ref.slice(0, ref.lastIndexOf(":")), parseInt(ref.slice(ref.lastIndexOf(":") + 1))];
            this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]] });
        } else {
            const i = ref.indexOf(":");
            let domain = [];
            try {
                domain = JSON.parse(ref.slice(i + 1));
            } catch {
                domain = [];
            }
            this.action.doAction({ type: "ir.actions.act_window", res_model: ref.slice(0, i), name: a.innerText, domain, views: [[false, "list"], [false, "form"]] });
        }
    }
    async create(p) {
        const act = await this.orm.call("sb.ai", "create_proposal", [this.state.chat, p.id]);
        p.created = true;
        this.notification.add(p.type === "attach" ? "Attached." : `${p.kind} created as a draft.`, { type: "success" });
        await this.action.doAction(act);
    }
    turnProposals(m) {
        return (m.proposals || []).map((p) => this.state.proposals[p.id] || p);
    }
}

registry.category("actions").add("sonador_ai.chat", UnioAi);
registry.category("systray").add("sonador_ai.systray", { Component: UnioAi }, { sequence: 3 });
