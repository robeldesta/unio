import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const STATUS = {
    present: "Present", late: "Late", absent: "Absent", leave: "On leave", holiday: "Public holiday", rest: "Rest day",
    missing: "No check-out", future: "", na: "", untracked: "Exempt",
};
const BOARD = { in: "In now", out: "Done for the day", notyet: "Not in yet", missing_in: "Not in (absent?)", nodata: "No machine record yet", leave: "On leave", holiday: "Holiday", rest: "Rest day" };

function hm(min) {
    min = Math.max(0, Math.round(min || 0));
    return `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, "0")}m`;
}

/** Attendance: clock in/out (location), who is in, timesheets, overtime approval, monthly report, kiosk, setup. */
export class AttendanceApp extends Component {
    static template = "sonador_attendance.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        const now = new Date();
        this.state = useState({
            ctx: null, page: p.page || "clock", busy: false, tick: Date.now(),
            me: null, locating: false, last: null,
            board: null, boardDept: "", boardFilter: "",
            month: this.iso(now), dept: "", search: "",
            grid: null, day: null, punch: { time: "", kind: "in", note: "" },
            ot: [], otState: "pending", otSel: {}, otNote: "",
            rep: null, warn: null,
            set: null, site: null, device: null, setTab: "people", peopleFilter: "", showUnmapped: false,
            kiosk: { code: "", res: null },
            pay: null, payPeriod: false, paySearch: "", payView: "days", otReason: "", year: null, person: null, yearSel: null, yview: "month", ymonth: 0,
        });
        this.timer = setInterval(() => (this.state.tick = Date.now()), 1000);
        onWillUnmount(() => {
            clearInterval(this.timer);
            clearTimeout(this.kioskReset);
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.att.service", "ctx", []);
            if (["board", "sheets", "overtime", "report"].includes(this.state.page) && !this.canTeam) {
                this.state.page = "clock";
            }
            if ((this.state.page === "setup" && !this.ctx.is_hr) || (this.state.page === "pay" && !this.ctx.is_hr && !this.ctx.is_ot_clerk)) {
                this.state.page = "clock";
            }
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    get canTeam() {
        return this.ctx.is_hr || this.ctx.is_manager;
    }
    iso(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    hm(m) {
        return hm(m);
    }
    statusLabel(s) {
        return STATUS[s] || s;
    }
    boardLabel(s) {
        return BOARD[s] || s;
    }
    async go(page) {
        this.state.page = page;
        this.state.day = null;
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "clock") {
                s.me = await this.orm.call("sb.att.service", "my_clock", []);
                s.year = s.me.none ? null : await this.orm.call("sb.att.service", "year", []);
            } else if (s.page === "board") {
                s.board = await this.orm.call("sb.att.service", "board", [s.boardDept || false]);
            } else if (s.page === "sheets") {
                s.grid = await this.orm.call("sb.att.service", "grid", [s.month, s.dept || false, s.search]);
            } else if (s.page === "overtime") {
                s.ot = await this.orm.call("sb.att.service", "overtime", [s.otState, s.otState === "pending" ? false : s.month]);
                s.otSel = {};
            } else if (s.page === "report") {
                s.rep = await this.orm.call("sb.att.service", "report", [s.month, s.dept || false]);
            } else if (s.page === "setup") {
                s.set = await this.orm.call("sb.att.service", "setup", []);
            } else if (s.page === "pay") {
                s.pay = await this.orm.call("sb.att.service", "month", [s.payPeriod || false]);
                s.payPeriod = s.pay.period;
            }
        } finally {
            s.busy = false;
        }
    }

    // ------------------------------------------------------------------ clock
    get elapsed() {
        const me = this.state.me;
        if (!me || !me.is_in || !me.since) {
            return "";
        }
        const sec = Math.max(0, Math.floor((this.state.tick - Date.parse(me.since)) / 1000));
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
    }
    get nowLabel() {
        return new Date(this.state.tick).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    }
    position() {
        return new Promise((resolve) => {
            if (!navigator.geolocation) {
                resolve(null);
                return;
            }
            navigator.geolocation.getCurrentPosition(
                (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, acc: Math.round(p.coords.accuracy || 0) }),
                () => resolve(null),
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
            );
        });
    }
    async clock() {
        if (this.state.locating) {
            return;
        }
        this.state.locating = true;
        try {
            const pos = await this.position();
            const mobile = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
            const res = await this.orm.call("sb.att.service", "clock", [pos ? pos.lat : 0, pos ? pos.lon : 0, pos ? pos.acc : 0, mobile]);
            this.state.me = res;
            const d = res.done;
            let msg = d.kind === "in" ? "Clocked in" : "Clocked out";
            msg += d.site ? ` at ${d.site}.` : pos ? "." : " (no location).";
            if (d.outside) {
                msg += ` You are ${d.distance.toLocaleString()} m from the nearest work site: HR sees this.`;
            }
            this.notification.add(msg, { type: d.outside ? "warning" : "success" });
        } finally {
            this.state.locating = false;
        }
    }
    weekBar(w) {
        const max = Math.max(9, ...this.state.me.week.map((x) => x.hours));
        return `height:${Math.max(3, (w.hours / max) * 100)}%`;
    }

    // ------------------------------------------------------------------ board
    async setBoardDept(v) {
        this.state.boardDept = v;
        await this.load();
    }
    get boardPeople() {
        const f = this.state.boardFilter;
        const ps = this.state.board.people;
        if (!f) {
            return ps;
        }
        if (f === "late") {
            return ps.filter((p) => p.late);
        }
        return ps.filter((p) => p.board === f);
    }
    boardCount(k) {
        return (this.state.board.counts || {})[k] || 0;
    }

    // ------------------------------------------------------------------ timesheets
    async shiftMonth(n) {
        // pay months (Tikimt 11 Oct - 9 Nov ...): the day before the first, or after the last, is in the next one
        const cur = this.state.page === "report" ? this.state.rep : this.state.grid;
        const d = new Date((n < 0 ? cur.month : cur.last) + "T12:00:00");
        d.setDate(d.getDate() + (n < 0 ? -1 : 1));
        this.state.month = this.iso(d);
        await this.load();
    }
    async setSearch(v) {
        this.state.search = v;
        await this.load();
    }
    otLabel(s) {
        return { pending: "To approve", approved: "Approved", refused: "Refused" }[s] || "";
    }
    get kioskDots() {
        const c = this.state.kiosk.code;
        return c ? "●".repeat(c.length) : "Enter your PIN";
    }
    async setDept(v) {
        this.state.dept = v;
        await this.load();
    }
    cellClass(c, d) {
        let k = `o_at_c ${c.s}`;
        if (d.sun || d.hol) {
            k += " off";
        }
        if (c.ots === "pending") {
            k += " otp";
        }
        if (c.abs === "unpaid") {
            k += " unpaid";
        }
        if (c.abs === "excused") {
            k += " excused";
        }
        return k;
    }
    cellText(c) {
        if (c.s === "present" || c.s === "late" || c.s === "missing") {
            return c.h ? c.h.toFixed(1) : c.in || "·";
        }
        return { absent: "A", leave: "L", holiday: "H", rest: "", future: "", na: "", untracked: "" }[c.s] || "";
    }
    cellTitle(c, d) {
        const parts = [`${d.wd} ${d.n} (${d.ec})`, this.statusLabel(c.s)];
        if (c.in) {
            parts.push(`in ${c.in}`);
        }
        if (c.h) {
            parts.push(`${c.h} h worked`);
        }
        if (c.late) {
            parts.push(`late ${c.late} min`);
        }
        if (c.ot) {
            parts.push(`overtime ${c.ot} h (${c.ots === "pending" ? "to approve" : c.ots})`);
        }
        if (d.hol) {
            parts.push(d.hol);
        }
        if (c.abs && c.s === "absent") {
            parts.push(c.abs);
        }
        return parts.join(" · ");
    }
    async openDay(employeeId, date) {
        this.state.day = await this.orm.call("sb.att.service", "day", [employeeId, date]);
        this.state.punch = { time: "", kind: "in", note: "" };
    }
    async closeDay() {
        this.state.day = null;
        await this.load();
    }
    async addPunch() {
        const d = this.state.day;
        const p = this.state.punch;
        if (!p.time) {
            this.notification.add("Enter the time.", { type: "warning" });
            return;
        }
        this.state.day = await this.orm.call("sb.att.service", "add_punch", [d.employee.id, d.date, p.time, p.kind, p.note]);
        this.state.punch = { time: "", kind: p.kind === "in" ? "out" : "in", note: "" };
    }
    async removePunch(id) {
        const d = this.state.day;
        this.state.day = await this.orm.call("sb.att.service", "remove_punch", [id]);
        if (!this.state.day) {
            await this.openDay(d.employee.id, d.date);
        }
    }
    async setAbsence(v) {
        const d = this.state.day;
        this.state.day = await this.orm.call("sb.att.service", "set_day", [d.employee.id, d.date, { absence: v }]);
    }
    async setNote(v) {
        const d = this.state.day;
        this.state.day = await this.orm.call("sb.att.service", "set_day", [d.employee.id, d.date, { note: v }]);
    }
    async decideDay(decision) {
        const d = this.state.day;
        await this.orm.call("sb.att.service", "decide_overtime", [[d.row.id], decision, ""]);
        await this.openDay(d.employee.id, d.date);
    }

    // ------------------------------------------------------------------ overtime
    async setOtState(v) {
        this.state.otState = v;
        await this.load();
    }
    toggleOt(id) {
        this.state.otSel[id] = !this.state.otSel[id];
    }
    get otChosen() {
        return Object.keys(this.state.otSel).filter((k) => this.state.otSel[k]).map(Number);
    }
    selectAllOt() {
        const all = this.state.ot.length && this.otChosen.length === this.state.ot.length;
        this.state.otSel = {};
        if (!all) {
            for (const r of this.state.ot) {
                if (r.employee_id !== this.ctx.me || this.ctx.is_hr) {
                    this.state.otSel[r.id] = true;
                }
            }
        }
    }
    async decide(decision, ids) {
        ids = ids || this.otChosen;
        if (!ids.length) {
            return;
        }
        const n = await this.orm.call("sb.att.service", "decide_overtime", [ids, decision, this.state.otNote]);
        this.notification.add(`${n} day(s) ${decision === "approve" ? "approved: payroll will pay them" : "refused"}.`, { type: "success" });
        this.state.otNote = "";
        await this.load();
    }
    get otTotals() {
        const t = { day: 0, night: 0, rest: 0, holiday: 0, total: 0 };
        for (const r of this.state.ot) {
            for (const k of Object.keys(t)) {
                t[k] += r[k];
            }
        }
        return t;
    }

    // ------------------------------------------------------------------ report
    get trendChart() {
        const t = this.state.rep.trend;
        return { labels: t.map((x) => x.label), series: [{ label: "Late arrivals", data: t.map((x) => x.late) }, { label: "Absent days", data: t.map((x) => x.absent) }] };
    }
    startWarn(r, kind) {
        this.state.warn = { id: r.id, name: r.name, kind, note: kind === "late" ?
            `You arrived late ${r.half_late} times in the six months to ${this.state.rep.label}. Under the Labour Proclamation 1156/2019 art. 27(1)(a), being late eight times in six months after a written warning is a ground for dismissal without notice.` :
            `You were absent without permission ${r.half_absent} days in the six months to ${this.state.rep.label}. Under the Labour Proclamation 1156/2019 art. 27(1)(b), five days of absence in six months after a written warning is a ground for dismissal without notice.` };
    }
    async saveWarn() {
        const w = this.state.warn;
        await this.orm.call("sb.att.service", "warn", [w.id, w.kind, w.note]);
        this.state.warn = null;
        this.notification.add("Written warning recorded on the person's timeline.", { type: "success" });
        await this.load();
    }
    exportCsv() {
        const r = this.state.rep;
        const head = ["Code", "Name", "Department", "Scheduled days", "Present", "Late (times)", "Late (min)", "Left early", "No check-out",
            "Absent", "Absent unpaid", "Absent excused", "Leave", "Hours worked", "Hours scheduled", "OT day h", "OT night h", "OT rest day h", "OT holiday h", "OT to approve h"];
        const rows = r.rows.map((x) => [x.code, x.name, x.department, x.scheduled, x.present, x.late, x.late_min, x.early, x.missing,
            x.absent, x.unpaid, x.excused, x.leave, x.hours, x.expected, x.ot_day, x.ot_night, x.ot_rest, x.ot_holiday, x.ot_wait]);
        const csv = [head, ...rows].map((l) => l.map((v) => `"${String(v === undefined || v === null ? "" : v).replace(/"/g, '""')}"`).join(",")).join("\n");
        const a = document.createElement("a");
        // with a byte-order mark Excel opens the names (Amharic too) correctly
        a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
        a.download = `attendance-${r.month}.csv`;
        a.click();
    }

    // ------------------------------------------------------------------ a year at a glance
    padStart(M) {
        return Array.from({ length: M.cells.length ? M.cells[0].dow : 0 }, (_x, i) => i);
    }
    dayNums(y) {
        return Array.from({ length: y.width }, (_x, i) => i + 1);
    }
    padCells(y, m) {
        return Array.from({ length: y.width - m.cells.length }, (_x, i) => i);
    }
    yearCols(y) {
        const cols = [];
        for (let i = 0; i < y.cells.length; i += 7) {
            cols.push(y.cells.slice(i, i + 7));
        }
        return cols;
    }
    get yearKinds() {
        return [["ontime", "Machine, on time"], ["late", "Machine, late"], ["ot", "Machine, with overtime"], ["manual", "Manual or field"],
            ["sick", "Sick leave"], ["annual", "Annual leave"], ["absent", "Absent"], ["off", "Sunday or holiday"]];
    }
    yearLabel(k) {
        const f = this.yearKinds.find((x) => x[0] === k);
        return f ? f[1] : { none: "No mark", out: "Not employed yet", future: "Still to come" }[k] || k;
    }
    yearTitle(c) {
        return `${c.ec} (${c.d}) — ${this.yearLabel(c.k)}${c.info ? ": " + c.info : ""}`;
    }
    pickDay(y, c) {
        if (!y.can_enter || ["out", "off", "future"].includes(c.k)) {
            return;
        }
        this.state.yearSel = { y, c, in: "08:00", out: "17:00", note: "" };
    }
    async _yearDone(res) {
        if (this.state.person && this.state.person.employee_id === res.employee_id) {
            this.state.person = res;
        } else {
            this.state.year = res;
        }
        this.state.yearSel = null;
    }
    async saveDay() {
        const S = this.state.yearSel;
        const res = await this.orm.call("sb.att.service", "hr_day", [S.y.employee_id, S.c.d, S.in, S.out, S.note]);
        await this._yearDone(res);
        this.notification.add("Entered by hand.", { type: "success" });
    }
    async clearDay() {
        const S = this.state.yearSel;
        await this._yearDone(await this.orm.call("sb.att.service", "hr_day_clear", [S.y.employee_id, S.c.d]));
        this.notification.add("Hand entry removed.", { type: "success" });
    }
    async openPerson(employeeId) {
        this.state.yearSel = null;
        this.state.yview = "month";
        this.state.ymonth = 0;
        this.state.person = await this.orm.call("sb.att.service", "year", [employeeId]);
    }

    // ------------------------------------------------------------------ days worked and the OT sheet
    get payCols() {
        if (this.state.payView === "ot") {
            return [
                ["ot_day", "Normal (h)", "Overtime hours on working days, 6 am – 10 pm (1.5×)"], ["ot_night", "Night (h)", "Overtime hours at night, 10 pm – 6 am (1.75×)"],
                ["ot_rest", "Weekend (h)", "Overtime hours on the weekly rest day (2×)"], ["ot_holiday", "Holiday (h)", "Overtime hours on public holidays (2.5×); at most 8 h for each holiday of the month"],
            ];
        }
        return [
            ["days_worked", "Days worked", "Working days up to today with a record, plus approved annual and sick leave (exempt: 30)"],
            ["paid_days", "Paid days", "Days worked + the Sundays and holidays so far, at most 30: payroll pays basic ÷ 30 × these"],
            ["annual_leave", "Annual leave", "Approved annual leave days"], ["sick_leave", "Sick leave", "Approved sick leave days"],
            ["other_leave", "Other leave", "Other approved leave days (unpaid leave is not in the days worked)"],
            ["absences", "Absent", "Working days with no record and no leave"], ["late_days", "Late (days)", "Days late (after the late-after time, or the start of work and the grace minutes)"],
            ["late_min", "Late (min)", "Minutes late in the month, all days added up"],
        ];
    }
    get payLines() {
        const q = (this.state.paySearch || "").toLowerCase();
        const src = this.state.payView === "ot" ? (this.state.otSource || "all") : "all";
        return this.state.pay.lines.filter((l) => (src === "all" || l.ot_source === src)
            && (!q || l.name.toLowerCase().includes(q) || (l.code || "").toLowerCase().includes(q) || (l.department || "").toLowerCase().includes(q)));
    }
    otApproval() {
        const src = this.state.otSource || "all";
        window.location.href = `/sonador_attendance/ot_approval_xlsx?period=${encodeURIComponent(this.state.pay.period)}&source=${encodeURIComponent(src)}`;
    }
    async openPay(period) {
        this.state.payPeriod = period;
        await this.load();
    }
    payCsv() {
        const P = this.state.pay;
        const head = ["Code", "Employee", "Department", ...this.payCols.map((c) => c[1]), "Attendance exempt"];
        const rows = P.lines.map((l) => [l.code, l.name, l.department, ...this.payCols.map((c) => l[c[0]]), l.exempt ? "Exempt" : ""]);
        const csv = [head, ...rows].map((l) => l.map((v) => `"${String(v === undefined || v === null ? "" : v).replace(/"/g, '""')}"`).join(",")).join("\n");
        const a = document.createElement("a");
        // with a byte-order mark Excel opens the names (Amharic too) correctly
        a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
        a.download = `${this.state.payView === "ot" ? "ot-sheet" : "days-worked"}-${P.period}.csv`;
        a.click();
    }

    otTemplate() {
        window.location.href = `/sonador_attendance/ot_template?period=${encodeURIComponent(this.state.pay.period)}`;
    }
    pickOtSheet() {
        document.querySelector(".o_at_otfile")?.click();
    }
    async uploadOtSheet(ev) {
        const f = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!f) {
            return;
        }
        const data = await new Promise((ok, ko) => {
            const rd = new FileReader();
            rd.onload = () => ok(String(rd.result).split(",")[1]);
            rd.onerror = ko;
            rd.readAsDataURL(f);
        });
        const res = await this.orm.call("sb.att.service", "ot_upload", [this.state.pay.period, data, f.name]);
        this.state.pay = res;
        const r = res.ot_result || {};
        this.notification.add(`OT sheet uploaded: ${r.people} people. It waits for HR approval.` +
            (r.unknown && r.unknown.length ? ` Not found, left out: ${r.unknown.join(", ")}` : ""),
            { type: r.unknown && r.unknown.length ? "warning" : "success", sticky: !!(r.unknown && r.unknown.length) });
    }
    async otDecide(approve) {
        const W = this.state.pay.ot_sheets.waiting;
        this.state.pay = await this.orm.call("sb.att.service", "ot_decide", [W.id, approve], { reason: this.state.otReason || "" });
        this.state.otReason = "";
        this.notification.add(approve ? "OT sheet approved: these hours are the month's overtime." : "OT sheet refused.",
            { type: approve ? "success" : "warning" });
    }
    payXlsx() {
        const P = this.state.pay;
        window.location.href = `/sonador_attendance/month_xlsx?period=${encodeURIComponent(P.period)}&view=${this.state.payView}`;
    }

    // ------------------------------------------------------------------ setup
    async saveRules() {
        this.state.set = await this.orm.call("sb.att.service", "save_rules", [this.state.set.rules]);
        this.state.ctx = await this.orm.call("sb.att.service", "ctx", []);
        this.notification.add("Rules saved.", { type: "success" });
    }
    setRule(k, v) {
        this.state.set.rules[k] = v;
    }
    editSite(s) {
        this.state.site = s ? { ...s } : { id: false, name: "", latitude: "", longitude: "", radius: 200 };
    }
    setSite(k, v) {
        this.state.site[k] = v;
    }
    async hereForSite() {
        const p = await this.position();
        if (!p) {
            this.notification.add("The browser did not give a location.", { type: "warning" });
            return;
        }
        this.state.site.latitude = p.lat.toFixed(7);
        this.state.site.longitude = p.lon.toFixed(7);
    }
    async saveSite() {
        this.state.set = await this.orm.call("sb.att.service", "save_site", [this.state.site]);
        this.state.site = null;
    }
    async deleteSite(id) {
        this.state.set = await this.orm.call("sb.att.service", "delete_site", [id]);
    }
    editDevice(d) {
        this.state.device = d ? { ...d } : { id: false, name: "", serial: "", code: "", site_id: "" };
    }
    setDevice(k, v) {
        this.state.device[k] = v;
    }
    async saveDevice() {
        this.state.set = await this.orm.call("sb.att.service", "save_device", [this.state.device]);
        this.state.device = null;
    }
    async deleteDevice(id) {
        this.state.set = await this.orm.call("sb.att.service", "delete_device", [id]);
    }
    async setPerson(p, k, v) {
        await this.orm.call("sb.att.service", "set_person", [p.id, { [k]: v }]);
        this.state.set = await this.orm.call("sb.att.service", "setup", []);
        this.notification.add(`${p.name}: saved.`, { type: "success" });
    }
    async setBio(p, deviceId, value) {
        await this.orm.call("sb.att.service", "set_person", [p.id, { bios: { [deviceId]: value } }]);
        this.state.set = await this.orm.call("sb.att.service", "setup", []);
        this.notification.add(`${p.name}: device number saved; punches re-linked.`, { type: "success" });
    }
    async mapBio(u, employeeId) {
        if (!employeeId) {
            return;
        }
        this.state.set = await this.orm.call("sb.att.service", "map_bio", [u.id, Number(employeeId)]);
        this.notification.add(`${u.device} user ${u.bio_id} linked; their punches are now on their timesheet.`, { type: "success" });
    }
    get setupPeople() {
        const f = (this.state.peopleFilter || "").toLowerCase();
        const ps = this.state.set.people;
        return f ? ps.filter((p) => p.name.toLowerCase().includes(f) || Object.values(p.bios).join(",").includes(f) || (p.department || "").toLowerCase().includes(f)) : ps;
    }
    copy(text) {
        navigator.clipboard.writeText(text);
        this.notification.add("Copied.", { type: "info" });
    }

    // ------------------------------------------------------------------ kiosk
    kioskKey(k) {
        const s = this.state.kiosk;
        if (k === "C") {
            s.code = "";
        } else if (k === "<") {
            s.code = s.code.slice(0, -1);
        } else if (s.code.length < 12) {
            s.code += k;
        }
    }
    async kioskGo() {
        const s = this.state.kiosk;
        try {
            s.res = await this.orm.call("sb.att.service", "kiosk", [s.code]);
        } finally {
            s.code = "";
        }
        clearTimeout(this.kioskReset);
        this.kioskReset = setTimeout(() => (this.state.kiosk.res = null), 5000);
    }
    openKiosk() {
        this.action.doAction("sonador_attendance.action_attendance_kiosk");
    }
}

registry.category("actions").add("sonador_attendance.app", AttendanceApp);
