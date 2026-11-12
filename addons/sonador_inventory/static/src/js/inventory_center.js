import { CENTERS } from "@sonador_books/nav_center/nav_center";
import { FEATURES, REPORT_TITLES } from "@sonador_books/report/report_viewer";

const I = (x) => `sonador_inventory.${x}`;
const B = (x) => `sonador_books.${x}`;

// Inventory & Services navigation center: the Peachtree flow, now live
Object.assign(CENTERS.inventory, {
    setup: [
        { label: "Inventory Items", icon: "fa-cube", action: B("action_sb_products") },
        { label: "Item Classes & Costing", icon: "fa-sliders", action: B("action_sb_product_categories"), hint: "FIFO / average" },
        { label: "Warehouses & Stores", icon: "fa-building-o", action: I("action_sb_warehouses") },
        { label: "Opening Stock", icon: "fa-flag-o", action: I("action_sb_opening_stock"), create: true },
    ],
    flows: [
        [{ label: "Purchase Orders", icon: "fa-shopping-cart", coming: true },
         { label: "Receive (GRN)", icon: "fa-sign-in", action: I("action_sb_grn"), create: true, hint: "goods before the bill" },
         { label: "Enter Bills", icon: "fa-file-text", action: B("action_sb_bill"), create: true, hint: "receives stock" }],
        [{ label: "Issue (GIV)", icon: "fa-sign-out", action: I("action_sb_giv"), create: true, hint: "to departments, sites, jobs" },
         { label: "Transfer", icon: "fa-exchange", action: I("action_sb_stock_transfer"), create: true, hint: "between warehouses" }],
        [{ label: "Cash Sale", icon: "fa-money", action: B("action_sb_cash_sale"), create: true, hint: "delivers stock" },
         { label: "Sales Invoice", icon: "fa-file-text-o", action: B("action_sb_sale"), create: true, hint: "delivers stock" }],
    ],
    more: [
        { label: "Stock Count", icon: "fa-check-square-o", action: I("action_sb_stock_count"), create: true },
        { label: "Count Sheet", icon: "fa-print", report: "count_sheet" },
        { label: "Bin Card", icon: "fa-book", report: "stock_card" },
        { label: "Not Yet Billed", icon: "fa-hourglass-half", report: "grni" },
        { label: "Reports", icon: "fa-bar-chart", center: "inventory" },
    ],
    defaults: ["inv_valuation", "stock_movement", "stock_card", "cogs_journal", "reorder"],
});

Object.assign(FEATURES, {
    inv_valuation: { asOf: true },
    stock_by_wh: { asOf: true },
    count_sheet: { asOf: true },
    inv_gl_rec: { asOf: true },
    grni: { asOf: true },
    item_costing: { asOf: true },
    price_list: { asOf: true },
    reorder: { asOf: true },
});

Object.assign(REPORT_TITLES, {
    inv_valuation: "Inventory Valuation",
    stock_card: "Stock Card (Bin Card)",
    stock_movement: "Inventory Movement Summary",
    item_costing: "Item Costing",
    cogs_journal: "Cost of Goods Sold Journal",
    adj_journal: "Inventory Adjustment Journal",
    grn_register: "GRN Register",
    giv_register: "GIV Register",
    reorder: "Reorder Worksheet",
    price_list: "Item List / Price List",
    stock_by_wh: "Stock by Warehouse",
    count_sheet: "Physical Count Sheet",
    inv_gl_rec: "Inventory to GL Reconciliation",
    grni: "Goods Received Not Invoiced",
});
