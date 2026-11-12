/** @odoo-module **/
import { Component, onWillStart, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const ICONS = ["truck", "shield", "cash", "star", "clock", "leaf", "heart", "phone", "gift", "check"];

/** The Website app: the site at a glance, pages built from blocks with a live preview, products, look, settings. */
export class SiteApp extends Component {
    static template = "sonador_site.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.frame = useRef("frame");
        this.icons = ICONS;
        this.state = useState({
            tab: "home", d: null, page: null, block: null, adding: false, saving: false, dirty: false,
            products: [], ptotal: 0, pq: "", chapaKey: "", settings: {}, previewUrl: "", device: "desktop", confirmDel: null,
        });
        this.timer = null;
        onWillStart(async () => {
            await this.load();
        });
    }

    async load() {
        const d = await this.orm.call("unio.site", "builder_data", []);
        this.state.d = d;
        this.state.settings = { ...d.site };
        if (!this.state.page && d.pages.length) {
            this.state.page = d.pages[0];
        } else if (this.state.page) {
            this.state.page = d.pages.find((p) => p.id === this.state.page.id) || d.pages[0];
        }
    }

    // ------------------------------------------------------------------ navigation
    async go(tab) {
        this.state.tab = tab;
        if (tab === "products" && !this.state.products.length) {
            await this.loadProducts();
        }
        if (tab === "pages") {
            this.refreshPreview();
        }
    }
    get siteUrl() {
        const d = this.state.d;
        return (d.storefront || d.url || "") + "/";
    }
    openSite() {
        window.open(this.siteUrl, "_blank");
    }
    openOrders() {
        this.action.doAction("sonador_site.action_site_orders");
    }
    openOrder(o) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sale.order", res_id: o.id, views: [[false, "form"]] });
    }
    get checklist() {
        const d = this.state.d, s = d.site;
        return [
            { done: d.site.has_logo, text: "Add your logo", tab: "look" },
            { done: d.counts.published > 0, text: "Put products on the site", tab: "products" },
            { done: !!(s.phone && s.address), text: "Phone and address", tab: "settings" },
            { done: s.pickup || s.delivery, text: "Pickup or delivery", tab: "settings" },
            { done: s.published, text: "Put the site online", tab: "home" },
        ];
    }
    get maxVisits() {
        let m = 1;
        for (const x of this.state.d.visits.days) {
            if (x.views > m) m = x.views;
        }
        return m;
    }
    barHeight(v) {
        return Math.round((v / this.maxVisits) * 100);
    }
    async toggleOnline() {
        this.state.d = await this.orm.call("unio.site", "set_online", [!this.state.d.site.published]);
        this.state.settings = { ...this.state.d.site };
        this.notification.add(this.state.d.site.published ? "Your site is online." : "Your site is offline.", { type: "success" });
    }
    money(v) {
        return (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    // ------------------------------------------------------------------ pages and blocks
    selectPage(p) {
        this.state.page = p;
        this.state.block = null;
        this.state.adding = false;
        this.refreshPreview();
    }
    async newPage() {
        const res = await this.orm.call("unio.site", "save_page", [false, { name: "New page", slug: "new-page", blocks: [{ ...this.state.d.blocks.text.default, type: "text" }] }]);
        this.state.d = res.data;
        this.state.page = res.data.pages.find((p) => p.id === res.id);
        this.refreshPreview();
    }
    async deletePage() {
        if (this.state.confirmDel !== this.state.page.id) {
            this.state.confirmDel = this.state.page.id;
            return;
        }
        this.state.d = await this.orm.call("unio.site", "delete_page", [this.state.page.id]);
        this.state.page = this.state.d.pages[0];
        this.state.confirmDel = null;
        this.refreshPreview();
    }
    blockInfo(b) {
        return this.state.d.blocks[b.type] || { label: b.type, icon: "fa-square", fields: [] };
    }
    blockSummary(b) {
        return b.title || b.subtitle || b.body || "";
    }
    addBlock(type) {
        const def = JSON.parse(JSON.stringify(this.state.d.blocks[type].default));
        const b = { ...def, type };
        const at = this.state.block ? this.state.page.blocks.indexOf(this.state.block) + 1 : this.state.page.blocks.length;
        this.state.page.blocks.splice(at, 0, b);
        this.state.block = this.state.page.blocks[at];
        this.state.adding = false;
        this.changed();
    }
    moveBlock(b, dir) {
        const list = this.state.page.blocks;
        const i = list.indexOf(b);
        const j = i + dir;
        if (j < 0 || j >= list.length) return;
        const [x] = list.splice(i, 1);
        list.splice(j, 0, x);
        this.changed();
    }
    removeBlock(b) {
        const list = this.state.page.blocks;
        list.splice(list.indexOf(b), 1);
        if (this.state.block === b) this.state.block = null;
        this.changed();
    }
    editBlock(b) {
        this.state.block = this.state.block === b ? null : b;
        this.state.adding = false;
        const i = this.state.page.blocks.indexOf(b);
        try {
            this.frame.el?.contentWindow.postMessage({ unio_scroll: "b-" + i }, "*");
        } catch {
            /* the preview is not loaded yet */
        }
    }
    fieldKind(f) {
        return f[1].split(":")[0];
    }
    fieldChoices(f) {
        return (f[1].split(":")[1] || "").split("|");
    }
    listKeys(f) {
        return (f[1].split(":")[1] || "").split("|");
    }
    choiceLabel(c) {
        const L = { split: "Picture beside", full: "Picture behind", plain: "No picture", featured: "Featured", newest: "Newest",
            category: "From a category", left: "Left", right: "Right", center: "Centre" };
        return L[c] || c;
    }
    setField(b, key, value) {
        b[key] = value;
        this.changed();
    }
    setNumber(b, key, ev) {
        b[key] = parseInt(ev.target.value || "0", 10);
        this.changed();
    }
    addItem(b, key, f) {
        const item = {};
        for (const k of this.listKeys(f)) item[k] = k === "icon" ? "star" : "";
        b[key] = [...(b[key] || []), item];
        this.changed();
    }
    removeItem(b, key, i) {
        b[key].splice(i, 1);
        this.changed();
    }
    setItem(b, key, i, k, value) {
        b[key][i][k] = value;
        this.changed();
    }
    async uploadTo(b, key, ev, many = false) {
        const files = [...ev.target.files];
        for (const file of files) {
            const data = await new Promise((resolve) => {
                const r = new FileReader();
                r.onload = () => resolve(r.result.split(",")[1]);
                r.readAsDataURL(file);
            });
            const url = await this.orm.call("unio.site", "upload_image", [file.name, data]);
            if (many) {
                b[key] = [...(b[key] || []), url];
            } else {
                b[key] = url;
            }
        }
        this.changed();
    }
    removeImage(b, key, i) {
        b[key].splice(i, 1);
        this.changed();
    }
    changed() {
        this.state.dirty = true;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.saveDraft(), 700);
    }
    cleanBlocks() {
        return this.state.page.blocks.map((b) => {
            const c = { ...b };
            delete c.products;
            return c;
        });
    }
    async saveDraft() {
        const p = this.state.page;
        this.state.saving = true;
        try {
            await this.orm.call("unio.site", "save_page", [p.id, { name: p.name, slug: p.slug, menu: p.menu, seo_title: p.seo_title, seo_description: p.seo_description, blocks: this.cleanBlocks() }]);
            this.state.dirty = false;
            p.changed = true;
            this.refreshPreview();
        } finally {
            this.state.saving = false;
        }
    }
    async publish() {
        clearTimeout(this.timer);
        const p = this.state.page;
        const res = await this.orm.call("unio.site", "save_page", [p.id, { name: p.name, slug: p.slug, menu: p.menu, seo_title: p.seo_title, seo_description: p.seo_description, blocks: this.cleanBlocks() }, true]);
        const keep = this.state.block ? p.blocks.indexOf(this.state.block) : -1;
        this.state.d = res.data;
        this.state.page = res.data.pages.find((x) => x.id === p.id);
        this.state.block = keep >= 0 ? this.state.page.blocks[keep] : null;
        this.state.dirty = false;
        this.notification.add(`"${this.state.page.name}" is published.`, { type: "success" });
        this.refreshPreview();
    }
    setPage(key, value) {
        this.state.page[key] = value;
        this.changed();
    }
    async refreshPreview() {
        if (!this.state.page) return;
        const path = await this.orm.call("unio.site", "preview_url", [this.state.page.id]);
        const at = this.state.block ? this.state.page.blocks.indexOf(this.state.block) : -1;
        /* the preview opens at the block being edited */
        this.state.previewUrl = (this.state.d.storefront || "") + path + "&t=" + Date.now() + (at >= 0 ? "#b-" + at : "");
    }

    // ------------------------------------------------------------------ products
    async loadProducts() {
        const r = await this.orm.call("product.template", "site_products", [this.state.pq, 200, 0]);
        this.state.products = r.products;
        this.state.ptotal = r.total;
    }
    onSearch(ev) {
        this.state.pq = ev.target.value;
        clearTimeout(this.ptimer);
        this.ptimer = setTimeout(() => this.loadProducts(), 300);
    }
    async setProduct(p, key, value) {
        p[key] = value;
        await this.orm.call("product.template", "site_set", [[p.id], { ["site_" + key]: value }]);
        if (key === "published") {
            this.state.d.counts.published += value ? 1 : -1;
        }
    }
    async publishAll(value) {
        const ids = this.state.products.map((p) => p.id);
        await this.orm.call("product.template", "site_set", [ids, { site_published: value }]);
        for (const p of this.state.products) p.published = value;
        await this.load();
    }
    async toggleCat(p, cid) {
        const has = p.categories.includes(cid);
        p.categories = has ? p.categories.filter((x) => x !== cid) : [...p.categories, cid];
        await this.orm.call("product.template", "site_set", [[p.id], { categories: p.categories }]);
    }
    openProduct(p) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "product.template", res_id: p.id, views: [[false, "form"]] });
    }
    openCategories() {
        this.action.doAction("sonador_site.action_site_categories");
    }

    // ------------------------------------------------------------------ look and settings
    get blockKeys() {
        return Object.keys(this.state.d.blocks);
    }
    setSNum(key, ev) {
        this.state.settings[key] = parseFloat(ev.target.value || "0");
    }
    setS(key, value) {
        this.state.settings[key] = value;
    }
    async saveSettings() {
        this.state.d = await this.orm.call("unio.site", "save_settings", [this.state.settings]);
        this.state.settings = { ...this.state.d.site };
        this.notification.add("Saved.", { type: "success" });
        this.refreshPreview();
    }
    async saveChapa() {
        this.state.d = await this.orm.call("unio.site", "set_chapa_key", [this.state.chapaKey]);
        this.state.settings = { ...this.state.d.site };
        this.state.chapaKey = "";
        this.notification.add("Chapa accepted the key.", { type: "success" });
    }
    async removeChapa() {
        this.state.d = await this.orm.call("unio.site", "set_chapa_key", [""]);
        this.state.settings = { ...this.state.d.site };
    }
    async uploadLogo(ev) {
        const file = ev.target.files[0];
        if (!file) return;
        const data = await new Promise((resolve) => {
            const r = new FileReader();
            r.onload = () => resolve(r.result.split(",")[1]);
            r.readAsDataURL(file);
        });
        await this.orm.call("unio.site", "save_logo", [data]);
        await this.load();
        this.notification.add("Logo saved.", { type: "success" });
    }
}
registry.category("actions").add("sonador_site.app", SiteApp);
