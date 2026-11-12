import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const PRIO = [["urgent", "Urgent", "Work has stopped"], ["high", "High", "Work is hard"], ["normal", "Normal", ""], ["low", "Low", "When you can"]];
const KINDS = [["broken", "Something does not work"], ["howto", "How do I…?"], ["data", "Wrong data to correct"], ["access", "Login or access"], ["request", "Something new"]];

/** "Report a problem": the screen and record you are on come with it. */
export class ReportDialog extends Component {
    static template = "sonador_support.Report";
    static components = { Dialog };
    static props = ["*"];
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.prio = PRIO;
        this.kinds = KINDS;
        const ctl = this.action.currentController;
        const p = (ctl && ctl.props) || {};
        this.ctx = { url: window.location.href, screen: (ctl && ctl.action && ctl.action.name) || document.title,
            res_model: p.resModel || "", res_id: p.resId || 0 };
        this.state = useState({ subject: "", description: "", kind: "broken", priority: "normal", busy: false });
    }
    async send() {
        this.state.busy = true;
        try {
            const t = await this.orm.call("sb.support.service", "report", [{ ...this.state, ...this.ctx, busy: undefined }]);
            this.notification.add(`Sent as ${t.number}. You get a message as soon as it is answered (Support › My problems).`, { type: "success" });
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

class ReportButton extends Component {
    static template = "sonador_support.Systray";
    static props = ["*"];
    setup() {
        this.dialog = useService("dialog");
    }
    open() {
        this.dialog.add(ReportDialog, {});
    }
}
registry.category("systray").add("sonador_support.report", { Component: ReportButton }, { sequence: 30 });

/** Support: my problems, and for IT / the admin the queue. */
export class SupportHub extends Component {
    static template = "sonador_support.Hub";
    static props = ["*"];
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({ d: null, tab: "mine", t: null, reply: "", note: "", busy: false, assign: "" });
        onWillStart(() => this.load());
    }
    async load() {
        this.state.d = await this.orm.call("sb.support.service", "home", []);
        if (this.state.d.agent && this.state.tab === "mine" && !this.state.d.mine.length) {
            this.state.tab = "queue";
        }
    }
    get rows() {
        const d = this.state.d;
        return this.state.tab === "queue" ? d.queue : this.state.tab === "done" ? d.done : d.mine;
    }
    setT(t) {
        for (const m of t.messages || []) {
            m.body = markup(m.body);  // the server sends sanitized chatter HTML
        }
        this.state.t = t;
    }
    async open(id) {
        this.setT(await this.orm.call("sb.support.service", "ticket", [id]));
        this.state.reply = "";
        this.state.note = "";
    }
    async act(action, note) {
        this.state.busy = true;
        try {
            this.setT(await this.orm.call("sb.support.service", "act", [this.state.t.id, action], { note: note ?? this.state.note }));
            this.state.note = "";
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
    async send() {
        if (!this.state.reply.trim()) {
            return;
        }
        this.setT(await this.orm.call("sb.support.service", "say", [this.state.t.id, this.state.reply]));
        this.state.reply = "";
        await this.load();
    }
    report() {
        this.dialog.add(ReportDialog, {}, { onClose: () => this.load() });
    }
    openScreen() {
        const t = this.state.t;
        if (t.res_model && t.res_id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: t.res_model, res_id: t.res_id, views: [[false, "form"]] });
        } else if (t.url) {
            window.open(t.url, "_blank");
        }
    }
    hours(h) {
        return h < 8 ? `${h} h` : `${Math.round((h / 8) * 10) / 10} days`;
    }
}
registry.category("actions").add("sonador_support.hub", SupportHub);
