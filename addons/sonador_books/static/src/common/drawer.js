import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { registry } from "@web/core/registry";

/** A record form in a right-hand slide-over. Esc / x closes it and leaves the page exactly as it was. */
export class SbRecordDrawer extends FormViewDialog {
    static template = "sonador_books.SbRecordDrawer";
}

export function openDrawer(dialogService, { resModel, resId, title, context, onClose }) {
    return dialogService.add(SbRecordDrawer, { resModel, resId, title: title || "", context: { ...(context || {}), sb_in_drawer: true }, canExpand: true },
        { onClose });
}

// "Post & Close": go back to wherever the user came from
registry.category("actions").add("sonador_books.back", async (env) => {
    try {
        await env.services.action.restore();
    } catch {
        await env.services.action.doAction("sonador_books.action_sb_nav_center");
    }
});

// "Post & New": a fresh voucher of the same type, replacing the current screen (no growing breadcrumb)
registry.category("actions").add("sonador_books.voucher_new", async (env, action) => {
    const params = action.params || {};
    await env.services.action.doAction({
        type: "ir.actions.act_window",
        res_model: "sb.voucher",
        views: [[false, "form"]],
        name: params.name || "New voucher",
        context: { default_voucher_type: params.voucher_type },
    }, { stackPosition: "replaceCurrentAction" });
});
