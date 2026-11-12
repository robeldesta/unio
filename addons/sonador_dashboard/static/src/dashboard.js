/** @odoo-module **/
import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

function esc(s) {
    return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

/** The assistant's answer: **bold**, lists, and links to records (record:MODEL:ID) and lists (list:MODEL:DOMAIN). */
function render(text) {
    let h = esc(text);
    h = h.replace(/\[([^\]]+)\]\((record|list):([^)]+)\)/g, (m, label, kind, rest) =>
        `<a href="#" class="o_sb_dash_link" data-kind="${kind}" data-ref="${encodeURIComponent(rest.replace(/&quot;/g, '"').replace(/&amp;/g, "&"))}">${label}</a>`);
    h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    let out = "", list = false;
    for (const l of h.split("\n")) {
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

/** Dashboard: briefing, needs you, what's off, ask, numbers. Everything comes from sb.dashboard with the person's own rights. */
export class Dashboard extends Component {
    static template = "sonador_dashboard.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ d: null, open: {}, done: {}, refusing: null, note: "", q: "", asking: false, answer: null,
            chat: false, rewriting: false, busy: null, refreshing: false });
        onWillStart(() => this.load());
    }
    async load(fresh = false) {
        this.state.d = await this.orm.call("sb.dashboard", "data", [fresh]);
        if (this.state.d.brief.pending) {
            this.writeBrief(false);  // no await: the page shows plain sentences until Unio AI has written today's briefing
        }
    }
    async writeBrief(force) {
        this.state.rewriting = true;
        try {
            this.state.d.brief = await this.orm.call("sb.dashboard", "rewrite_brief", [force]);
        } catch {
            /* the plain sentences stay */
        } finally {
            this.state.rewriting = false;
        }
    }
    get greeting() {
        const h = new Date().getHours();
        return (h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening") + ", " + this.state.d.user.first;
    }
    async refresh() {
        this.state.refreshing = true;
        try {
            this.state.done = {};
            await this.load(true);
        } finally {
            this.state.refreshing = false;
        }
    }
    get builtAt() {
        const b = this.state.d.built_at;
        if (!b) return "";
        const d = new Date(b.replace(" ", "T") + "Z");
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    get needCount() {
        const n = this.state.d.needs;
        return n.approvals_total - Object.keys(this.state.done).length;
    }
    levelLabel(l) {
        return { bad: "Act now", warn: "Watch", info: "Note" }[l] || "";
    }
    money(v) {
        return v ? (v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) : "";
    }

    // ---------------------------------------------------------------- open the records behind anything
    async openList(model, domain, title) {
        const act = await this.orm.call("sb.dashboard", "open_list", [model, domain, title]);
        if (act) {
            this.action.doAction(act);
        }
    }
    openTile(t) {
        if (t.me) {
            return this.action.doAction("sonador_me.action_me");
        }
        if (t.action) {
            this.openList(t.action.model, t.action.domain, t.label);
        }
    }
    openSignal(s) {
        if (s.me) {
            return this.action.doAction("sonador_me.action_me");
        }
        if (s.action) {
            this.openList(s.action.model, s.action.domain, s.title);
        }
    }
    toggle(key) {
        this.state.open[key] = !this.state.open[key];
    }

    // ---------------------------------------------------------------- decide here (same rules as Me)
    async openDoc(item) {
        const act = await this.orm.call("sb.me", "open_doc", [item.key]);
        if (act) {
            this.action.doAction(act);
        }
    }
    async decide(item, approve) {
        if (!approve && this.state.refusing !== item.key) {
            this.state.refusing = item.key;
            this.state.note = "";
            return;
        }
        this.state.busy = item.key;
        try {
            await this.orm.call("sb.me", "decide", [item.key, approve, approve ? "" : this.state.note]);
            this.state.done[item.key] = approve ? "Approved" : "Sent back";
            this.state.refusing = null;
        } catch (e) {
            // a decision that needs a signed confirmation, or one already decided elsewhere: Me handles it
            const msg = (e.data && e.data.message) || e.message || "";
            this.notification.add(msg.includes("signature") || msg.includes("confirm") ? "This one needs your signature: opening it in Me." : msg,
                { type: "warning" });
            if (msg.includes("signature") || msg.includes("confirm")) {
                this.action.doAction("sonador_me.action_me");
            }
        } finally {
            this.state.busy = null;
        }
    }

    // ---------------------------------------------------------------- ask (Unio AI, with my rights)
    async ask(text) {
        text = (text || this.state.q || "").trim();
        if (!text || this.state.asking) {
            return;
        }
        this.state.q = text;
        this.state.asking = true;
        this.state.answer = null;
        try {
            const r = await this.orm.call("sb.ai", "ask", [this.state.chat, text]);
            this.state.chat = r.id;
            const last = [...r.shown].reverse().find((m) => m.role === "assistant");
            this.state.answer = { q: text, html: render(last ? last.text : ""), steps: (last && last.steps) || [] };
        } catch (e) {
            this.state.answer = { q: text, html: render((e.data && e.data.message) || "Unio AI could not answer just now."), error: true };
        } finally {
            this.state.asking = false;
        }
    }
    onAnswerClick(ev) {
        const a = ev.target.closest(".o_sb_dash_link");
        if (!a) return;
        ev.preventDefault();
        const ref = decodeURIComponent(a.dataset.ref);
        if (a.dataset.kind === "record") {
            const i = ref.lastIndexOf(":");
            this.action.doAction({ type: "ir.actions.act_window", res_model: ref.slice(0, i), res_id: parseInt(ref.slice(i + 1)), views: [[false, "form"]] });
        } else {
            const i = ref.indexOf(":");
            let domain = [];
            try { domain = JSON.parse(ref.slice(i + 1)); } catch { domain = []; }
            this.openList(ref.slice(0, i), domain, a.innerText);
        }
    }
    onAskKey(ev) {
        if (ev.key === "Enter") {
            this.ask();
        }
    }
    openChat() {
        this.action.doAction("sonador_ai.action_ai");
    }

    // ---------------------------------------------------------------- briefing
    rewrite() {
        return this.writeBrief(true);
    }
    briefHtml(p) {
        return markup(esc(p).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>"));
    }

    // ---------------------------------------------------------------- chart
    bars(c) {
        // plot area: x 8..352, y 24..164 (grid lines at quarters of the tallest bar)
        const W = 360, base = 164, top = 24, pl = 8;
        const vals = c.bars.map((b) => b[1]);
        const max = Math.max(...vals, 0.0001);
        const bw = (W - pl * 2) / c.bars.length;
        const best = vals.indexOf(Math.max(...vals));
        return c.bars.map((b, i) => {
            const h = Math.max(((base - top) * Math.max(b[1], 0)) / max, 2), x = pl + i * bw + bw * 0.2, w = bw * 0.6;
            return { x, w, y: base - h, h, cx: x + w / 2, label: b[0], value: b[1],
                partial: c.partial_last && i === c.bars.length - 1, top: i === best };
        });
    }
    newQuestion() {
        this.state.answer = null;
        this.state.q = "";
    }
    cancelRefuse() {
        this.state.refusing = null;
    }
    openMe() {
        this.action.doAction("sonador_me.action_me");
    }
}

registry.category("actions").add("sonador_dashboard.app", Dashboard);
