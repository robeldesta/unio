import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class Maintenance extends Component {
    static template = "sonador_books.Maintenance";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ files: [], auto: false, busy: false, verify: null, db_size: 0, next: "", keep: 14, folder: "" });
        onWillStart(() => this.refresh());
    }

    async refresh() {
        Object.assign(this.state, await this.orm.call("sb.maintenance", "get_status", []));
    }

    async backupNow() {
        this.state.busy = true;
        try {
            const name = await this.orm.call("sb.maintenance", "backup_now", []);
            this.notification.add(`Backup ${name} written.`, { type: "success" });
            await this.refresh();
        } finally {
            this.state.busy = false;
        }
    }

    async toggleAuto(ev) {
        await this.orm.call("sb.maintenance", "set_auto", [ev.target.checked]);
        await this.refresh();
    }

    async verify() {
        this.state.verify = await this.orm.call("sb.maintenance", "verify", []);
    }

    size(b) {
        return b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " KB";
    }
}
registry.category("actions").add("sonador_books.maintenance", Maintenance);
