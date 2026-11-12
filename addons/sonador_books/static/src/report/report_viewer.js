import { Component, onWillStart, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { router } from "@web/core/browser/router";
import { download } from "@web/core/network/download";
import { browser } from "@web/core/browser/browser";
import { SbChart, sbMoney } from "../common/chart";
import { SbPeriodPicker } from "../common/period_picker";
import { openDrawer } from "../common/drawer";

const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const REPORT_TITLES = {
    trial_balance: "Trial Balance",
    general_ledger: "General Ledger",
    profit_loss: "Income Statement",
    balance_sheet: "Statement of Financial Position",
    aged: "Aged Balances",
    partner_ledger: "Customer / Vendor Ledger",
    cash_book: "Cash and Bank Book",
};

// which filters each report shows
export const FEATURES = {
    trial_balance: { comparison: true, noMonths: true, asOf: false, analytic: true, preClosing: true },
    general_ledger: { comparison: false, asOf: false, analytic: true, voided: true, preClosing: true, pickers: true },
    cash_book: { comparison: false, asOf: false, voided: true },
    profit_loss: { comparison: true, asOf: false, analytic: true },
    balance_sheet: { comparison: true, asOf: true, analytic: true },
    cash_flow: { comparison: true, noMonths: true },
    equity_changes: { comparison: true, noMonths: true },
    retained: { comparison: true, noMonths: true },
    notes_pack: { asOf: true, comparison: true, noMonths: true },
    aged: { comparison: false, asOf: true, partnerType: true, buckets: true },
    partner_ledger: { comparison: false, asOf: false, partnerType: true },
    partner_statement: { partnerType: true },
    partner_list: { asOf: true, partnerType: true },
    open_credits: { asOf: true, partnerType: true },
    cash_requirements: { asOf: true },
    chart: { asOf: true },
    partner_sales: { partnerType: true },
};

const MODES = [
    ["month", "Month"],
    ["quarter", "Quarter"],
    ["fy", "Fiscal year"],
    ["ytd", "Year to date"],
    ["range", "Period range"],
    ["custom", "Dates"],
];

function storageKey(report) {
    return `sonador_books.report.${report}`;
}

// filters a drill-down or a one-off pick sets: they belong to that view, never to the next time the report opens
// from the menu (they show as removable chips while they are on)
const TRANSIENT = ["move_ids", "account_ids", "partner_ids", "exclude_closing", "exclude_closing_since"];

function withoutTransient(options) {
    const out = { ...(options || {}) };
    for (const k of TRANSIENT) {
        delete out[k];
    }
    return out;
}

export class ReportViewer extends Component {
    static template = "sonador_books.ReportViewer";
    static props = ["*"];
    static components = { SbChart, SbPeriodPicker };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.root = useRef("root");
        this.searchInput = useRef("search");
        const ctx = this.props.ctx || (this.props.action && this.props.action.context) || {};
        this.ctx = ctx;
        this.reportKey = ctx.report || "trial_balance";
        this.catalogKey = ctx.catalog_key || this.reportKey;
        this.features = FEATURES[this.reportKey] || {};
        this.modes = MODES;
        this.state = useState({
            loading: true,
            periods: null,
            doc: null,
            expanded: new Set(),
            selected: -1,
            views: [],
            showViews: false,
            showJournals: false,
            showColumns: false,
            showFilters: false,
            rowMenu: null,
            view: "both",
            journals: [],
            analytics: [],
            plans: [],
            options: {},
            ui: { mode: "month", month: null, monthTo: null, quarter: null, fy: null },
            pick: { field: null, q: "", results: [] },
        });
        this._searchTimer = null;
        this._onKey = this.onKeydown.bind(this);

        onWillStart(async () => {
            const [periods, journals, views] = await Promise.all([
                this.orm.call("sb.report", "get_periods", []),
                this.orm.searchRead("account.journal", [], ["id", "name", "code", "type"]),
                this.orm.call("sb.report.view", "list_views", [this.reportKey]),
            ]);
            this.state.periods = periods;
            this.state.journals = journals;
            if (this.features.analytic) {
                try {
                    // analytic accounts and plans for the filter; a user without analytic access simply has none
                    const [accounts, plans] = await Promise.all([
                        this.orm.searchRead("account.analytic.account", [], ["id", "name", "code", "plan_id"], { order: "plan_id, code, name" }),
                        this.orm.searchRead("account.analytic.plan", [["parent_id", "=", false]], ["id", "name"]),
                    ]);
                    this.state.analytics = accounts;
                    this.state.plans = plans;
                } catch {
                    this.state.analytics = [];
                    this.state.plans = [];
                }
            }
            this.state.views = views;
            this.initOptions(ctx);
            await this.load();
            const lazy = this.state.doc.rows.filter((r) => r.lazy);
            if (ctx.expand_first && lazy.length === 1) {
                await this.toggle(lazy[0]);
            }
        });
        onMounted(() => {
            this._mounted = true;
            document.addEventListener("keydown", this._onKey);
            this.root.el && this.root.el.focus();
        });
        onWillUnmount(() => document.removeEventListener("keydown", this._onKey));
    }

    // ------------------------------------------------------------------ options
    initOptions(ctx) {
        const p = this.state.periods;
        const cur = p.current_period || p.years[p.years.length - 2].months[0];
        let stored = {};
        try {
            stored = JSON.parse(browser.localStorage.getItem(storageKey(this.catalogKey)) || "{}");
        } catch {
            stored = {};
        }
        let fromUrl = {};
        try {
            fromUrl = router.current.sb ? JSON.parse(router.current.sb) : {};
        } catch {
            fromUrl = {};
        }
        const base = {
            posted_only: true,
            hide_zero: true,
            comparison: "none",
            comparison_count: 1,
            journal_ids: [],
            analytic_ids: [],
            analytic_plan_ids: [],
            search: "",
            partner_type: "customer",
            buckets: [30, 60, 90, 120],
        };
        const drill = { ...(ctx.options || {}), ...((this.props.saved && this.props.saved.options) || {}) };
        // what an older version stored with a drill's account or partner in it is dropped here too
        const options = Object.assign(base, withoutTransient(stored.options), fromUrl.options || {}, drill);
        const ui = Object.assign(
            { mode: "month", month: cur.id, monthTo: cur.id, quarter: null, fy: p.current_fy },
            stored.ui || {},
            fromUrl.ui || {},
            ctx.ui || {},
            (this.props.saved && this.props.saved.ui) || {}
        );
        if ((drill.date_from !== undefined || drill.date_to !== undefined) && !(this.props.saved && this.props.saved.ui)) {
            ui.mode = "custom";
            const fy = p.years.find((y) => y.fy === p.current_fy);
            options.date_from = drill.date_from || (drill.date_from === null ? "1900-01-01" : fy.date_from);
            options.date_to = drill.date_to || p.today;
        }
        this.state.options = options;
        this.state.ui = ui;
        this.state.view = (stored.view || "both");
        if (ui.mode !== "custom") {
            this.applyMode(false);
        }
    }

    get allMonths() {
        return this.state.periods.years.flatMap((y) => y.months);
    }
    get allQuarters() {
        return this.state.periods.years.flatMap((y) => y.quarters);
    }

    applyMode(reload = true) {
        const { ui, options, periods } = this.state;
        const find = (id) => this.allMonths.find((m) => m.id === id);
        const year = periods.years.find((y) => y.fy === Number(ui.fy)) || periods.years.find((y) => y.fy === periods.current_fy);
        if (ui.mode === "month") {
            const m = find(ui.month) || periods.current_period;
            options.date_from = m.date_from;
            options.date_to = m.date_to;
        } else if (ui.mode === "quarter") {
            const q = this.allQuarters.find((x) => x.id === ui.quarter) || year.quarters[0];
            ui.quarter = q.id;
            options.date_from = q.date_from;
            options.date_to = q.date_to;
        } else if (ui.mode === "fy") {
            options.date_from = year.date_from;
            options.date_to = year.date_to;
        } else if (ui.mode === "ytd") {
            const fy = periods.years.find((y) => y.fy === periods.current_fy);
            options.date_from = fy.date_from;
            options.date_to = periods.today;
        } else if (ui.mode === "range") {
            const a = find(ui.month) || periods.current_period;
            const b = find(ui.monthTo) || a;
            options.date_from = a.date_from < b.date_from ? a.date_from : b.date_from;
            options.date_to = a.date_to > b.date_to ? a.date_to : b.date_to;
        }
        // an "as at" report (who owes what, the statement of financial position) is as at today, never the end of a
        // month that has not ended yet (that would take in entries dated in the future)
        const asAt = String(this.catalogKey || "").startsWith("aged") || this.reportKey === "balance_sheet";
        if (asAt && options.date_to > periods.today) {
            options.date_to = periods.today;
        }
        if (reload) {
            this.load();
        }
    }

    setMode(mode) {
        this.state.ui.mode = mode;
        if (mode === "custom") {
            return;
        }
        this.applyMode();
    }

    onUi(field, ev) {
        this.state.ui[field] = ev.target.value;
        this.applyMode();
    }

    onOption(field, ev) {
        const t = ev.target;
        let v = t.type === "checkbox" ? t.checked : t.value;
        if (field === "comparison_count") {
            v = Math.max(1, Math.min(24, parseInt(v) || 1));
        }
        this.state.options[field] = v;
        this.load();
    }

    onDate(field, ev) {
        this.state.options[field] = ev.target.value;
        this.load();
    }

    onSearch(ev) {
        this.state.options.search = ev.target.value;
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => this.load(), 300);
    }

    toggleJournal(id) {
        const ids = new Set(this.state.options.journal_ids || []);
        ids.has(id) ? ids.delete(id) : ids.add(id);
        this.state.options.journal_ids = [...ids];
        this.load();
    }

    toggleIn(field, id) {
        const ids = new Set(this.state.options[field] || []);
        ids.has(id) ? ids.delete(id) : ids.add(id);
        this.state.options[field] = [...ids];
        this.load();
    }

    get filtersOn() {
        const o = this.state.options;
        return !!((o.journal_ids || []).length || !o.posted_only || (o.analytic_ids || []).length ||
            (o.analytic_plan_ids || []).length || o.hide_voided || (o.move_ids || []).length ||
            (o.account_ids || []).length || (o.partner_ids || []).length || o.exclude_closing || o.exclude_closing_since);
    }

    get chips() {
        return (this.state.doc && this.state.doc.chips) || [];
    }

    removeChip(chip) {
        const o = this.state.options;
        if (chip.id === undefined) {
            o[chip.key] = Array.isArray(o[chip.key]) ? [] : false;
        } else {
            o[chip.key] = (o[chip.key] || []).filter((x) => x !== chip.id);
        }
        this.load();
    }

    // ------------------------------------------------------------------ account / customer-vendor pickers
    async onPick(field, ev) {
        const q = ev.target.value;
        this.state.pick = { field, q, results: [] };
        if (!q.trim()) {
            return;
        }
        const model = field === "account_ids" ? "account.account" : "res.partner";
        const domain = field === "account_ids" ? ["|", ["code", "=ilike", `${q.trim()}%`], ["name", "ilike", q.trim()]]
            : [["name", "ilike", q.trim()]];
        const results = await this.orm.searchRead(model, domain, ["id", "display_name"], { limit: 8 });
        if (this.state.pick.q === q) {
            this.state.pick.results = results;
        }
    }

    addPick(field, rec) {
        const ids = new Set(this.state.options[field] || []);
        ids.add(rec.id);
        this.state.options[field] = [...ids];
        this.state.pick = { field: null, q: "", results: [] };
        this.load();
    }

    setPartnerType(t) {
        this.state.options.partner_type = t;
        this.load();
    }

    onBuckets(ev) {
        const b = ev.target.value
            .split(/[ ,]+/)
            .map((x) => parseInt(x))
            .filter((x) => x > 0)
            .sort((a, b) => a - b);
        if (b.length) {
            this.state.options.buckets = b;
            this.load();
        }
    }

    persist() {
        // entries, accounts and partners picked by a drill-down belong to that one view, not to the next time the
        // report opens
        const kept = withoutTransient(this.state.options);
        const payload = { options: { ...kept, search: "" }, ui: this.state.ui, view: this.state.view };
        try {
            browser.localStorage.setItem(storageKey(this.catalogKey), JSON.stringify(payload));
        } catch {
            // private mode: no memory, still works
        }
        if (this.props.onState && this._mounted) {
            this.props.onState({ options: { ...this.state.options }, ui: { ...this.state.ui }, view: this.state.view });
        }
    }

    onPeriod(dateFrom, dateTo) {
        this.state.ui.mode = "custom";
        this.state.options.date_from = dateFrom;
        this.state.options.date_to = dateTo;
        this.load();
    }

    setView(v) {
        this.state.view = v;
        this.persist();
    }

    rememberRecent() {
        try {
            const key = "sonador_books.recent_reports";
            const list = JSON.parse(browser.localStorage.getItem(key) || "[]").filter((r) => r.key !== this.catalogKey);
            const ctx = this.ctx || {};
            list.unshift({ key: this.catalogKey, report: this.reportKey, name: (this.state.doc && this.state.doc.title) || this.reportKey,
                options: ctx.options || {}, ui: ctx.ui || {}, catalog_key: ctx.catalog_key || false, at: Date.now() });
            browser.localStorage.setItem(key, JSON.stringify(list.slice(0, 12)));
        } catch {
            // ignore
        }
    }

    // ------------------------------------------------------------------ data
    async load() {
        this.state.loading = true;
        const prevExpanded = this.state.expanded;
        try {
            const doc = await this.orm.call("sb.report", "get_report", [this.reportKey, this.state.options]);
            const expanded = new Set();
            for (const r of doc.rows) {
                if (r.kind === "section" && !r.lazy) {
                    expanded.add(r.id);
                }
                if (prevExpanded.has(r.id) && !r.lazy) {
                    expanded.add(r.id);
                }
            }
            this.state.doc = doc;
            if (!this._remembered) {
                this._remembered = true;
                this.rememberRecent();
            }
            this.state.expanded = expanded;
            this.state.selected = -1;
            // re-open lazily loaded groups that were open
            for (const r of doc.rows) {
                if (r.lazy && prevExpanded.has(r.id)) {
                    await this.toggle(r);
                }
            }
            this.persist();
        } finally {
            this.state.loading = false;
        }
    }

    get parentMap() {
        const m = {};
        for (const r of this.state.doc.rows) {
            m[r.id] = r.parent;
        }
        return m;
    }

    get visibleRows() {
        if (!this.state.doc) {
            return [];
        }
        const parents = this.parentMap;
        const exp = this.state.expanded;
        return this.state.doc.rows.filter((r) => {
            let p = r.parent;
            let depth = 0;
            while (p && depth++ < 20) {
                if (!exp.has(p)) {
                    return false;
                }
                p = parents[p] === p ? null : parents[p];
            }
            return true;
        });
    }

    hasChildren(row) {
        return row.expandable || row.lazy;
    }

    async toggle(row) {
        if (!this.hasChildren(row)) {
            return;
        }
        const exp = new Set(this.state.expanded);
        if (exp.has(row.id)) {
            exp.delete(row.id);
            this.state.expanded = exp;
            return;
        }
        if (row.lazy && !row.loaded) {
            const children = await this.orm.call("sb.report", "get_children", [row.lazy, this.state.options]);
            const rows = this.state.doc.rows;
            const idx = rows.findIndex((r) => r.id === row.id);
            for (const c of children) {
                c.parent = row.id;
            }
            rows.splice(idx + 1, 0, ...children);
            row.loaded = true;
        }
        exp.add(row.id);
        this.state.expanded = exp;
    }

    expandAll() {
        const exp = new Set();
        for (const r of this.state.doc.rows) {
            if (r.expandable && !r.lazy) {
                exp.add(r.id);
            }
        }
        this.state.expanded = exp;
    }

    collapseAll() {
        this.state.expanded = new Set();
    }

    // ------------------------------------------------------------------ drill-down
    cellDrill(row, col) {
        // a statement with comparatives drills each column to its own period's detail
        return row.drills && col ? row.drills[col.key] || null : row.drill;
    }

    async activate(row, ev, col = null) {
        if (col && row.drills) {
            row = { ...row, drill: this.cellDrill(row, col), open: null };
            if (!row.drill) {
                return;
            }
        }
        if (row.open) {
            // records open in a slide-over: closing it leaves the report exactly as it was
            return openDrawer(this.dialog, { resModel: row.open.model, resId: row.open.id, title: row.label || "",
                onClose: () => this.props.onRecordClosed && this.props.onRecordClosed() });
        }
        if (row.drill) {
            const { report: _r, ...extra } = row.drill;
            const d = row.drill;
            const options = {
                ...this.state.options,
                ...extra,
                search: "",
                account_ids: d.account_ids || [],
                partner_ids: d.partner_ids || [],
                move_ids: d.move_ids || [],
                partner_type: d.partner_type || this.state.options.partner_type,
                comparison: "none",
                date_from: d.date_from === undefined ? this.state.options.date_from : d.date_from,
                date_to: d.date_to || this.state.options.date_to,
            };
            const ctx = { report: d.report, options, ui: { mode: "custom" }, expand_first: true };
            const title = REPORT_TITLES[d.report] || d.report;
            const background = !!(ev && (ev.ctrlKey || ev.metaKey || ev.button === 1));
            if (this.props.onOpenReport) {
                return this.props.onOpenReport({ ctx, title, crumb: row.label || title, background });
            }
            return this.action.doAction({ type: "ir.actions.client", tag: "sonador_books.report", name: title, context: ctx });
        }
        return this.toggle(row);
    }

    toggleRowMenu(row, ev) {
        ev.stopPropagation();
        this.state.rowMenu = this.state.rowMenu === row.id ? null : row.id;
    }

    rowActions(row) {
        const acts = [];
        if (row.drill) {
            acts.push(["drill", row.drill.report === "general_ledger" ? "Open the ledger for this line" : "Open the detail report"]);
        }
        if (row.open) {
            acts.push(["open", row.open.model === "sb.voucher" ? "Open the voucher" : "Open the record"]);
        }
        if (this.hasChildren(row)) {
            acts.push(["toggle", this.state.expanded.has(row.id) ? "Collapse" : "Expand"]);
        }
        acts.push(["copy", "Copy the amounts"]);
        return acts;
    }

    async rowAction(row, act) {
        this.state.rowMenu = null;
        if (act === "drill") {
            return this.activate({ ...row, open: null });
        }
        if (act === "open") {
            return this.activate({ ...row, drill: null });
        }
        if (act === "toggle") {
            return this.toggle(row);
        }
        if (act === "copy") {
            const cols = this.shownColumns;
            const text = cols.map((c, i) => this.cell(row, c, i)).join("\t");
            try {
                await navigator.clipboard.writeText(text);
                this.notification.add("Copied", { type: "success" });
            } catch {
                // clipboard blocked
            }
        }
    }

    // KPI formatting
    kpiValue(card) {
        if (card.kind === "int") {
            return (card.value || 0).toLocaleString("en-US");
        }
        if (card.kind === "percent") {
            return card.value === null || card.value === undefined ? "–" : `${card.value.toFixed(1)}%`;
        }
        if (card.kind === "ratio") {
            return card.value === null || card.value === undefined ? "–" : card.value.toFixed(2);
        }
        if (card.kind === "badge" || typeof card.value === "string") {
            return card.value;
        }
        return sbMoney(card.value, 2);
    }

    get hasChart() {
        const c = this.state.doc && this.state.doc.chart;
        // nothing to plot (no movement in the period): no empty chart
        return Boolean(c && this.state.doc.rows.length > 1 &&
            (c.series || []).some((x) => (x.data || []).some((v) => Math.abs(Number(v) || 0) > 0.005)));
    }

    get emptyText() {
        return "Nothing to show for this period.";
    }

    tryPeriod(key) {
        const p = this.state.periods;
        const fy = p.years.find((y) => y.fy === p.current_fy);
        const last = p.years.find((y) => y.fy === p.current_fy - 1);
        if (key === "fy") {
            this.onPeriod(fy.date_from, fy.date_to);
        } else if (key === "last_fy" && last) {
            this.onPeriod(last.date_from, last.date_to);
        }
    }

    onRowClick(row, index) {
        this.state.selected = index;
        if (this.hasChildren(row) && !row.drill) {
            this.toggle(row);
        }
    }

    onKeydown(ev) {
        if (!this.root.el || !document.body.contains(this.root.el) || this.props.active === false) {
            return;
        }
        if (document.querySelector(".modal") || (this.props.isListFocused && this.props.isListFocused())) {
            return;
        }
        const tag = (ev.target.tagName || "").toLowerCase();
        const typing = ["input", "select", "textarea"].includes(tag);
        if (ev.key === "/" && !typing) {
            ev.preventDefault();
            this.searchInput.el && this.searchInput.el.focus();
            return;
        }
        if (typing) {
            if (ev.key === "Escape") {
                ev.target.blur();
            }
            return;
        }
        const rows = this.visibleRows;
        if (ev.key === "ArrowDown") {
            ev.preventDefault();
            this.state.selected = Math.min(rows.length - 1, this.state.selected + 1);
            this.scrollSelected();
        } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            this.state.selected = Math.max(0, this.state.selected - 1);
            this.scrollSelected();
        } else if (ev.key === "Enter" && rows[this.state.selected]) {
            ev.preventDefault();
            this.activate(rows[this.state.selected]);
        } else if (ev.key === "ArrowRight" && rows[this.state.selected]) {
            const r = rows[this.state.selected];
            if (!this.state.expanded.has(r.id)) {
                this.toggle(r);
            }
        } else if (ev.key === "ArrowLeft" && rows[this.state.selected]) {
            const r = rows[this.state.selected];
            if (this.state.expanded.has(r.id)) {
                this.toggle(r);
            }
        } else if (ev.key === "Escape" && this.props.onBack) {
            this.props.onBack();
        }
    }

    scrollSelected() {
        setTimeout(() => {
            const el = this.root.el && this.root.el.querySelector(".o_sb_rep_selected");
            el && el.scrollIntoView({ block: "nearest" });
        });
    }

    // ------------------------------------------------------------------ export / views
    async export(fmt, inline = false) {
        const data = {
            report: this.reportKey,
            fmt,
            options: JSON.stringify(this.state.options),
            expanded: JSON.stringify([...this.state.expanded]),
        };
        if (inline) {
            // open the PDF in a new tab for printing
            const form = document.createElement("form");
            form.method = "POST";
            form.action = "/sonador_books/report/export";
            form.target = "_blank";
            for (const [k, v] of Object.entries({ ...data, inline: 1, csrf_token: odoo.csrf_token })) {
                const i = document.createElement("input");
                i.type = "hidden";
                i.name = k;
                i.value = v;
                form.appendChild(i);
            }
            document.body.appendChild(form);
            form.submit();
            form.remove();
            return;
        }
        await download({ url: "/sonador_books/report/export", data });
    }

    get shownColumns() {
        const hidden = new Set(this.state.options.hidden_cols || []);
        return this.state.doc.columns.filter((c, i) => i === 0 || !hidden.has(c.key));
    }

    toggleColumn(key) {
        const hidden = new Set(this.state.options.hidden_cols || []);
        hidden.has(key) ? hidden.delete(key) : hidden.add(key);
        this.state.options.hidden_cols = [...hidden];
        this.persist();
    }

    async copyReport() {
        const name = prompt("Save this report, with its filters and columns, as:", (this.state.doc && this.state.doc.title) + " (copy)");
        if (!name) {
            return;
        }
        const options = { ...this.state.options, _title: name };
        await this.orm.call("sb.report.view", "save_view", [this.reportKey, name, { options, ui: this.state.ui }, false,
            this.ctx.catalog_key || false]);
        this.state.views = await this.orm.call("sb.report.view", "list_views", [this.reportKey]);
        this.notification.add(`"${name}" now appears at the top of its category in the Report Center.`, { type: "success" });
    }

    async saveView() {
        const name = prompt("Name this view (e.g. My monthly TB):");
        if (!name) {
            return;
        }
        await this.orm.call("sb.report.view", "save_view", [this.reportKey, name, { options: this.state.options, ui: this.state.ui }, false,
            this.ctx.catalog_key || false]);
        this.state.views = await this.orm.call("sb.report.view", "list_views", [this.reportKey]);
        this.notification.add(`Saved view "${name}"`, { type: "success" });
    }

    applyView(view) {
        const v = view.options || {};
        Object.assign(this.state.options, v.options || {});
        Object.assign(this.state.ui, v.ui || {});
        this.state.showViews = false;
        if (this.state.ui.mode !== "custom") {
            this.applyMode(false);
        }
        this.load();
    }

    async removeView(view) {
        await this.orm.call("sb.report.view", "remove_view", [[view.id]]);
        this.state.views = await this.orm.call("sb.report.view", "list_views", [this.reportKey]);
    }

    // ------------------------------------------------------------------ formatting
    isNum(col) {
        return ["monetary", "number", "percent", "number_int"].includes(col.type);
    }

    isNegative(row, col) {
        const v = row.values ? row.values[col.key] : undefined;
        return (col.type === "monetary" || col.type === "number") && typeof v === "number" && v < -0.005;
    }

    cell(row, col, colIndex) {
        let v = row.values ? row.values[col.key] : undefined;
        if (colIndex === 0 && (col.key === "label" || v === undefined || v === "" || v === null)) {
            v = row.label;
        }
        if (v === undefined || v === null || v === "") {
            return "";
        }
        if (col.type === "monetary" || col.type === "number") {
            if (Math.abs(v) < 0.005) {
                return "-";
            }
            const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            return v < 0 ? `(${s})` : s;
        }
        if (col.type === "percent") {
            return `${v.toFixed(1)}%`;
        }
        if (col.type === "number_int") {
            return v ? Math.round(v).toLocaleString("en-US") : "";
        }
        if (col.type === "date" && typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) {
            // the house format, as in the report's heading: 30 Oct 2026
            const [y, m, d] = v.split("-");
            return `${d} ${MONTHS_SHORT[Number(m) - 1]} ${y}`;
        }
        return v;
    }

    rowClass(row, index) {
        const c = [`o_sb_rep_${row.kind}`, `o_sb_rep_l${Math.min(row.level || 0, 4)}`];
        if (index === this.state.selected) {
            c.push("o_sb_rep_selected");
        }
        if (row.warning) {
            c.push("text-danger");
        }
        if (row.drill || row.open) {
            c.push("o_sb_rep_link");
        }
        return c.join(" ");
    }
}

// the "sonador_books.report" client action is served by the Report Workspace (report_center.js)
