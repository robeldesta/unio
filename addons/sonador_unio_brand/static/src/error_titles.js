/** @odoo-module **/
// The titles of the message boxes in plain words: "Invalid Operation" and "Validation Error" read like a crash to
// someone who only typed a wrong date. The message itself says what to do.
import { _t } from "@web/core/l10n/translation";
import { odooExceptionTitleMap } from "@web/core/errors/error_dialogs";

const PLAIN = {
    "odoo.exceptions.UserError": _t("Please check"),
    "odoo.exceptions.ValidationError": _t("Please check"),
    "odoo.addons.base.models.ir_actions.ServerActionWithWarningsError": _t("Please check"),
    "odoo.exceptions.AccessError": _t("Not allowed"),
    "odoo.exceptions.AccessDenied": _t("Not allowed"),
    "odoo.exceptions.MissingError": _t("Not found"),
};
for (const [name, title] of Object.entries(PLAIN)) {
    odooExceptionTitleMap.set(name, title);
}
