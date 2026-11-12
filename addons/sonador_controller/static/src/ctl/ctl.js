import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const money = (v) => Number(v || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });

/** Controller overview: what needs attention, how much money is at stake, which checks passed. */
export class ControllerApp extends Component {
    static template = "sonador_controller.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ d: null, busy: false });
        onWillStart(() => this.load());
    }
    money(v) {
        return money(v);
    }
    async load() {
        this.state.d = await this.orm.call("ctl.engine", "overview", []);
    }
    async runNow() {
        this.state.busy = true;
        try {
            this.state.d = await this.orm.call("ctl.engine", "run_now", []);
            this.notification.add("Checks finished.", { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
    openFinding(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "ctl.finding", res_id: id, views: [[false, "form"]] });
    }
    openFindings(check) {
        const domain = [["state", "in", ["open", "review"]]];
        if (check) {
            domain.push(["rule", "=", check]);
        }
        this.action.doAction({ type: "ir.actions.act_window", name: "Findings", res_model: "ctl.finding", domain,
            views: [[false, "list"], [false, "form"]] });
    }
    lastRun() {
        const d = this.state.d;
        if (!d.last_run) {
            return "Never";
        }
        const t = new Date(d.last_run.replace(" ", "T") + "Z");
        return t.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    }
}
registry.category("actions").add("sonador_controller.app", ControllerApp);
