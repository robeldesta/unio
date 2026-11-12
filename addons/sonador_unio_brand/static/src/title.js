/** @odoo-module **/
// The browser tab says Unio where Odoo would have put its own name (pages without a title of their own).
const fix = () => {
    const t = document.title;
    if (t === "Odoo" || / - Odoo$/.test(t)) {
        document.title = t === "Odoo" ? "Unio" : t.replace(/ - Odoo$/, " - Unio");
    }
};
const head = document.querySelector("title") || document.head;
new MutationObserver(fix).observe(head, { childList: true, characterData: true, subtree: true });
fix();
