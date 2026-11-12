/** @odoo-module **/
import { Component, onMounted, onWillUnmount, reactive, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Unio guided tours. A tour is a list of steps:
 *   { action: "module.xmlid" }     open this screen first (optional)
 *   { sel: "css", text: "…" }       the element to point at (the first visible match containing the text)
 *   { title, body }                 what to say
 *   { advance: "click" | "input" | "next" }   move on when the person clicks it, types in it, or presses Next
 * Tours live in registry.category("unio_tours").
 */
export const tourState = reactive({ key: null, i: 0, el: null, rect: null, missing: false, paused: false });

function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
}
export function findTarget(step) {
    if (!step.sel) return null;
    const want = (step.text || "").toLowerCase();
    /* the selectors in a list are tried in order: the first one that matches wins */
    for (const sel of step.sel.split(/,(?![^(\[]*[)\]])/)) {
        for (const el of document.querySelectorAll(sel)) {
            if (!visible(el)) continue;
            if (!want || (el.innerText || el.value || el.placeholder || "").toLowerCase().includes(want)) return el;
        }
    }
    return null;
}

export const unioTourService = {
    dependencies: ["action", "orm", "menu"],
    start(env, { action, orm, menu }) {
        let poll = null, onClick = null, onInput = null;
        const log = (event, key = tourState.key, i = tourState.i) => {
            orm.call("unio.tour", "event", [key, i, event]).catch(() => {});
        };
        let availability = null;
        const tours = () => registry.category("unio_tours");
        const step = () => (tourState.key ? tours().get(tourState.key).steps[tourState.i] : null);
        const unhook = () => {
            clearInterval(poll);
            clearInterval(api._typing);
            if (onClick) document.removeEventListener("click", onClick, true);
            if (onInput) document.removeEventListener("input", onInput, true);
            onClick = onInput = null;
        };
        const locate = () => {
            const s = step();
            unhook();
            tourState.el = null;
            tourState.rect = null;
            tourState.missing = false;
            if (!s) return;
            let tries = 0;
            const look = () => {
                /* a dialog opened over the screen (an error, a confirmation): step aside until it closes */
                const modal = [...document.querySelectorAll(".modal")].find(visible);
                const el = findTarget(s);
                tourState.paused = !!modal && !(el && modal.contains(el));
                if (tourState.paused) {
                    return;
                }
                if (el) {
                    if (tourState.el !== el) {
                        tourState.el = el;
                        el.scrollIntoView({ block: "center", behavior: "smooth" });
                    }
                    const r = el.getBoundingClientRect();
                    tourState.rect = { x: r.left, y: r.top, w: r.width, h: r.height };
                    tourState.missing = false;
                } else if (s.optional && ++tries > 5) {
                    /* not in this system (e.g. AI not switched on): pass over it in the way we were going */
                    clearInterval(poll);
                    if (api._dir < 0 && tourState.i > 0) {
                        api.back();
                    } else {
                        api.next();
                    }
                } else if (s.sel && ++tries > 20) {
                    tourState.missing = true;
                    tourState.rect = null;
                }
            };
            poll = setInterval(look, 300);
            look();
            if (s.advance === "click") {
                onClick = (ev) => {
                    if (tourState.el && (ev.target === tourState.el || tourState.el.contains(ev.target))) {
                        const at = tourState.i;
                        const modals = document.querySelectorAll(".modal").length;
                        setTimeout(() => {
                            /* if it did not go through (a required field, an error), stay here so they can fix it */
                            const stuck = document.querySelectorAll(".modal").length > modals ||
                                document.querySelector(".o_notification.border-danger, .o_field_invalid");
                            if (tourState.i === at && !stuck) {
                                api.next();
                            }
                        }, 900);
                    }
                };
                document.addEventListener("click", onClick, true);
            }
            if (s.advance === "input") {
                onInput = (ev) => {
                    if (tourState.el && (ev.target === tourState.el || tourState.el.contains(ev.target))) {
                        /* move on once they stop typing and have picked from any list that opened */
                        const field = ev.target;
                        clearInterval(api._typing);
                        let calm = 0;
                        api._typing = setInterval(() => {
                            const open = document.querySelector(".o-autocomplete--dropdown-menu, .o_m2o_dropdown_option, .dropdown-menu.show");
                            calm = field.value && !open ? calm + 1 : 0;
                            if (calm >= 3) {
                                clearInterval(api._typing);
                                api.next();
                            }
                        }, 400);
                    }
                };
                document.addEventListener("input", onInput, true);
            }
        };
        const api = {
            _typing: null,
            log,
            /** The tours this person can take here (their first screen exists and is open to them). */
            async list() {
                if (!availability) {
                    const starts = {};
                    for (const [key, t] of tours().getEntries()) {
                        starts[key] = t.steps.find((s) => s.action)?.action || false;
                    }
                    availability = orm.call("unio.tour", "available", [starts]).catch(() => Object.keys(starts));
                }
                const ok = await availability;
                return tours().getEntries()
                    .filter(([key]) => ok.includes(key))
                    .map(([key, t]) => ({ key, name: t.name, app: t.app, minutes: t.minutes || 2, sequence: t.sequence || 50,
                        start: t.steps.find((s) => s.action)?.action || false, done: api.isDone(key) }))
                    .sort((a, b) => a.sequence - b.sequence);
            },
            isDone(key) {
                try {
                    return localStorage.getItem("unio_tour_done_" + key) === "1";
                } catch {
                    return false;
                }
            },
            async start(key, at = 0) {
                if (!tours().contains(key)) return;
                tourState.key = key;
                tourState.i = at;
                document.body.classList.add("o_ut_on");
                log("start");
                await api.go();
            },
            async go() {
                const s = step();
                if (!s) return api.finish();
                if (s.action) {
                    try {
                        await action.doAction(s.action, { clearBreadcrumbs: !!s.clear });
                        /* light up the app the screen belongs to, as if it had been opened from the menu */
                        const id = action.currentController?.action?.id;
                        const m = id && menu.getAll().find((x) => x.actionID === id);
                        if (m) {
                            menu.setCurrentMenu(m);
                        }
                    } catch {
                        /* the screen is not in this system: the step explains where it would be */
                    }
                }
                locate();
            },
            _dir: 1,
            async next() {
                if (!tourState.key) return;
                api._dir = 1;
                tourState.i += 1;
                if (tourState.i >= tours().get(tourState.key).steps.length) return api.finish();
                await api.go();
            },
            async back() {
                api._dir = -1;
                if (tourState.i > 0) {
                    tourState.i -= 1;
                    await api.go();
                }
            },
            finish() {
                log("done");
                try {
                    localStorage.setItem("unio_tour_done_" + tourState.key, "1");
                } catch { /* private window */ }
                api.close();
            },
            skip() {
                log("skip");
                api.close();
            },
            close() {
                unhook();
                document.body.classList.remove("o_ut_on");
                tourState.key = null;
                tourState.el = null;
                tourState.rect = null;
            },
        };
        return api;
    },
};
registry.category("services").add("unio_tour", unioTourService);

/** The spotlight and the speech bubble. */
export class TourOverlay extends Component {
    static props = {};
    static template = xml`
        <t t-if="t.key and !t.paused">
            <t t-set="s" t-value="step"/>
            <div class="o_ut_shade" t-if="!t.rect"/>
            <div class="o_ut_spot" t-if="t.rect" t-attf-style="left: {{ t.rect.x - 6 }}px; top: {{ t.rect.y - 6 }}px; width: {{ t.rect.w + 12 }}px; height: {{ t.rect.h + 12 }}px;"/>
            <div class="o_ut_bubble" t-if="t.rect or !s.sel or t.missing" t-attf-style="{{ bubbleStyle }}">
                <div class="o_ut_progress"><span t-esc="tour.name"/><span><t t-esc="t.i + 1"/> / <t t-esc="tour.steps.length"/></span></div>
                <h4 t-esc="s.title"/>
                <p t-esc="s.body"/>
                <p t-if="t.missing and s.sel" class="o_ut_missing">Can't see it on this screen? <t t-esc="s.where or 'It may be hidden in this system, or one step back.'"/> You can press Next.</p>
                <div class="o_ut_buttons">
                    <button class="btn btn-link btn-sm" t-on-click="() => this.tours.skip()">Skip tour</button>
                    <span>
                        <button class="btn btn-light btn-sm" t-if="t.i" t-on-click="() => this.tours.back()">Back</button>
                        <button class="btn btn-primary btn-sm ms-1" t-if="s.advance !== 'click' or t.missing" t-on-click="() => this.tours.next()">
                            <t t-if="t.i + 1 === tour.steps.length">Finish</t><t t-else="">Next</t>
                        </button>
                        <span t-else="" class="o_ut_hint">Click the highlighted button</span>
                    </span>
                </div>
            </div>
        </t>`;

    setup() {
        this.t = useState(tourState);
        this.tours = useService("unio_tour");
        const onResize = () => {
            if (tourState.el) {
                const r = tourState.el.getBoundingClientRect();
                tourState.rect = { x: r.left, y: r.top, w: r.width, h: r.height };
            }
        };
        onMounted(() => window.addEventListener("resize", onResize));
        onWillUnmount(() => window.removeEventListener("resize", onResize));
    }
    get tour() {
        return registry.category("unio_tours").get(this.t.key);
    }
    get step() {
        return this.tour.steps[this.t.i] || {};
    }
    get bubbleStyle() {
        const r = this.t.rect;
        const W = window.innerWidth, H = window.innerHeight, bw = 340, bh = 200, gap = 16;
        if (!r) return `left: ${(W - bw) / 2}px; top: ${H * 0.28}px;`;
        const clampX = (x) => Math.min(Math.max(x, 12), W - bw - 12);
        const clampY = (y) => Math.min(Math.max(y, 12), H - bh - 12);
        const spots = {
            below: r.y + r.h + gap + bh < H && { left: clampX(r.x + r.w / 2 - bw / 2), top: r.y + r.h + gap },
            above: r.y - gap - bh > 0 && { left: clampX(r.x + r.w / 2 - bw / 2), bottom: H - r.y + gap },
            right: r.x + r.w + gap + bw < W && { left: r.x + r.w + gap, top: clampY(r.y - 10) },
            left: r.x - gap - bw > 0 && { left: r.x - gap - bw, top: clampY(r.y - 10) },
        };
        /* where someone types, a list may open underneath: keep out of its way */
        let order = this.step.advance === "input" ? ["right", "above", "left", "below"] : ["below", "above", "right", "left"];
        if (this.step.place) {
            order = [this.step.place, ...order.filter((k) => k !== this.step.place)];
        }
        const pos = order.map((k) => spots[k]).find(Boolean) || { left: clampX(r.x), top: clampY(r.y + r.h + gap) };
        return pos.bottom !== undefined ? `left: ${pos.left}px; bottom: ${pos.bottom}px;` : `left: ${pos.left}px; top: ${pos.top}px;`;
    }

}
registry.category("main_components").add("unio_tour_overlay", { Component: TourOverlay });
