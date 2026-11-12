import { CENTERS } from "@sonador_books/nav_center/nav_center";
import { FEATURES, REPORT_TITLES } from "@sonador_books/report/report_viewer";

const P = (x) => `sonador_purchase.${x}`;

// Vendors & Purchases and Inventory: purchase orders are live
CENTERS.vendors.flows[0] = [
    { label: "Purchase Orders", icon: "fa-shopping-cart", action: P("action_sb_purchase_orders"), hint: "receive, bill, pay against" },
    { label: "Requests for Quotation", icon: "fa-file-text-o", action: P("action_sb_rfq"), create: true },
];
if (CENTERS.inventory.flows[0] && CENTERS.inventory.flows[0][0] && CENTERS.inventory.flows[0][0].coming) {
    CENTERS.inventory.flows[0][0] = { label: "Purchase Orders", icon: "fa-shopping-cart", action: P("action_sb_purchase_orders") };
}
Object.assign(FEATURES, { po_status: { asOf: true }, po_payments: { asOf: true } });
Object.assign(REPORT_TITLES, {
    po_status: "Purchase Orders: Ordered, Received, Billed",
    po_payments: "Purchase Orders: Paid and Owed",
});
