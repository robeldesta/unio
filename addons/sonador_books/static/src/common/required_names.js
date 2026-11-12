/** @odoo-module **/
// "Missing required fields" names them: "Fill in: Fiscal receipt / e-invoice no., Goods or services", with the label the
// screen shows (else the field's own name).
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { Record } from "@web/model/relational_model/record";

function screenLabel(fieldName) {
    // the form's own label for the field (labels are "for" the field's input: sb_pad_number_1)
    const prefix = `${fieldName}_`;
    for (const label of document.querySelectorAll(".o_form_view label.o_form_label[for]")) {
        const target = label.getAttribute("for");
        if (target.startsWith(prefix) && /^\d+$/.test(target.slice(prefix.length)) && label.offsetParent) {
            const text = label.textContent.replace(/\?\s*$/, "").trim();
            if (text) {
                return text;
            }
        }
    }
    return "";
}

patch(Record.prototype, {
    _displayInvalidFieldNotification() {
        const names = [...(this._invalidFields || [])].map(
            (f) => screenLabel(f) || (this.fields[f] && this.fields[f].string) || f
        );
        if (!names.length) {
            return super._displayInvalidFieldNotification(...arguments);
        }
        return this.model.notification.add(`${_t("Fill in:")} ${[...new Set(names)].join(", ")}`, {
            title: _t("Missing required fields"),
            type: "danger",
        });
    },
});
