/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Refer a friend: the company's link, what the friend gets, what the company earns, and who joined. */
export class SaasRefer extends Component {
    static template = "sonador_saas_client.Refer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ r: null, copied: false });
        onWillStart(async () => {
            this.state.r = await this.orm.call("saas.referral", "info", []);
        });
    }
    async copy() {
        try {
            await navigator.clipboard.writeText(this.state.r.link);
            this.state.copied = true;
            this.notification.add("Link copied.", { type: "success" });
        } catch {
            this.notification.add("Select the link and copy it.", { type: "info" });
        }
    }
    get shareText() {
        const r = this.state.r;
        return encodeURIComponent(`We run our business on Unio: accounting, sales, stock, payroll in one system. Start with ${r.offer_pct}% off your first ${r.offer_months} months: ${r.link}`);
    }
}

registry.category("actions").add("sonador_saas_client.refer", SaasRefer);
