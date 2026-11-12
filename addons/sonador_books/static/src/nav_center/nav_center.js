import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { SbChart, SB_AGING, sbMoney } from "../common/chart";
import { openReport, exportReport } from "../common/report_links";

const A = (x) => `sonador_books.${x}`;

// tile: { label, icon, action | report | coming, create, draft, hint }
export const CENTERS = {
    business: { title: "Business Status", icon: "fa-tachometer" },
    customers: {
        title: "Customers & Sales", icon: "fa-users", category: "ar",
        setup: [
            { label: "Customers", icon: "fa-users", action: A("action_sb_customers") },
            { label: "Sales Taxes", icon: "fa-percent", action: "account.action_tax_form" },
            { label: "Jobs", icon: "fa-briefcase", coming: true },
        ],
        flows: [
            [{ label: "Quotes", icon: "fa-file-text-o", coming: true }, { label: "Sales Orders", icon: "fa-list-alt", coming: true }],
            [{ label: "Cash Sale", icon: "fa-money", action: A("action_sb_cash_sale"), create: true, draft: "cash_sale", hint: "paid on the spot" },
             { label: "Bank Deposit", icon: "fa-bank", action: A("action_sb_bank_deposit"), hint: "undeposited to bank" }],
            [{ label: "Sales Invoice", icon: "fa-file-text", action: A("action_sb_sale"), create: true, draft: "sale", hint: "on account" },
             { label: "Receive Money", icon: "fa-download", action: A("action_sb_receipt"), create: true, draft: "receipt", hint: "CRV" },
             { label: "Cheques", icon: "fa-ticket", action: A("action_sb_cheques"), hint: "deposit, bounce" }],
        ],
        more: [
            { label: "Customer Statements", icon: "fa-envelope-o", report: "cust_statement" },
            { label: "Credits & Returns", icon: "fa-undo", action: A("action_sb_sale_return"), create: true, draft: "sale_return" },
            { label: "Finance Charges", icon: "fa-clock-o", action: A("action_sb_finance_charge") },
            { label: "Reports", icon: "fa-bar-chart", center: "ar" },
        ],
        defaults: ["aged_ar", "cust_ledger", "crj", "sj", "cust_sales_history"],
    },
    vendors: {
        title: "Vendors & Purchases", icon: "fa-truck", category: "ap",
        setup: [
            { label: "Vendors", icon: "fa-truck", action: A("action_sb_vendors") },
            { label: "Write Cheque", icon: "fa-pencil-square-o", action: A("action_sb_payment"), create: true, hint: "CPV" },
            { label: "Withholding", icon: "fa-scissors", report: "wht_payable", hint: "register for the tax office" },
        ],
        flows: [
            [{ label: "Purchase Orders", icon: "fa-shopping-cart", coming: true }],
            [{ label: "Cash Purchase", icon: "fa-money", action: A("action_sb_cash_purchase"), create: true, draft: "cash_purchase", hint: "paid on the spot" }],
            [{ label: "Enter Bills", icon: "fa-file-text", action: A("action_sb_bill"), create: true, draft: "bill", hint: "on account" },
             { label: "Pay Bills", icon: "fa-upload", action: A("action_sb_payment"), create: true, draft: "payment", hint: "CPV" },
             { label: "Pay Several Bills", icon: "fa-list-ol", action: A("action_sb_batch_payment") }],
        ],
        more: [
            { label: "Void Cheques", icon: "fa-ban", action: A("action_sb_cheques") },
            { label: "Electronic Payment", icon: "fa-exchange", coming: true },
            { label: "Credits & Returns", icon: "fa-undo", action: A("action_sb_purchase_return"), create: true, draft: "purchase_return" },
            { label: "Cash Requirements", icon: "fa-calendar-check-o", report: "cash_requirements" },
            { label: "Reports", icon: "fa-bar-chart", center: "ap" },
        ],
        defaults: ["aged_ap", "vend_ledger", "pj", "cdj", "wht_payable"],
    },
    inventory: {
        title: "Inventory & Services", icon: "fa-cubes", category: "inventory",
        setup: [
            { label: "Inventory Items", icon: "fa-cube", action: A("action_sb_products") },
            { label: "Costing (FIFO / AVCO)", icon: "fa-sliders", action: A("action_sb_product_categories") },
            { label: "Assemblies", icon: "fa-sitemap", coming: true },
            { label: "Prices", icon: "fa-tags", coming: true },
        ],
        flows: [
            [{ label: "Purchase Orders", icon: "fa-shopping-cart", coming: true }, { label: "Receive (GRN)", icon: "fa-sign-in", coming: true },
             { label: "Issue (GIV)", icon: "fa-sign-out", coming: true }],
        ],
        more: [
            { label: "Stock Count", icon: "fa-check-square-o", coming: true },
            { label: "Adjustments", icon: "fa-random", coming: true },
            { label: "Reports", icon: "fa-bar-chart", center: "inventory" },
        ],
        defaults: [],
    },
    payroll: {
        title: "Employees & Payroll", icon: "fa-id-badge", category: "payroll",
        setup: [
            { label: "Employees", icon: "fa-id-badge", action: A("action_sb_employees") },
            { label: "Users", icon: "fa-user", action: "base.action_res_users" },
            { label: "Income Tax Table", icon: "fa-percent", action: A("action_sb_payroll_brackets") },
            { label: "Payroll Setup", icon: "fa-cog", action: A("action_sb_company_settings") },
        ],
        flows: [[{ label: "Time Sheets", icon: "fa-clock-o", coming: true },
                 { label: "Payroll Run", icon: "fa-calculator", action: A("action_sb_payroll"), create: true, hint: "PAYE + pension" },
                 { label: "Pay Employees", icon: "fa-money", action: A("action_sb_payroll"), hint: "net salaries" }],
                [{ label: "Remit PAYE & Pension", icon: "fa-university", action: A("action_sb_payment"), create: true, hint: "CPV to the authorities" }]],
        more: [{ label: "Payroll Register", icon: "fa-list", report: "payroll_register" },
               { label: "PAYE Declaration", icon: "fa-file-text-o", report: "emp_income_tax" },
               { label: "Pension declaration", icon: "fa-shield", report: "poessa" },
               { label: "Reports", icon: "fa-bar-chart", center: "payroll" }],
        defaults: ["payroll_register", "emp_income_tax", "poessa", "earnings", "employee_list"],
    },
    banking: {
        title: "Banking", icon: "fa-bank", category: "bank",
        setup: [
            { label: "Write Cheque", icon: "fa-pencil-square-o", action: A("action_sb_payment"), create: true },
            { label: "Account Register", icon: "fa-list", report: "account_register" },
            { label: "Bank & Cash Accounts", icon: "fa-bank", action: A("action_sb_bank_accounts") },
            { label: "Chart of Accounts", icon: "fa-sitemap", action: A("action_sb_chart") },
        ],
        flows: [
            [{ label: "Receive Money", icon: "fa-download", action: A("action_sb_receipt"), create: true, draft: "receipt" },
             { label: "Bank Deposit", icon: "fa-bank", action: A("action_sb_bank_deposit"), hint: "undeposited to bank" },
             { label: "Cheques", icon: "fa-ticket", action: A("action_sb_cheques") }],
            [{ label: "Enter Bills", icon: "fa-file-text", action: A("action_sb_bill"), create: true, draft: "bill" },
             { label: "Pay Bills", icon: "fa-upload", action: A("action_sb_payment"), create: true, draft: "payment" }],
            [{ label: "Bank Transfer", icon: "fa-exchange", action: A("action_sb_transfer"), create: true, draft: "transfer" },
             { label: "Reconcile Accounts", icon: "fa-balance-scale", action: A("action_sb_bank_rec") }],
        ],
        more: [
            { label: "Journal Voucher", icon: "fa-pencil", action: A("action_sb_journal"), create: true, draft: "journal" },
            { label: "Receipt / Payment Purposes", icon: "fa-tags", action: A("action_sb_purpose") },
            { label: "Reports", icon: "fa-bar-chart", center: "bank" },
        ],
        defaults: ["bank_rec", "account_register", "bank_deposits", "deposits_transit", "outstanding_cheques"],
    },
    company: {
        title: "Company", icon: "fa-building", category: "fs",
        setup: [
            { label: "Setup Guide", icon: "fa-magic", action: A("action_sb_setup_guide") },
            { label: "Users", icon: "fa-user", action: "base.action_res_users" },
            { label: "Chart of Accounts", icon: "fa-sitemap", action: A("action_sb_chart") },
            { label: "Settings", icon: "fa-cog", action: A("action_sb_company_settings") },
        ],
        flows: [
            [{ label: "Opening Balances", icon: "fa-flag-o", action: A("action_sb_opening") },
             { label: "Journal Voucher", icon: "fa-pencil", action: A("action_sb_journal"), create: true, draft: "journal" },
             { label: "Accounting Periods", icon: "fa-calendar", action: A("action_sb_periods_open") },
             { label: "Year-End Closing", icon: "fa-step-forward", action: A("action_sb_year_end") }],
        ],
        more: [
            { label: "IFRS Mapping", icon: "fa-check-square-o", action: A("action_sb_ifrs_check") },
            { label: "Accounting Review", icon: "fa-stethoscope", report: "acc_review" },
            { label: "Data Maintenance", icon: "fa-database", action: A("action_sb_maintenance") },
            { label: "Report Center", icon: "fa-bar-chart", action: A("action_sb_report_center") },
        ],
        defaults: ["is_12", "is_ytd", "sfp", "cash_flow", "tb"],
        companyReports: ["gl", "audit_trail", "find_tx", "daily_register", "acc_review"],
    },
};

const ORDER = ["business", "customers", "vendors", "inventory", "payroll", "banking", "company"];

const SHORTCUTS = [
    { key: "cash_sale", label: "Cash Sale", action: A("action_sb_cash_sale"), create: true },
    { key: "sale", label: "Sales Invoice", action: A("action_sb_sale"), create: true },
    { key: "receipt", label: "Receive Money", action: A("action_sb_receipt"), create: true },
    { key: "bill", label: "Enter a Bill", action: A("action_sb_bill"), create: true },
    { key: "payment", label: "Pay a Bill", action: A("action_sb_payment"), create: true },
    { key: "transfer", label: "Bank Transfer", action: A("action_sb_transfer"), create: true },
    { key: "journal", label: "Journal Voucher", action: A("action_sb_journal"), create: true },
    { key: "customers", label: "Customer List", action: A("action_sb_customers") },
    { key: "vendors", label: "Vendor List", action: A("action_sb_vendors") },
    { key: "find", label: "Find Transactions", report: "find_tx" },
    { key: "reports", label: "Report Center", action: A("action_sb_report_center") },
    { key: "tb", label: "Trial Balance", report: "tb" },
    { key: "cheques", label: "Cheques", action: A("action_sb_cheques") },
];
const DEFAULT_SHORTCUTS = ["cash_sale", "sale", "receipt", "payment", "customers", "vendors", "find", "journal"];

function load(key, fallback) {
    try {
        const v = browser.localStorage.getItem(key);
        return v ? JSON.parse(v) : fallback;
    } catch {
        return fallback;
    }
}
function save(key, value) {
    try {
        browser.localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // ignore
    }
}

export class NavCenter extends Component {
    static template = "sonador_books.NavCenter";
    static props = ["*"];
    static components = { SbChart };

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.centers = CENTERS;
        this.order = ORDER;
        this.allShortcuts = SHORTCUTS;
        this.aging = SB_AGING;
        const ctxCenter = this.props.action && this.props.action.context && this.props.action.context.center;
        this.state = useState({
            center: ctxCenter || load("sonador_books.default_center", "business"),
            data: {},
            nav: {},
            catalog: [],
            loading: true,
            shortcuts: load("sonador_books.shortcuts", DEFAULT_SHORTCUTS),
            editShortcuts: false,
            defaultCenter: load("sonador_books.default_center", "business"),
            asOf: "",
        });
        onWillStart(async () => {
            const cat = await this.orm.call("sb.report", "get_catalog", []);
            this.state.catalog = cat.reports;
            await this.refresh();
        });
    }

    get center() {
        return this.centers[this.state.center];
    }

    async refresh() {
        this.state.loading = true;
        const [data, nav] = await Promise.all([
            this.orm.call("sb.report", "get_center_data", [this.state.center, this.state.asOf || false]),
            this.orm.call("sb.voucher", "sb_nav_data", []),
        ]);
        this.state.data = data;
        this.state.nav = nav;
        this.state.loading = false;
    }

    async setAsOf(ev) {
        this.state.asOf = ev.target.value || "";
        await this.refresh();
    }

    async backToToday() {
        this.state.asOf = "";
        await this.refresh();
    }

    async go(center) {
        this.state.center = center;
        await this.refresh();
    }

    makeDefault() {
        save("sonador_books.default_center", this.state.center);
        this.state.defaultCenter = this.state.center;
        this.notification.add(`${this.center.title} will open first from now on.`, { type: "success" });
    }

    // ------------------------------------------------------------------ actions
    entry(key) {
        return this.state.catalog.find((r) => r.key === key);
    }

    async openTile(t) {
        if (t.coming) {
            this.notification.add(`"${t.label}" is coming in a later release.`, { type: "info" });
            return;
        }
        if (t.center) {
            return this.action.doAction("sonador_books.action_sb_report_center");
        }
        if (t.report) {
            const e = this.entry(t.report);
            return e && openReport(this.action, e);
        }
        if (t.create) {
            const act = await this.action.loadAction(t.action);
            return this.action.doAction({ ...act, views: [[false, "form"]], view_mode: "form" });
        }
        return this.action.doAction(t.action);
    }

    openList(t) {
        return this.action.doAction(t.action);
    }

    drafts(type) {
        return ((this.state.nav && this.state.nav.drafts) || {})[type] || 0;
    }

    openPartnerLedger(row, kind) {
        const e = this.entry(kind === "customer" ? "cust_ledger" : "vend_ledger");
        const pid = row.partner_id || row.id;
        return e && openReport(this.action, { ...e, options: { ...e.options, partner_ids: pid ? [pid] : [] } });
    }

    openAccountRegister(acc) {
        const e = this.entry("account_register");
        return e && openReport(this.action, { ...e, options: { ...e.options, account_ids: [acc.id] } });
    }

    report(key) {
        const e = this.entry(key);
        return e && openReport(this.action, e);
    }

    printReport(key) {
        const e = this.entry(key);
        return e && exportReport(this.orm, e, "pdf", true);
    }

    recentFor(category, defaults) {
        const out = [];
        const keys = new Set();
        for (const r of load("sonador_books.recent_reports", [])) {
            const e = this.entry(r.key);
            if (e && e.category === category && e.priority === 1 && !keys.has(e.key)) {
                out.push(e);
                keys.add(e.key);
            }
        }
        for (const k of defaults || []) {
            const e = this.entry(k);
            if (e && e.priority === 1 && !keys.has(k)) {
                out.push(e);
                keys.add(k);
            }
        }
        if (!out.length) {
            return this.state.catalog.filter((e) => e.category === category).slice(0, 5);
        }
        return out.slice(0, 5);
    }

    // ------------------------------------------------------------------ shortcuts
    get shortcuts() {
        return this.state.shortcuts.map((k) => SHORTCUTS.find((s) => s.key === k)).filter((s) => s && this.allowed(s));
    }

    // a screen the user's menus do not offer (a clerk's Journal Voucher) is not offered here either
    allowed(tile) {
        const m = this.state.nav && this.state.nav.menu_actions;
        if (!m || typeof tile.action !== "string") {
            return true;
        }
        return !m.known.includes(tile.action) || m.visible.includes(tile.action);
    }

    toggleShortcut(key) {
        const set = new Set(this.state.shortcuts);
        set.has(key) ? set.delete(key) : set.add(key);
        this.state.shortcuts = SHORTCUTS.map((s) => s.key).filter((k) => set.has(k));
        save("sonador_books.shortcuts", this.state.shortcuts);
    }

    // ------------------------------------------------------------------ formatting
    money(v, digits = 2) {
        return sbMoney(v || 0, digits);
    }

    pct(part, total) {
        return total ? ((part / total) * 100).toFixed(1) + "%" : "–";
    }

    date(d) {
        if (!d) {
            return "";
        }
        const [y, m, dd] = d.split("-");
        return new Date(+y, +m - 1, +dd).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
    }

    get trendSeries() {
        const t = this.state.data.trend || {};
        return [
            { label: "Revenue", data: t.revenue || [] },
            { label: "Gross profit", data: t.gross || [] },
            { label: "Net income", data: t.net || [] },
        ];
    }

    get hasTrend() {
        const t = this.state.data.trend;
        return t && [...(t.revenue || []), ...(t.net || [])].some((v) => Math.abs(v) > 0.005);
    }

    agingSeries(a) {
        return [{ label: "Amount", data: (a && a.values) || [] }];
    }

    hasAging(a) {
        return a && (a.values || []).some((v) => Math.abs(v) > 0.005);
    }
}

registry.category("actions").add("sonador_books.nav_center", NavCenter);
