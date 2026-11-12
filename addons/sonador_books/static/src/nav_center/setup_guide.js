import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class SetupGuide extends Component {
    static template = "sonador_books.SetupGuide";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ steps: [], done: 0, total: 0 });
        onWillStart(() => this.refresh());
    }

    async refresh() {
        Object.assign(this.state, await this.orm.call("sb.report", "get_setup_status", []));
    }

    get pct() {
        return this.state.total ? Math.round((this.state.done / this.state.total) * 100) : 0;
    }

    async go(step) {
        await this.action.doAction(step.action);
    }
}
registry.category("actions").add("sonador_books.setup_guide", SetupGuide);
