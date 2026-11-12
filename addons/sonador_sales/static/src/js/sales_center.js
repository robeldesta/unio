import { CENTERS } from "@sonador_books/nav_center/nav_center";
import { REPORT_TITLES } from "@sonador_books/report/report_viewer";

const S = (x) => `sonador_sales.${x}`;

// Customers & Sales: quotes and orders are live, deliveries follow invoices
CENTERS.customers.flows[0] = [
    { label: "Quotes", icon: "fa-file-text-o", action: S("action_sb_quotations"), create: true },
    { label: "Sales Orders", icon: "fa-list-alt", action: S("action_sb_sale_orders"), hint: "invoice from the order" },
];
CENTERS.customers.flows.push([
    { label: "Delivery Orders", icon: "fa-truck", action: S("action_sb_delivery_orders"), hint: "batched truck loads" },
    { label: "Not Yet Delivered", icon: "fa-hourglass-half", report: "undelivered", hint: "invoiced, still in the yard" },
]);
Object.assign(REPORT_TITLES, {
    undelivered: "Invoiced, Not Yet Delivered",
    delivery_register: "Delivery Register",
    open_orders: "Open Sales Orders",
});
