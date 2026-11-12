/** @odoo-module **/
/**
 * The system's address is /app (company.unio.et/app/...), not /odoo. The web client is written for /odoo, so it is
 * shown /odoo-style addresses (browser.location) while the address bar and the history get /app ones.
 */
import { browser } from "@web/core/browser/browser";
import { startRouter } from "@web/core/browser/router";

const real = window.location;
const APP = /^\/app(?=\/|$)/;
const ODOO = /^\/odoo(?=\/|$)/;
const asOdoo = (path) => path.replace(APP, "/odoo");
function asApp(url) {
    if (url === undefined || url === null) return url;
    try {
        const u = new URL(String(url), real.href);
        if (u.origin === real.origin && ODOO.test(u.pathname)) {
            u.pathname = u.pathname.replace(ODOO, "/app");
            return u.href;
        }
    } catch {
        /* not a url */
    }
    return url;
}
const odooHref = () => real.origin + asOdoo(real.pathname) + real.search + real.hash;

const location = new Proxy({}, {
    get(_t, key) {
        if (key === "pathname") return asOdoo(real.pathname);
        if (key === "href") return odooHref();
        if (key === "toString" || key === "valueOf") return odooHref;
        if (key === "assign" || key === "replace") return (url) => real[key](asApp(url));
        const v = real[key];
        return typeof v === "function" ? v.bind(real) : v;
    },
    set(_t, key, value) {
        real[key] = key === "href" || key === "pathname" ? asApp(value) : value;
        return true;
    },
});
Object.defineProperty(browser, "location", {
    get: () => location,
    set: (val) => {
        window.location = asApp(val);
    },
    configurable: true,
});

const h = window.history;
browser.history = {
    pushState: (s, t, url) => h.pushState(s, t, asApp(url)),
    replaceState: (s, t, url) => h.replaceState(s, t, asApp(url)),
    back: () => h.back(),
    forward: () => h.forward(),
    go: (n) => h.go(n),
    get state() {
        return h.state;
    },
    get length() {
        return h.length;
    },
    get scrollRestoration() {
        return h.scrollRestoration;
    },
    set scrollRestoration(v) {
        h.scrollRestoration = v;
    },
};

// arrived on an /odoo address (old link, email): show /app at once
if (ODOO.test(real.pathname)) {
    h.replaceState(h.state, "", asApp(real.href));
}

// the router read the address when it was loaded, before this: read it again, /odoo-style
startRouter();
