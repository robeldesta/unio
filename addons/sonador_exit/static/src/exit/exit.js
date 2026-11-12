import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const money = (v) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Leaving and clearance: start, clear each line, HR and the general manager approve; items people hold. */
export class ExitApp extends Component {
    static template = "sonador_exit.App";
    static props = ["*"];
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ d: null, x: null, tab: "list", form: null, notes: {}, amounts: {}, items: null, itemEmp: "", item: null, busy: false });
        onWillStart(() => this.load());
    }
    money(v) {
        return money(v);
    }
    async load() {
        this.state.d = await this.orm.call("sb.exit.service", "home", []);
    }
    async call(method, args, kw = {}) {
        this.state.busy = true;
        try {
            return await this.orm.call("sb.exit.service", method, args, kw);
        } finally {
            this.state.busy = false;
        }
    }
    async open(id) {
        this.state.x = await this.call("get", [id]);
        this.state.form = null;
    }
    newExit(self) {
        this.state.x = null;
        this.state.form = { employee_id: self ? this.state.d.me : "", reason: "resign", last_day: "", note: "", self };
    }
    async start(send) {
        const f = this.state.form;
        this.state.x = await this.call("start", [{ ...f, send }]);
        this.state.form = null;
        await this.load();
        this.notification.add(send ? "Sent: each department head is asked to clear their part." : "Saved, not sent yet.", { type: "success" });
    }
    async act(action) {
        const kw = { note: this.state.notes.main || "" };
        if (action === "hr") {
            kw.pay_months = parseFloat(this.state.notes.months || 0) || 0;
        }
        this.state.x = await this.call("act", [this.state.x.id, action], kw);
        await this.load();
    }
    async clear(l, ok) {
        this.state.x = await this.call("clear", [l.id, ok], { note: this.state.notes[l.id] || "", amount: this.state.amounts[l.id] || 0 });
        await this.load();
    }
    async loadItems() {
        if (this.state.itemEmp) {
            this.state.items = await this.call("items", [this.state.itemEmp]);
            this.state.item = { kind: "laptop", name: "", serial: "", issued_on: "", note: "" };
        }
    }
    async saveItem(it) {
        this.state.items = await this.call("save_item", [this.state.itemEmp, it || this.state.item]);
        this.state.item = { kind: "laptop", name: "", serial: "", issued_on: "", note: "" };
    }
    async returned(it) {
        const today = new Date().toISOString().slice(0, 10);
        this.state.items = await this.call("save_item", [this.state.itemEmp, { id: it.id, kind: it.kind, name: it.name, serial: it.serial,
            issued_on: it.issued || false, returned_on: today, note: it.note }]);
    }
    areaLines(area) {
        return this.state.x.lines.filter((l) => l.area === area);
    }
    get areas() {
        return [["vehicle", "Vehicle"], ["it", "IT equipment"], ["finance", "Money owed"], ["other", "Other items"]].filter((a) => this.areaLines(a[0]).length);
    }
}
registry.category("actions").add("sonador_exit.app", ExitApp);
