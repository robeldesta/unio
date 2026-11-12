/** @odoo-module **/
// Void in one step: the reason is typed in the confirmation itself. Any "Void" button (name action_void) of a form whose
// record has the field sb_void_reason opens this dialog; the button's help says what voiding does. The reason is written
// on the record and the void runs at once; nothing is voided without one.
import { Component, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

const MIN_REASON = 5;

export class SbVoidReasonDialog extends Component {
    static template = "sonador_books.VoidReasonDialog";
    static components = { Dialog };
    static props = { title: String, body: { type: String, optional: true }, reason: { type: String, optional: true },
                     confirm: Function, close: Function };

    setup() {
        this.state = useState({ reason: this.props.reason || "", tried: false });
        this.input = useRef("reason");
    }
    get ok() {
        return this.state.reason.trim().length >= MIN_REASON;
    }
    async onConfirm() {
        this.state.tried = true;
        if (!this.ok) {
            this.input.el?.focus();
            return;
        }
        await this.props.confirm(this.state.reason.trim());
        this.props.close();
    }
    onKeydown(ev) {
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            this.onConfirm();
        }
    }
}

patch(FormController.prototype, {
    async beforeExecuteActionButton(clickParams) {
        const record = this.model.root;
        if (clickParams.name === "action_void" && clickParams.type === "object" && "sb_void_reason" in record.fields
                && !clickParams.sbReasonGiven) {
            const reason = await new Promise((resolve) => {
                let done = false;
                this.env.services.dialog.add(SbVoidReasonDialog, {
                    title: `Void ${record.data.name || record.data.display_name || ""}`.trim(),
                    body: clickParams.help || "",
                    reason: record.data.sb_void_reason || "",
                    confirm: (text) => { done = true; resolve(text); },
                }, { onClose: () => !done && resolve(null) });
            });
            if (!reason) {
                return false;
            }
            await record.update({ sb_void_reason: reason });
        }
        return super.beforeExecuteActionButton(...arguments);
    },
});
