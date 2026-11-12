/** @odoo-module **/
// Odoo names itself in its own messages ("Odoo Server Error", "Your Odoo session expired", "Install Odoo"...):
// every text the web client shows goes through TranslatedString, so the name is swapped there, in any language.
import { TranslatedString } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

const ODOO = /\bOdoo\b(?!\.com)/g;
const valueOf = TranslatedString.prototype.valueOf;
TranslatedString.prototype.valueOf = function () {
    const v = valueOf.call(this);
    return typeof v === "string" && v.includes("Odoo") ? v.replace(ODOO, "Unio") : v;
};

// user menu: Odoo's help site, support and odoo.com account
const items = registry.category("user_menuitems");
for (const key of ["documentation", "support", "odoo_account"]) {
    if (items.contains(key)) {
        items.remove(key);
    }
}
