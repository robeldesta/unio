import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

/** Top-bar note in a cloud system: days left in the trial, or that the system is read-only. */
class SaasStatus extends Component {
    static template = "sonador_saas_client.Status";
    static props = {};
    setup() {
        this.action = useService("action");
        this.isAdmin = user.isAdmin;
        this.state = useState({ s: null });
        onWillStart(async () => {
            try {
                this.state.s = await rpc("/saas/status", {});
            } catch {
                this.state.s = null;
            }
        });
    }
    refer() {
        this.action.doAction("sonador_saas_client.action_refer");
    }
}

registry.category("systray").add("sonador_saas_client.status", { Component: SaasStatus }, { sequence: 1 });
