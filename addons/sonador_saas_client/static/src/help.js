/** @odoo-module **/
import { Component, markup, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { session } from "@web/session";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** The "?" in the top bar: search the help, read an answer, or ask Unio support (answers come back here and by email). */
export class SaasHelp extends Component {
    static template = "sonador_saas_client.Help";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.state = useState({ on: false, open: false, view: "search", q: "", results: null, article: null, mine: [], busy: false,
            ask: { subject: "", question: "" }, sent: false, voted: false, unread: 0 });
        this.markup = markup;
        /* guided tours, when that app is installed */
        this.tours = this.env.services.unio_tour || null;
        this.state.tours = [];
        this.timer = null;
        // whether help (and its AI answers) is there comes with the page; the person's questions load once the screen is up
        const flags = session.unio || {};
        this.state.on = !!flags.help;
        this.state.ai = !!flags.help_ai;
        onMounted(() => {
            if (this.state.on) {
                this._late = setTimeout(() => this.loadMine(), 2500);
            }
        });
        onWillUnmount(() => clearTimeout(this._late));
    }
    async loadMine() {
        try {
            this.state.mine = await this.orm.call("saas.help", "mine", []);
            this.state.unread = this.state.mine.filter((t) => t.unread).length;
        } catch {
            this.state.mine = [];
        }
    }
    async toggle() {
        this.state.open = !this.state.open;
        if (this.state.open && this.tours && !this.state.tours.length) {
            this.state.tours = await this.tours.list();
        }
    }
    /** Tours that match what is typed (all of them before anything is typed). */
    get matchingTours() {
        const words = this.state.q.toLowerCase().split(/\s+/).filter((w) => w.length > 2);
        if (!words.length) {
            return this.state.tours;
        }
        return this.state.tours.filter((t) => {
            const hay = (t.name + " " + t.app).toLowerCase();
            return words.some((w) => hay.includes(w));
        });
    }
    startTour(key) {
        this.state.open = false;
        this.tours.start(key);
    }
    tourFor(article) {
        return article && article.tour && this.state.tours.find((t) => t.key === article.tour);
    }
    allTours() {
        this.state.open = false;
        this.env.services.action.doAction("sonador_tours.action_tours");
    }
    onInput(ev) {
        this.state.q = ev.target.value;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.search(), 300);
    }
    async search() {
        if (this.state.q.trim().length < 2) {
            this.state.results = null;
            return;
        }
        this.state.results = await this.orm.call("saas.help", "search", [this.state.q]);
        this.state.view = "search";
        this.state.aiAnswer = null;
    }
    async askAi() {
        this.state.aiBusy = true;
        try {
            this.state.aiAnswer = await this.orm.call("saas.help", "ai_answer", [this.state.q]);
        } finally {
            this.state.aiBusy = false;
        }
    }
    async open(a) {
        this.state.article = await this.orm.call("saas.help", "article", [a.id]);
        this.state.voted = false;
        this.state.view = "article";
    }
    async vote(helpful) {
        await this.orm.call("saas.help", "vote", [this.state.article.id, helpful]);
        this.state.voted = true;
        if (!helpful) {
            this.startAsk();
        }
    }
    startAsk() {
        this.state.ask = { subject: this.state.q || (this.state.article && this.state.article.title) || "", question: "" };
        this.state.sent = false;
        this.state.view = "ask";
    }
    async send() {
        this.state.busy = true;
        try {
            this.state.mine = await this.orm.call("saas.help", "ask", [this.state.ask.subject, this.state.ask.question, window.location.href]);
            this.state.sent = true;
        } finally {
            this.state.busy = false;
        }
    }
    async showMine() {
        this.state.mine = await this.orm.call("saas.help", "mine", []);
        this.state.view = "mine";
        for (const t of this.state.mine.filter((x) => x.unread)) {
            await this.orm.call("saas.help", "mark_read", [t.id]);
        }
        this.state.unread = 0;
    }
}

registry.category("systray").add("sonador_saas_client.help", { Component: SaasHelp }, { sequence: 2 });
