/** @odoo-module **/
// Posted and void documents are voided or corrected, never deleted: the Delete action is not offered for them (the server
// refuses it anyway), so nobody meets Odoo's "Bye-bye, record!" dialog only to be told no.
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { ListController } from "@web/views/list/list_controller";

const LOCKED = {
    "sb.voucher": ["posted", "void"],
    "sb.stock.voucher": ["posted", "void"],
    "sb.bank.rec": ["done"],
};

function locked(resModel, records) {
    const states = LOCKED[resModel];
    return Boolean(states) && records.some((r) => states.includes(r.data.state));
}

patch(FormController.prototype, {
    getStaticActionMenuItems() {
        const items = super.getStaticActionMenuItems(...arguments);
        if (items.delete && LOCKED[this.props.resModel]) {
            const available = items.delete.isAvailable;
            items.delete.isAvailable = () => available() && !locked(this.props.resModel, [this.model.root]);
        }
        return items;
    },
});

patch(ListController.prototype, {
    getStaticActionMenuItems() {
        const items = super.getStaticActionMenuItems(...arguments);
        if (items.delete && LOCKED[this.props.resModel]) {
            const available = items.delete.isAvailable;
            items.delete.isAvailable = () => available() && !locked(this.props.resModel, this.model.root.selection);
        }
        return items;
    },
});
