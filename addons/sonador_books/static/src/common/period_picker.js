import { Component, useState, useExternalListener, useRef } from "@odoo/owl";

const CHIPS = [
    ["this_month", "This month"],
    ["last_month", "Last month"],
    ["this_quarter", "This quarter"],
    ["ytd", "Year to date"],
    ["this_fy", "This fiscal year"],
    ["last_fy", "Last fiscal year"],
    ["all", "All dates"],
];
const ALL_FROM = "1900-01-01";
const ALL_TO = "2999-12-31";

/**
 * Ethiopian period picker.
 * props: periods (from sb.report.get_periods), dateFrom, dateTo, asOf (single date), onChange(dateFrom, dateTo, label)
 */
export class SbPeriodPicker extends Component {
    static template = "sonador_books.SbPeriodPicker";
    static props = ["*"];

    setup() {
        this.chips = this.props.asOf ? CHIPS.filter((c) => c[0] !== "all") : CHIPS;
        this.root = useRef("root");
        const fyOf = this.fyFor(this.props.dateTo) || this.props.periods.current_fy;
        this.state = useState({ open: false, fy: fyOf, anchor: null });
        useExternalListener(window, "mousedown", (ev) => {
            if (this.state.open && this.root.el && !this.root.el.contains(ev.target)) {
                this.state.open = false;
            }
        });
    }

    get years() {
        return this.props.periods.years;
    }
    get year() {
        return this.years.find((y) => y.fy === this.state.fy) || this.years[this.years.length - 2];
    }

    fyFor(date) {
        const y = this.years.find((y) => y.date_from <= date && date <= y.date_to);
        return y && y.fy;
    }

    get label() {
        const { dateFrom, dateTo, asOf } = this.props;
        if (!asOf && dateFrom <= ALL_FROM) {
            // a drill from a balance "as at" a date: every entry up to that date
            return dateTo && dateTo < ALL_TO ? `All dates to ${this.fmt(dateTo)}` : "All dates";
        }
        const months = this.years.flatMap((y) => y.months);
        if (asOf) {
            const m = months.find((m) => m.date_to === dateTo);
            return `As at ${m ? "end of " + m.name : this.fmt(dateTo)}`;
        }
        const a = months.find((m) => m.date_from === dateFrom);
        const b = months.find((m) => m.date_to === dateTo);
        const y = this.years.find((y) => y.date_from === dateFrom && y.date_to === dateTo);
        if (y) {
            return y.name;
        }
        if (a && b) {
            return a.id === b.id ? a.name : `${a.name.split(" ")[0]} – ${b.name}`;
        }
        return `${this.fmt(dateFrom)} – ${this.fmt(dateTo)}`;
    }

    fmt(d) {
        if (!d) {
            return "";
        }
        const [y, m, dd] = d.split("-");
        return new Date(+y, +m - 1, +dd).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
    }

    isSelected(m) {
        return this.props.asOf ? m.date_to === this.props.dateTo
            : m.date_from >= this.props.dateFrom && m.date_to <= this.props.dateTo;
    }

    isCurrent(m) {
        const t = this.props.periods.today;
        return m.date_from <= t && t <= m.date_to;
    }

    pick(m, ev) {
        if (this.props.asOf) {
            this.props.onChange(this.props.dateFrom, m.date_to);
            this.state.open = false;
            return;
        }
        if (ev && ev.shiftKey && this.state.anchor) {
            const a = this.state.anchor;
            const from = a.date_from < m.date_from ? a.date_from : m.date_from;
            const to = a.date_to > m.date_to ? a.date_to : m.date_to;
            this.props.onChange(from, to);
            this.state.anchor = null;
            this.state.open = false;
            return;
        }
        this.state.anchor = m;
        this.props.onChange(m.date_from, m.date_to);
    }

    pickYear() {
        const y = this.year;
        this.props.onChange(this.props.asOf ? this.props.dateFrom : y.date_from, y.date_to);
        this.state.open = false;
    }

    pickQuarter(q) {
        this.props.onChange(this.props.asOf ? this.props.dateFrom : q.date_from, q.date_to);
        this.state.open = false;
    }

    chip(key) {
        const p = this.props.periods;
        const months = this.years.flatMap((y) => y.months);
        const cur = p.current_period || months[0];
        const idx = months.findIndex((m) => m.id === cur.id);
        const fy = this.years.find((y) => y.fy === p.current_fy);
        const lastFy = this.years.find((y) => y.fy === p.current_fy - 1);
        let from, to;
        if (key === "this_month") {
            [from, to] = [cur.date_from, cur.date_to];
        } else if (key === "last_month") {
            const m = months[Math.max(0, idx - 1)];
            [from, to] = [m.date_from, m.date_to];
        } else if (key === "this_quarter") {
            const q = fy.quarters.find((q) => q.date_from <= p.today && p.today <= q.date_to) || fy.quarters[0];
            [from, to] = [q.date_from, q.date_to];
        } else if (key === "ytd") {
            [from, to] = [fy.date_from, p.today];
        } else if (key === "this_fy") {
            [from, to] = [fy.date_from, fy.date_to];
        } else if (key === "last_fy" && lastFy) {
            [from, to] = [lastFy.date_from, lastFy.date_to];
        } else if (key === "all") {
            [from, to] = [ALL_FROM, ALL_TO];
        }
        if (this.props.asOf) {
            from = this.props.dateFrom;
            if (key === "ytd") {
                to = p.today;
            }
        }
        this.props.onChange(from, to);
        this.state.open = false;
    }

    isChip(key) {
        return false;
    }

    onDate(field, ev) {
        const v = ev.target.value;
        if (!v) {
            return;
        }
        if (field === "from") {
            this.props.onChange(v, this.props.dateTo);
        } else {
            this.props.onChange(this.props.asOf ? this.props.dateFrom : this.props.dateFrom, v);
        }
    }
}
