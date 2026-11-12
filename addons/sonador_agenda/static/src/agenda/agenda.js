import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const money = (v) => Number(v || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parse = (s) => new Date(s + "T00:00:00");
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** Agenda: what is due, for the person looking, from the dates the other apps already hold. */
export class AgendaApp extends Component {
    static template = "sonador_agenda.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            d: null, area: "all", view: "list", month: null, day: null, menu: null, give: null,
            todo: { name: "", date: "", user_id: false },
        });
        onWillStart(() => this.load());
    }
    money(v) {
        return money(v);
    }
    async call(method, args = []) {
        this.state.d = await this.orm.call("agenda.engine", method, args);
        this.state.menu = null;
        this.state.give = null;
    }
    async load() {
        await this.call("agenda");
        const t = parse(this.state.d.today);
        this.state.month = new Date(t.getFullYear(), t.getMonth(), 1);
        this.state.todo.user_id = this.state.d.uid;
    }
    get areaItems() {
        const a = this.state.area;
        const items = this.state.d.items;
        return a === "all" ? items : items.filter((i) => (a === "mine" ? i.mine : i.area === a));
    }
    get items() {
        return this.state.day ? this.areaItems.filter((i) => i.date === this.state.day) : this.areaItems;
    }
    get groups() {
        const today = this.state.d.today;
        const t = parse(today);
        const week = iso(new Date(t.getFullYear(), t.getMonth(), t.getDate() + 7));
        const out = [
            { key: "overdue", name: "Overdue", items: [] },
            { key: "today", name: "Today", items: [] },
            { key: "week", name: "Next 7 days", items: [] },
            { key: "later", name: "Later", items: [] },
        ];
        for (const i of this.items) {
            const g = i.date < today ? 0 : i.date === today ? 1 : i.date <= week ? 2 : 3;
            out[g].items.push(i);
        }
        return out.filter((g) => g.items.length);
    }
    get todayLabel() {
        return parse(this.state.d.today).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    }
    dayLabel(i) {
        const d = parse(i.date);
        return { day: d.getDate(), mon: MONTHS[d.getMonth()].slice(0, 3), dow: d.toLocaleDateString("en-GB", { weekday: "short" }) };
    }
    areaName(code) {
        const a = this.state.d.areas.find((x) => x.code === code);
        return a ? a.name : code;
    }
    setArea(a) {
        this.state.area = a;
        this.state.day = null;
    }
    // ------------------------------------------------------------ actions
    open(i) {
        if (i.ids && i.ids.length) {
            this.action.doAction({ type: "ir.actions.act_window", name: i.title, res_model: i.model, domain: [["id", "in", i.ids]],
                views: [[false, "list"], [false, "form"]] });
        } else if (i.model && i.res_id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: i.model, res_id: i.res_id, views: [[false, "form"]] });
        }
    }
    async done(i) {
        if (i.todo_id) {
            await this.call("todo_done", [i.todo_id]);
        } else {
            await this.call("mark_done", [i.key]);
            this.notification.add("Marked as filed and paid.", { type: "success" });
        }
    }
    async later(i, days) {
        await this.call("later", [i.key, days]);
    }
    toggleMenu(i) {
        this.state.menu = this.state.menu === i.key ? null : i.key;
        this.state.give = null;
    }
    toggleGive(i) {
        this.state.give = this.state.give === i.key ? null : i.key;
        this.state.menu = null;
    }
    async giveTo(i, ev) {
        const uid = parseInt(ev.target.value);
        if (!uid) {
            return;
        }
        await this.call("add_todo", [{ name: i.title, date: i.date, user_id: uid, priority: i.importance, note: i.sub,
            source_key: i.key, res_model: i.model, res_id: i.res_id }]);
        const who = this.state.d.users.find((u) => u.id === uid);
        this.notification.add(`Given to ${who ? who.name : "them"}. It stays on their agenda until it is done.`, { type: "success" });
    }
    async addTodo(ev) {
        if (ev) {
            ev.preventDefault();
        }
        const t = this.state.todo;
        if (!t.name.trim()) {
            return;
        }
        await this.call("add_todo", [{ name: t.name.trim(), date: t.date || false, user_id: parseInt(t.user_id) || this.state.d.uid }]);
        t.name = "";
        t.date = "";
    }
    openTodos() {
        this.action.doAction("sonador_agenda.action_agenda_todos");
    }
    // ------------------------------------------------------------ month
    get weeks() {
        const m = this.state.month;
        const first = new Date(m.getFullYear(), m.getMonth(), 1);
        const start = new Date(first);
        start.setDate(1 - ((first.getDay() + 6) % 7));
        const byDay = {};
        for (const i of this.areaItems) {
            (byDay[i.date] = byDay[i.date] || []).push(i);
        }
        const weeks = [];
        const d = new Date(start);
        do {
            const week = [];
            for (let k = 0; k < 7; k++) {
                const key = iso(d);
                week.push({ key, n: d.getDate(), out: d.getMonth() !== m.getMonth(), items: byDay[key] || [],
                    today: key === this.state.d.today });
                d.setDate(d.getDate() + 1);
            }
            weeks.push(week);
        } while (d.getMonth() === m.getMonth());
        return weeks;
    }
    get monthName() {
        const m = this.state.month;
        return `${MONTHS[m.getMonth()]} ${m.getFullYear()}`;
    }
    shiftMonth(n) {
        const m = this.state.month;
        this.state.month = new Date(m.getFullYear(), m.getMonth() + n, 1);
    }
    pickDay(cell) {
        this.state.day = this.state.day === cell.key ? null : cell.key;
    }
}
registry.category("actions").add("sonador_agenda.app", AgendaApp);
