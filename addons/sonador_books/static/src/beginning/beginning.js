import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export function fmt(v, blankZero = false) {
    if (v === null || v === undefined || v === "") {
        return "";
    }
    let n = Number(v);
    if (Math.abs(n) < 0.005) {
        if (blankZero) {
            return "";
        }
        n = 0; // no "-0.00"
    }
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtQty(v) {
    const n = Number(v || 0);
    if (!n) {
        return "";
    }
    return n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 4 });
}

export function parse(s) {
    if (typeof s === "number") {
        return s;
    }
    let t = String(s || "").replace(/,/g, "").trim();
    let neg = false;
    if (/^\(.*\)$/.test(t)) {
        neg = true;
        t = t.slice(1, -1);
    }
    const n = parseFloat(t);
    if (isNaN(n)) {
        return 0;
    }
    return neg ? -n : n;
}

const round2 = (n) => Math.round(n * 100) / 100;

/**
 * Beginning Balances (Peachtree: Account / Inventory / Customer / Vendor Beginning Balances) on one screen.
 * Receivable and payable accounts are fed by the customer and vendor items, inventory accounts by the items.
 */
export class BeginningBalances extends Component {
    static template = "sonador_books.BeginningBalances";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null,
            tab: p.tab || "accounts",
            period: null,
            busy: false,
            // chart of accounts
            accounts: [],
            acctDirty: {},
            acctSearch: "",
            acctFilter: "all",
            focusAccount: p.account_id || null,
            // inventory
            items: [],
            itemInfo: {},
            itemDirty: {},
            acctVouchers: [],
            acctFilterSet: false,
            itemSearch: "",
            itemFilter: "all",
            focusItem: p.product_id || null,
            // customers / vendors
            partners: [],
            partnerInfo: {},
            partnerSearch: "",
            partnerFilter: "all",
            partnerId: p.partner_id || null,
            detail: null,
            rows: [],
            rowsDirty: false,
            pane: "items",
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.beginning", "bb_context", []);
            this.state.period = this.state.ctx.opening;
            await this.load();
        });
    }

    // ------------------------------------------------------------------ general
    get ctx() {
        return this.state.ctx;
    }
    get periodInfo() {
        return this.ctx.periods.find((x) => x.key === this.state.period) || this.ctx.periods[0];
    }
    get canEdit() {
        // a closed (locked) period cannot take beginning balances until it is reopened
        if (!this.ctx.can_edit) {
            return false;
        }
        if (this.state.tab === "customers" || this.state.tab === "vendors") {
            return !this.ctx.items_locked;
        }
        return !this.periodInfo.locked;
    }
    get kind() {
        return this.state.tab === "vendors" ? "vendor" : "customer";
    }
    get dirty() {
        const s = this.state;
        if (s.tab === "accounts") {
            return Object.keys(s.acctDirty).length > 0;
        }
        if (s.tab === "inventory") {
            return Object.keys(s.itemDirty).length > 0;
        }
        return s.rowsDirty;
    }
    fmt(v, z) {
        return fmt(v, z);
    }
    fmtQty(v) {
        return fmtQty(v);
    }

    async guard() {
        if (!this.dirty) {
            return true;
        }
        return new Promise((resolve) => {
            this.dialog.add(ConfirmationDialog, {
                title: "Unsaved changes",
                body: "You have changes that are not saved. Discard them?",
                confirmLabel: "Discard changes",
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            });
        });
    }

    async setTab(tab) {
        if (tab === this.state.tab || !(await this.guard())) {
            return;
        }
        this.clearDirty();
        this.state.tab = tab;
        await this.load();
    }

    async setPeriod(ev) {
        const key = ev.target.value;
        if (!(await this.guard())) {
            ev.target.value = this.state.period;
            return;
        }
        this.clearDirty();
        this.state.period = key;
        await this.load();
    }

    clearDirty() {
        this.state.acctDirty = {};
        this.state.itemDirty = {};
        this.state.rowsDirty = false;
    }

    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.tab === "accounts") {
                const res = await this.orm.call("sb.beginning", "bb_accounts", [s.period]);
                s.accounts = res.rows;
                s.acctVouchers = res.vouchers;
                if (!s.acctFilterSet) {
                    // with balances entered, show those first (the full chart is one click away)
                    s.acctFilter = res.rows.some((r) => Math.abs(r.amount) > 0.004) && !s.focusAccount ? "used" : "all";
                    s.acctFilterSet = true;
                }
                if (s.focusAccount) {
                    this.scrollTo(`acct-${s.focusAccount}`);
                }
            } else if (s.tab === "inventory") {
                const res = await this.orm.call("sb.beginning", "bb_items", [s.period]);
                s.items = res.rows;
                s.itemInfo = res;
                if (s.focusItem) {
                    this.scrollTo(`item-${s.focusItem}`);
                }
            } else {
                await this.loadPartners();
            }
        } finally {
            s.busy = false;
        }
    }

    scrollTo(id) {
        setTimeout(() => {
            const el = document.getElementById(id);
            if (el) {
                el.scrollIntoView({ block: "center" });
                const input = el.querySelector("input");
                if (input) {
                    input.focus();
                    input.select();
                }
            }
        }, 60);
    }

    onKeydown(ev) {
        // Enter / arrows move between rows in the same column, like the Peachtree grids
        const keys = { Enter: 1, ArrowDown: 1, ArrowUp: -1 };
        if (!(ev.key in keys) || ev.target.tagName !== "INPUT" || ev.target.type === "date") {
            return;
        }
        const col = ev.target.dataset.col;
        const table = ev.target.closest("table");
        if (!col || !table) {
            return;
        }
        const inputs = [...table.querySelectorAll(`input[data-col="${col}"]`)];
        const i = inputs.indexOf(ev.target);
        const next = inputs[i + (ev.shiftKey && ev.key === "Enter" ? -1 : keys[ev.key])];
        if (next) {
            ev.preventDefault();
            ev.target.dispatchEvent(new Event("change", { bubbles: true }));
            next.focus();
            next.select();
        }
    }

    selectAll(ev) {
        ev.target.select();
    }

    openVouchers(kind) {
        const domain = [["voucher_type", "=", "opening"]];
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: "Beginning balance entries",
            res_model: kind === "stock" ? "sb.stock.voucher" : "sb.voucher",
            views: [[false, "list"], [false, "form"]],
            domain: kind === "stock" ? domain : [...domain, ["sb_ob_kind", "!=", false]],
            context: kind === "stock" ? {} : { default_voucher_type: "opening" },
        });
    }

    // ------------------------------------------------------------------ chart of accounts
    // One opening balance (Sage: Chart of Accounts Beginning Balances): the trial balance on the day before the
    // books start, each balance in its debit or credit column. Saved only when debits equal credits.
    get accountRows() {
        const s = this.state;
        const q = s.acctSearch.trim().toLowerCase();
        return s.accounts.filter((r) => {
            if (q && !`${r.code} ${r.name}`.toLowerCase().includes(q)) {
                return false;
            }
            if (s.acctFilter === "used") {
                return Math.abs(r.amount) > 0.004 || s.acctDirty[r.id] !== undefined;
            }
            if (s.acctFilter === "bs") {
                return !r.pl;
            }
            if (s.acctFilter === "pl") {
                return r.pl;
            }
            return true;
        });
    }
    get acctTotals() {
        // the whole chart, filters aside: customers, vendors and items included
        let dr = 0;
        let cr = 0;
        for (const r of this.state.accounts) {
            if (r.amount > 0) {
                dr += r.amount;
            } else {
                cr -= r.amount;
            }
        }
        dr = round2(dr);
        cr = round2(cr);
        return { dr, cr, diff: round2(dr - cr) };
    }
    get acctBalanced() {
        return Math.abs(this.acctTotals.diff) < 0.005;
    }
    acctEditable(r) {
        return this.canEdit && !(r.source === "customer" || r.source === "vendor");
    }
    sideValue(r, side) {
        // side: "dr" | "cr"
        if (side === "dr") {
            return r.amount > 0.004 ? fmt(r.amount) : "";
        }
        return r.amount < -0.004 ? fmt(-r.amount) : "";
    }
    onAcctChange(r, side, ev) {
        const raw = String(ev.target.value || "").trim();
        let n = parse(raw);
        if (!raw && ((side === "dr" && r.amount < 0) || (side === "cr" && r.amount > 0))) {
            // clearing the empty column leaves the other one alone
            ev.target.value = "";
            return;
        }
        // a minus in a column flips it to the other side (a contra balance)
        const amount = round2(side === "dr" ? n : -n);
        if (Math.abs(amount - r.amount) < 0.005) {
            ev.target.value = this.sideValue(r, side);
            return;
        }
        r.amount = amount;
        this.state.acctDirty[r.id] = amount;
        ev.target.value = this.sideValue(r, side);
    }
    async saveAccounts() {
        const s = this.state;
        if (!this.acctBalanced) {
            this.notification.add("Debits must equal credits before the opening balance can be saved.", { type: "danger" });
            return;
        }
        s.busy = true;
        try {
            const res = await this.orm.call("sb.beginning", "bb_accounts_save", [s.period, s.acctDirty]);
            s.accounts = res.rows;
            s.acctVouchers = res.vouchers;
            s.acctDirty = {};
            this.notification.add("Opening balance saved and posted. Debits equal credits.", { type: "success" });
            await this.refreshCtx();
        } finally {
            s.busy = false;
        }
    }
    async refreshCtx() {
        const period = this.state.period;
        this.state.ctx = await this.orm.call("sb.beginning", "bb_context", []);
        this.state.period = period;
    }
    async discard() {
        this.clearDirty();
        await this.load();
    }
    async gotoSource(r) {
        if (r.source === "customer") {
            return this.setTab("customers");
        }
        if (r.source === "vendor") {
            return this.setTab("vendors");
        }
        if (r.source === "inventory") {
            return this.setTab("inventory");
        }
    }

    // ------------------------------------------------------------------ inventory
    get itemRows() {
        const s = this.state;
        const q = s.itemSearch.trim().toLowerCase();
        return s.items.filter((r) => {
            if (q && !`${r.code} ${r.name}`.toLowerCase().includes(q)) {
                return false;
            }
            if (s.itemFilter === "used") {
                return r.qty || r.value;
            }
            return true;
        });
    }
    get itemTotal() {
        return this.state.items.reduce((a, r) => a + (r.value || 0), 0);
    }
    onItem(r, field, ev) {
        const n = parse(ev.target.value);
        if (field === "qty") {
            r.qty = n;
            r.value = round2(n * (r.cost || 0));
        } else if (field === "cost") {
            r.cost = n;
            r.value = round2((r.qty || 0) * n);
        } else {
            r.value = round2(n);
            if (r.qty) {
                r.cost = n / r.qty;
            }
        }
        this.state.itemDirty[r.id] = { id: r.id, qty: r.qty, value: r.value };
    }
    async saveItems() {
        const s = this.state;
        s.busy = true;
        try {
            const res = await this.orm.call("sb.beginning", "bb_items_save", [s.period, Object.values(s.itemDirty)]);
            s.items = res.rows;
            s.itemInfo = res;
            s.itemDirty = {};
            this.notification.add("Inventory beginning balances saved and posted.", { type: "success" });
            await this.refreshCtx();
        } finally {
            s.busy = false;
        }
    }

    // ------------------------------------------------------------------ customers / vendors
    async loadPartners() {
        const s = this.state;
        const res = await this.orm.call("sb.beginning", "bb_partners", [this.kind]);
        s.partners = res.partners;
        s.partnerInfo = res;
        const keep = s.partners.find((p) => p.id === s.partnerId);
        const first = keep || s.partners.find((p) => p.amount) || s.partners[0];
        if (first) {
            await this.loadDetail(first.id);
        } else {
            s.detail = null;
            s.rows = [];
        }
    }
    get partnerRows() {
        const s = this.state;
        const q = s.partnerSearch.trim().toLowerCase();
        return s.partners.filter((p) => {
            if (q && !`${p.ref} ${p.name}`.toLowerCase().includes(q)) {
                return false;
            }
            if (s.partnerFilter === "used") {
                return Math.abs(p.amount) > 0.004;
            }
            return true;
        });
    }
    async pickPartner(p) {
        if (p.id === this.state.partnerId || !(await this.guard())) {
            return;
        }
        this.state.rowsDirty = false;
        await this.loadDetail(p.id);
        this.state.pane = "items";
    }
    async loadDetail(pid) {
        const s = this.state;
        const res = await this.orm.call("sb.beginning", "bb_partner_items", [this.kind, pid]);
        s.partnerId = pid;
        s.detail = res;
        s.rows = res.rows.map((r) => ({ ...r, key: `r${r.id}` }));
        this.ensureBlank();
    }
    ensureBlank() {
        const s = this.state;
        const last = s.rows[s.rows.length - 1];
        if (!last || last.number || last.amount) {
            s.rows.push({
                key: `n${Date.now()}${s.rows.length}`, id: false, number: "", date: s.detail.last_day, due: false,
                po: "", amount: 0, account_id: s.detail.default_account, paid: 0, period: "",
            });
        }
    }
    accountChoices() {
        const own = this.kind === "customer" ? this.ctx.receivable_accounts : this.ctx.payable_accounts;
        return [...own, ...this.ctx.partner_accounts.filter((a) => !own.find((o) => o.id === a.id))];
    }
    onRow(r, field, ev) {
        const v = ev.target.value;
        if (field === "amount") {
            r.amount = round2(parse(v));
            ev.target.value = fmt(r.amount, true);
        } else if (field === "account_id") {
            r.account_id = parseInt(v);
        } else {
            r[field] = v || false;
        }
        this.state.rowsDirty = true;
        this.ensureBlank();
    }
    removeRow(r) {
        const s = this.state;
        s.rows.splice(s.rows.indexOf(r), 1);
        s.rowsDirty = true;
        this.ensureBlank();
    }
    get detailTotal() {
        return this.state.rows.reduce((a, r) => a + (r.amount || 0), 0);
    }
    get detailCount() {
        return this.state.rows.filter((r) => r.number || r.amount).length;
    }
    async saveRows() {
        const s = this.state;
        s.busy = true;
        try {
            const rows = s.rows.filter((r) => r.id || r.number || r.amount).map((r) => ({
                id: r.id, number: r.number, date: r.date, due: r.due, po: r.po, amount: r.amount, account_id: r.account_id,
            }));
            await this.orm.call("sb.beginning", "bb_partner_save", [this.kind, s.partnerId, rows]);
            s.rowsDirty = false;
            await this.refreshCtx();
            this.notification.add("Beginning balances saved and posted.", { type: "success" });
            const pid = s.partnerId;
            const res = await this.orm.call("sb.beginning", "bb_partners", [this.kind]);
            s.partners = res.partners;
            s.partnerInfo = res;
            await this.loadDetail(pid);
        } finally {
            s.busy = false;
        }
    }
    async discardRows() {
        this.state.rowsDirty = false;
        await this.loadDetail(this.state.partnerId);
    }
    openPartner() {
        return this.action.doAction({
            type: "ir.actions.act_window", res_model: "res.partner", res_id: this.state.partnerId,
            views: [[false, "form"]], target: "current",
        });
    }
}
registry.category("actions").add("sonador_books.beginning_balances", BeginningBalances);
