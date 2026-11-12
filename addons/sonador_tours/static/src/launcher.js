/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { tourState } from "./engine";

const seenKey = (key) => `unio_tour_seen_${user.userId}_${key}`;
function seen(key) {
    try {
        return localStorage.getItem(seenKey(key)) === "1";
    } catch {
        return true;
    }
}
function markSeen(key) {
    try {
        localStorage.setItem(seenKey(key), "1");
    } catch { /* private window */ }
}

/** The page with every tour, by app ("Guided tours" in the user menu, "Show me how" in help). */
export class TourList extends Component {
    static props = ["*"];
    static template = xml`
        <div class="o_ut_list o_action">
            <div class="o_ut_list_in">
                <h2>Guided tours</h2>
                <p class="text-muted">Each tour walks you through doing the real thing, on your own data, in a minute or two. Stop any time.</p>
                <div t-foreach="groups" t-as="g" t-key="g.app" class="o_ut_group">
                    <h5 t-esc="g.app"/>
                    <div t-foreach="g.tours" t-as="t" t-key="t.key" class="o_ut_card" t-on-click="() => this.start(t.key)">
                        <i t-attf-class="fa {{ t.done ? 'fa-check-circle text-success' : 'fa-play-circle o_ut_play' }}"/>
                        <span class="o_ut_card_name" t-esc="t.name"/>
                        <span class="text-muted small"><t t-esc="t.minutes"/> min</span>
                    </div>
                </div>
            </div>
        </div>`;
    setup() {
        this.tours = useService("unio_tour");
        this.state = useState({ list: [] });
        onWillStart(async () => (this.state.list = await this.tours.list()));
    }
    get groups() {
        const out = [];
        for (const t of this.state.list) {
            let g = out.find((x) => x.app === t.app);
            if (!g) {
                g = { app: t.app, tours: [] };
                out.push(g);
            }
            g.tours.push(t);
        }
        return out;
    }
    start(key) {
        this.tours.start(key);
    }
}
registry.category("actions").add("unio_tours.list", TourList);

/** "New here? Take the 2-minute tour", the first time someone opens a screen that has a tour. */
export class TourOffer extends Component {
    static props = {};
    static template = xml`
        <div t-if="state.offer and !t.key" class="o_ut_offer">
            <img src="/sonador_tours/static/src/mark.svg" alt=""/>
            <div>
                <div class="fw-bold" t-esc="state.offer.key === 'welcome' ? 'New to Unio?' : 'New here?'"/>
                <div class="small">Take the <t t-esc="state.offer.minutes"/>-minute tour: <t t-esc="state.offer.name"/>.</div>
                <div class="mt-2">
                    <button class="btn btn-primary btn-sm" t-on-click="take">Show me</button>
                    <button class="btn btn-link btn-sm" t-on-click="later">Not now</button>
                </div>
            </div>
        </div>`;
    setup() {
        this.tours = useService("unio_tour");
        this.action = useService("action");
        this.t = useState(tourState);
        this.state = useState({ offer: null });
        const check = () => setTimeout(() => this.check(), 900);
        onMounted(() => {
            this.env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", check);
            check();
        });
        onWillUnmount(() => this.env.bus.removeEventListener("ACTION_MANAGER:UI-UPDATED", check));
    }
    async check() {
        if (tourState.key || this.state.offer || document.querySelector(".modal")) {
            return;
        }
        const list = await this.tours.list();
        const ctrl = this.action.currentController;
        const act = ctrl && ctrl.action;
        const here = act && (act.xml_id || act.path);
        let offer = null;
        if (!seen("welcome") && list.find((t) => t.key === "welcome")) {
            offer = list.find((t) => t.key === "welcome");
        } else if (here) {
            offer = list.find((t) => t.start && (t.start === here || t.start.endsWith("." + here)) && !t.done && !seen(t.key));
        }
        if (offer) {
            markSeen(offer.key);
            this.state.offer = offer;
            this.tours.log("offer", offer.key, 0);
        }
    }
    take() {
        const key = this.state.offer.key;
        this.state.offer = null;
        this.tours.start(key);
    }
    later() {
        this.tours.log("decline", this.state.offer.key, 0);
        this.state.offer = null;
    }
}
registry.category("main_components").add("unio_tour_offer", { Component: TourOffer });

registry.category("user_menuitems").add("unio_tours", (env) => ({
    type: "item",
    id: "unio_tours",
    description: "Guided tours",
    callback: () => env.services.action.doAction("sonador_tours.action_tours"),
    sequence: 25,
}));
