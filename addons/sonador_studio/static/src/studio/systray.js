/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

/** A Studio button in the top bar: opens Studio on the screen being looked at. */
export class StudioSystray extends Component {
    static template = "sonador_studio.Systray";
    static props = {};

    setup() {
        this.action = useService("action");
    }
    open() {
        const ctrl = this.action.currentController;
        const model = ctrl && ctrl.props && ctrl.props.resModel;
        const context = model && !["sb.studio.app"].includes(model) ? { studio_model: model } : {};
        this.action.doAction({ type: "ir.actions.client", tag: "sonador_studio.studio", name: "Studio", context });
    }
}

registry.category("systray").add("sonador_studio.systray", { Component: StudioSystray, isDisplayed: () => user.isSystem }, { sequence: 1 });
