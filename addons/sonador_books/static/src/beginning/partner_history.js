import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { fmt } from "./beginning";

/** Maintain Customers / Vendors > History: sales and receipts (purchases and payments) per period. */
export class PartnerHistory extends Component {
    static template = "sonador_books.PartnerHistory";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, offset: null });
        onWillStart(() => this.load(this.props.record.resId));
        onWillUpdateProps((next) => {
            if (next.record.resId !== this.props.record.resId) {
                this.state.offset = null;
                return this.load(next.record.resId);
            }
        });
    }
    fmt(v, z) {
        return fmt(v, z);
    }
    async load(id) {
        this.state.data = id ? await this.orm.call("sb.beginning", "bb_partner_history", [id, this.state.offset]) : null;
        if (this.state.data && this.state.offset === null) {
            this.state.offset = 0;
            this.anchor = this.state.data.anchor;
        }
    }
    async shift(n) {
        this.state.offset += n;
        const id = this.props.record.resId;
        this.state.data = await this.orm.call("sb.beginning", "bb_partner_history", [id, this.state.offset, this.anchor]);
    }
    openBeginning(side) {
        return this.action.doAction({
            type: "ir.actions.client", tag: "sonador_books.beginning_balances", name: "Beginning Balances",
            params: { tab: side.kind === "customer" ? "customers" : "vendors", partner_id: this.props.record.resId },
        });
    }
}
registry.category("view_widgets").add("sb_partner_history", { component: PartnerHistory });
