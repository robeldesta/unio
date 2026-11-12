import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { openReport } from "@sonador_books/common/report_links";

/** Home page of a department app (Inventory = store, Sales = commercial): the task flow and the work waiting. */
export class AppHome extends Component {
    static template = "sonador_inventory.AppHome";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.app = (this.props.action.params && this.props.action.params.app) ||
            (this.props.action.context && this.props.action.context.app) || "store";
        this.state = useState({ data: null, catalog: [], loading: true });
        onWillStart(() => this.refresh());
    }

    async refresh() {
        this.state.loading = true;
        const [data, cat] = await Promise.all([
            this.orm.call("sb.app.home", "get_home", [this.app]),
            this.orm.call("sb.report", "get_catalog", []).catch(() => ({ reports: [] })),
        ]);
        this.state.data = data;
        this.state.catalog = cat.reports || [];
        this.state.loading = false;
    }

    get reports() {
        const keys = (this.state.data && this.state.data.reports) || [];
        return keys.map((k) => this.state.catalog.find((r) => r.key === k)).filter(Boolean);
    }

    async openTile(t) {
        if (t.create) {
            const act = await this.action.loadAction(t.action);
            return this.action.doAction({ ...act, views: [[false, "form"]], view_mode: "form" });
        }
        return this.action.doAction(t.action);
    }

    openList(t) {
        return this.action.doAction(t.action);
    }

    openQueue(q) {
        return this.action.doAction({
            type: "ir.actions.act_window", name: q.action_name, res_model: q.model, domain: q.domain,
            views: [[false, "list"], [false, "form"]],
        });
    }

    openRow(q, row) {
        return this.action.doAction({
            type: "ir.actions.act_window", res_model: q.model, res_id: row.id, views: [[false, "form"]],
        });
    }

    openReport(e) {
        return openReport(this.action, e);
    }
}
registry.category("actions").add("sonador_inventory.app_home", AppHome);
