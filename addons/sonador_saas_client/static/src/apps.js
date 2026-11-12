/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** The company's apps: every one is included in the price; switch on what the business uses. */
export class SaasApps extends Component {
    static template = "sonador_saas_client.Apps";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.menu = useService("menu");
        this.notification = useService("notification");
        this.state = useState({ apps: [], busy: null });
        onWillStart(async () => {
            this.state.apps = await this.orm.call("saas.apps", "list_apps", []);
        });
    }
    icon(app) {
        if (!app.icon) {
            return app.icon_url || "";
        }
        return app.icon ? `data:image/${app.icon.startsWith("PHN2") ? "svg+xml" : "png"};base64,${app.icon}` : "";
    }
    async toggle(app) {
        const adding = !app.added;
        this.state.busy = app.key;
        let res;
        try {
            res = await this.orm.call("saas.apps", "set_app", [app.key, !app.on]);
        } catch (e) {
            this.state.busy = null;
            this.state.apps = await this.orm.call("saas.apps", "list_apps", []);
            throw e;
        }
        if (adding || (res && res.reload)) {
            this.notification.add(`${app.name} is added. Opening it now…`, { type: "success" });
            window.location.reload();
            return;
        }
        this.state.apps = res;
        await this.menu.reload();
        this.state.busy = null;
        this.notification.add(`${app.name} is ${app.on ? "hidden" : "on"}.`, { type: "success" });
    }
}

registry.category("actions").add("sonador_saas_client.apps", SaasApps);
