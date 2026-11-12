import { Component, onWillStart, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Documents and printing: pick a document, design it, see your latest real document in that design as you go. */
export class PrintDesigner extends Component {
    static template = "sonador_print.Designer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.frame = useRef("frame");
        this.state = useState({ meta: null, kind: "invoice", st: null, dirty: false, busy: false, html: "", tab: "look" });
        this._timer = null;
        onWillStart(async () => {
            this.state.meta = await this.orm.call("sb.print", "designer", []);
            await this.open(this.state.meta.kinds[0].kind);
        });
    }
    get kinds() {
        return this.state.meta.kinds;
    }
    async open(kind) {
        this.state.kind = kind;
        this.state.st = await this.orm.call("sb.print", "load_style", [kind]);
        this.state.dirty = false;
        await this.refresh();
    }
    set(key, value) {
        this.state.st[key] = value;
        this.state.dirty = true;
        clearTimeout(this._timer);
        this._timer = setTimeout(() => this.refresh(), 350);
    }
    async refresh() {
        this.state.busy = true;
        try {
            this.state.html = await this.orm.call("sb.print", "preview", [this.state.kind, this.state.st]);
        } finally {
            this.state.busy = false;
        }
    }
    async save(forAll = false) {
        this.state.meta = await this.orm.call("sb.print", "save_style", [this.state.kind, this.state.st, forAll]);
        this.state.dirty = false;
        this.notification.add(forAll ? "Saved for every document" : "Design saved", { type: "success" });
    }
    async pdf() {
        const act = await this.orm.call("sb.print", "print_sample", [this.state.kind, this.state.st]);
        await this.action.doAction(act);
    }
}

registry.category("actions").add("sonador_print.designer", PrintDesigner);
