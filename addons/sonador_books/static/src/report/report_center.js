import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { ReportViewer } from "./report_viewer";
import { reportContext, exportReport } from "../common/report_links";
import { openDrawer } from "../common/drawer";

const MAX_TABS = 12;

const ICONS = {
    ar: "fa-users", ap: "fa-truck", payroll: "fa-id-badge", gl: "fa-book", fs: "fa-line-chart", tax: "fa-university",
    inventory: "fa-cubes", jobs: "fa-briefcase", time: "fa-clock-o", bank: "fa-bank", assets: "fa-building",
    company: "fa-shield", groups: "fa-files-o",
};

/**
 * Report Workspace: the report list stays on the left, reports open as tabs on the right,
 * drill-downs open new tabs, records open in a drawer. Nothing ever needs the browser Back button.
 */
export class ReportWorkspace extends Component {
    static template = "sonador_books.ReportWorkspace";
    static props = ["*"];
    static components = { ReportViewer };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.root = useRef("root");
        this.searchRef = useRef("search");
        this.listRef = useRef("list");
        this.storeKey = `sonador_books.workspace.${user.userId}`;
        const saved = this.read(this.storeKey, {});
        this.state = useState({
            categories: [],
            reports: [],
            category: saved.category || "fs",
            tab: "reports",
            search: "",
            selected: saved.selected || null,
            recent: this.read("sonador_books.recent_reports", []),
            favs: this.read("sonador_books.fav_reports", []),
            collapsed: !!saved.collapsed,
            tabs: (saved.tabs || []).slice(0, MAX_TABS),
            active: saved.active || "home",
            history: [],
            listFocused: false,
        });
        if (!this.state.tabs.find((t) => t.id === this.state.active)) {
            this.state.active = "home";
        }
        this._seq = Date.now();
        // per-tab filters live outside reactive state: saving them must not re-render the workspace
        this.savedStates = Object.fromEntries((saved.tabs || []).map((t) => [t.id, t.saved || null]));
        this._onKey = this.onKeydown.bind(this);
        onWillStart(async () => {
            const cat = await this.orm.call("sb.report", "get_catalog", []);
            this.state.categories = cat.categories;
            this.state.reports = cat.reports.filter((r) => r.priority === 1);  // what is not available yet is not listed
            const ctx = (this.props.action && this.props.action.context) || {};
            if (ctx.report) {
                const entry = ctx.catalog_key && this.state.reports.find((r) => r.key === ctx.catalog_key);
                this.openTab({
                    ctx,
                    title: (ctx.options && ctx.options._title) || (entry && entry.name) || this.props.action.name || "Report",
                    category: entry ? entry.category : null,
                    catalogKey: ctx.catalog_key || null,
                    reuse: true,
                });
            }
        });
        onMounted(() => document.addEventListener("keydown", this._onKey));
        onWillUnmount(() => document.removeEventListener("keydown", this._onKey));
    }

    read(key, fallback) {
        try {
            const v = browser.localStorage.getItem(key);
            return v ? JSON.parse(v) : fallback;
        } catch {
            return fallback;
        }
    }

    save() {
        try {
            browser.localStorage.setItem(this.storeKey, JSON.stringify({
                category: this.state.category, selected: this.state.selected, collapsed: this.state.collapsed,
                tabs: this.state.tabs.map((t) => ({ ...t, saved: this.savedStates[t.id] || null })), active: this.state.active,
            }));
        } catch {
            // private mode
        }
    }

    // ------------------------------------------------------------------ catalog / list
    icon(cat) {
        return ICONS[cat] || "fa-file-text-o";
    }

    categoryName(key) {
        const c = this.state.categories.find((c) => c.key === key);
        return c ? c.name : "";
    }

    get currentCategory() {
        return this.state.categories.find((c) => c.key === this.state.category) || this.state.categories[0] || {};
    }

    listFor(cat) {
        const forms = this.state.tab === "forms";
        return this.state.reports.filter((r) => r.category === cat && !!r.form === forms);
    }

    get searchResults() {
        const s = this.state.search.trim().toLowerCase();
        if (!s) {
            return [];
        }
        return this.state.reports.filter((r) => (r.name + " " + r.description).toLowerCase().includes(s));
    }

    /** The flat list the keyboard moves through. */
    get keyboardList() {
        if (this.state.search.trim()) {
            return this.searchResults;
        }
        return this.listFor(this.state.category);
    }

    get favReports() {
        return this.state.reports.filter((r) => this.state.favs.includes(r.key) && r.priority === 1);
    }

    get recentReports() {
        const byKey = Object.fromEntries(this.state.reports.map((r) => [r.key, r]));
        return this.read("sonador_books.recent_reports", []).map((x) => byKey[x.key]).filter((r) => r && r.priority === 1).slice(0, 6);
    }

    get selectedReport() {
        return this.state.reports.find((r) => r.key === this.state.selected);
    }

    count(cat) {
        return this.state.reports.filter((r) => r.category === cat.key && r.priority === 1 && !!r.form === (this.state.tab === "forms")).length;
    }

    selectCategory(key) {
        this.state.category = this.state.category === key && this.state.active === "home" ? key : key;
        this.state.search = "";
        this.goHome();
        this.save();
    }

    select(r) {
        this.state.selected = r.key;
        this.save();
    }

    onSearch(ev) {
        this.state.search = ev.target.value;
        const first = this.keyboardList.find((r) => r.priority === 1);
        this.state.selected = first ? first.key : this.state.selected;
    }

    isFav(r) {
        return this.state.favs.includes(r.key);
    }

    toggleFav(r, ev) {
        ev && ev.stopPropagation();
        const favs = new Set(this.state.favs);
        favs.has(r.key) ? favs.delete(r.key) : favs.add(r.key);
        this.state.favs = [...favs];
        try {
            browser.localStorage.setItem("sonador_books.fav_reports", JSON.stringify(this.state.favs));
        } catch {
            // ignore
        }
    }

    toggleCollapsed() {
        this.state.collapsed = !this.state.collapsed;
        this.save();
    }

    // ------------------------------------------------------------------ tabs
    get activeTab() {
        return this.state.tabs.find((t) => t.id === this.state.active);
    }

    activate(id) {
        if (id === this.state.active) {
            return;
        }
        this.state.history.push(this.state.active);
        this.state.active = id;
        this.save();
    }

    goHome() {
        this.activate("home");
    }

    openEntry(r, ev) {
        if (!r) {
            return;
        }
        this.select(r);
        if (r.priority !== 1) {
            this.notification.add(`"${r.name}" is coming in a later release.`, { type: "info" });
            return;
        }
        if (r.pack_id) {
            return openDrawer(this.dialog, { resModel: "sb.report.pack", resId: r.pack_id, title: r.name });
        }
        const background = !!(ev && (ev.ctrlKey || ev.metaKey || ev.button === 1));
        this.openTab({ ctx: reportContext(r), title: r.name, category: r.category, catalogKey: r.key, reuse: true, background });
    }

    openTab({ ctx, title, category, catalogKey, parent, crumb, reuse, background }) {
        if (reuse) {
            const same = JSON.stringify(ctx.options || {});
            const existing = this.state.tabs.find((t) => !t.parent && (catalogKey ? t.catalogKey === catalogKey
                : t.ctx.report === ctx.report && JSON.stringify(t.ctx.options || {}) === same));
            if (existing) {
                if (!background) {
                    this.activate(existing.id);
                }
                return existing;
            }
        }
        const crumbs = parent
            ? [...parent.crumbs, { label: crumb || title }]
            : [{ label: "Report Center", home: true },
               ...(category ? [{ label: this.categoryName(category), category }] : []),
               { label: title }];
        const tab = { id: `t${++this._seq}`, ctx, title, catalogKey: catalogKey || null, category: category || (parent && parent.category) || null,
            parent: parent ? parent.id : null, crumbs, saved: null };
        crumbs[crumbs.length - 1].tabId = tab.id;
        this.state.tabs.push(tab);
        while (this.state.tabs.length > MAX_TABS) {
            const victim = this.state.tabs.find((t) => t.id !== this.state.active);
            this.state.tabs.splice(this.state.tabs.indexOf(victim), 1);
        }
        if (!background) {
            this.activate(tab.id);
        } else {
            this.notification.add(`Opened "${title}" in a new tab`, { type: "info" });
        }
        this.save();
        return tab;
    }

    openFromTab(tab, { ctx, title, crumb, background }) {
        return this.openTab({ ctx, title, parent: tab, crumb, background });
    }

    closeTab(id, ev) {
        ev && ev.stopPropagation();
        const idx = this.state.tabs.findIndex((t) => t.id === id);
        if (idx < 0) {
            return;
        }
        this.state.tabs.splice(idx, 1);
        this.state.history = this.state.history.filter((h) => h !== id);
        if (this.state.active === id) {
            const prev = this.state.history.pop();
            this.state.active = prev && (prev === "home" || this.state.tabs.find((t) => t.id === prev))
                ? prev : (this.state.tabs[idx] || this.state.tabs[idx - 1] || { id: "home" }).id;
        }
        this.save();
    }

    closeAll() {
        this.state.tabs = [];
        this.state.history = [];
        this.state.active = "home";
        this.save();
    }

    back() {
        while (this.state.history.length) {
            const prev = this.state.history.pop();
            if (prev === "home" || this.state.tabs.find((t) => t.id === prev)) {
                this.state.active = prev;
                this.save();
                return;
            }
        }
        // nothing left in the workspace: go back to the screen that opened it, if any
        const crumbs = this.odooCrumbs;
        if (crumbs.length) {
            crumbs[crumbs.length - 1].onSelected();
        } else if (this.state.active !== "home") {
            this.state.active = "home";
        }
    }

    get canGoBack() {
        return this.state.history.length > 0 || this.odooCrumbs.length > 0;
    }

    cycle(step) {
        const ids = ["home", ...this.state.tabs.map((t) => t.id)];
        const i = ids.indexOf(this.state.active);
        this.activate(ids[(i + step + ids.length) % ids.length]);
    }

    saveTabState(tab, st) {
        this.savedStates[tab.id] = st;
        this.save();
    }

    savedFor(tab) {
        return this.savedStates[tab.id] || null;
    }

    crumbClick(c) {
        if (c.home) {
            return this.goHome();
        }
        if (c.category) {
            this.state.category = c.category;
            return this.goHome();
        }
        if (c.tabId && this.state.tabs.find((t) => t.id === c.tabId)) {
            return this.activate(c.tabId);
        }
    }

    get odooCrumbs() {
        const bc = (this.env.config && this.env.config.breadcrumbs) || [];
        return bc.slice(0, -1).filter((b) => b && b.onSelected);
    }

    isListFocused() {
        return this.state.listFocused;
    }

    // ------------------------------------------------------------------ toolbar on the home pane
    async sendTo(fmt, inline = false, r = null) {
        const entry = r || this.selectedReport;
        if (!entry || entry.priority !== 1) {
            return;
        }
        if (entry.pack_id) {
            window.open(`/sonador_books/pack/${entry.pack_id}/${fmt}${inline ? "?inline=1" : ""}`, inline ? "_blank" : "_self");
            return;
        }
        return exportReport(this.orm, entry, fmt, inline);
    }

    async remove() {
        const r = this.selectedReport;
        if (!r || !r.custom || !r.mine || !confirm(`Delete the custom report "${r.name}"?`)) {
            return;
        }
        await this.orm.call("sb.report.view", "remove_view", [[r.view_id]]);
        const cat = await this.orm.call("sb.report", "get_catalog", []);
        this.state.reports = cat.reports.filter((r) => r.priority === 1);  // what is not available yet is not listed
    }

    // ------------------------------------------------------------------ keyboard
    onKeydown(ev) {
        if (!this.root.el || !document.body.contains(this.root.el) || document.querySelector(".modal")) {
            return;
        }
        const tag = (ev.target.tagName || "").toLowerCase();
        const typing = ["input", "select", "textarea"].includes(tag);
        if (ev.altKey && ev.key === "ArrowLeft") {
            ev.preventDefault();
            return this.back();
        }
        if (ev.altKey && (ev.key === "PageDown" || ev.key === "PageUp")) {
            ev.preventDefault();
            return this.cycle(ev.key === "PageDown" ? 1 : -1);
        }
        if (ev.ctrlKey && ev.key === "Tab") {
            ev.preventDefault();
            return this.cycle(ev.shiftKey ? -1 : 1);
        }
        if (ev.altKey && (ev.key === "w" || ev.key === "W") && this.state.active !== "home") {
            ev.preventDefault();
            return this.closeTab(this.state.active);
        }
        if (ev.key === "Backspace" && !typing && this.state.active !== "home") {
            ev.preventDefault();
            return this.back();
        }
        const listMode = this.state.active === "home" || this.state.listFocused;
        if (!listMode) {
            return; // the active report handles its own keys
        }
        if (ev.key === "/" && !typing) {
            ev.preventDefault();
            this.searchRef.el && this.searchRef.el.focus();
            return;
        }
        const list = this.keyboardList;
        const idx = list.findIndex((r) => r.key === this.state.selected);
        if (ev.key === "ArrowDown") {
            ev.preventDefault();
            this.state.selected = (list[Math.min(list.length - 1, idx + 1)] || {}).key;
        } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            this.state.selected = (list[Math.max(0, idx - 1)] || {}).key;
        } else if (ev.key === "Enter" && idx >= 0) {
            ev.preventDefault();
            this.openEntry(list[idx], ev);
        }
    }
}

registry.category("actions").add("sonador_books.report_center", ReportWorkspace);
registry.category("actions").add("sonador_books.report", ReportWorkspace);
