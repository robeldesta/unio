/** @odoo-module **/
/**
 * Odoo saves the open form when the browser tab is hidden (switching to another app on a phone, another tab, the
 * screen locking), so unsaved work is not lost. It also did that for a brand-new form nobody had touched: every
 * "New" opened and left created an empty draft ("/" vouchers piling up under "Documents not posted").
 * A new record with no change is not saved; anything the person typed is saved as before.
 */
import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

patch(FormController.prototype, {
    beforeVisibilityChange() {
        const root = this.model.root;
        if (document.visibilityState === "hidden" && root && root.isNew && !root.dirty) {
            return;
        }
        return super.beforeVisibilityChange(...arguments);
    },
});
