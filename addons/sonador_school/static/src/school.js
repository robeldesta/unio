/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATUSES = [
    ["present", "Present", "P"],
    ["absent", "Absent", "A"],
    ["late", "Late", "L"],
    ["excused", "Excused", "E"],
];

/** School: overview, students and admissions, classes (attendance, marks, results), fees and family accounts. */
export class SchoolApp extends Component {
    static template = "sonador_school.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.statuses = STATUSES;
        this.state = useState({
            c: null, tab: "overview", busy: false,
            ov: null,
            students: [], search: "", filter: "enrolled", sectionFilter: "", student: null, form: null,
            classes: null, sec: null, mode: "register", day: null, reg: null, termId: null, subjectId: null, marks: null, res: null, editSection: null,
            fees: null, arrears: [], arrearsSearch: "", family: null, payForm: null, run: null,
            setup: null,
        });
        onWillStart(() => this.boot());
    }
    call(method, args = []) {
        return this.orm.call("school.service", method, args);
    }
    async boot() {
        const c = await this.call("ctx");
        this.state.c = c;
        if (c.setup) {
            this.state.tab = "setup";
            this.state.setup = { name: "", start: "", end: "", grades: {}, per: 1 };
            return;
        }
        this.state.termId = c.term_id;
        this.state.day = c.today;
        await this.go(c.office ? "overview" : "classes");
    }
    async run(fn) {
        this.state.busy = true;
        try {
            return await fn();
        } finally {
            this.state.busy = false;
        }
    }
    money(v) {
        return (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    barW(n) {
        const top = Math.max(1, ...this.state.ov.grades.map((x) => x.m + x.f));
        return (100 * n) / top;
    }
    whole(v) {
        return Math.round(v || 0).toLocaleString();
    }
    async go(tab) {
        this.state.tab = tab;
        if (tab === "overview") this.state.ov = await this.call("overview");
        if (tab === "students") await this.loadStudents();
        if (tab === "classes") {
            this.state.classes = await this.call("classes");
            if (!this.state.sec && this.state.classes.sections.length) await this.openSection(this.state.classes.sections[0].id);
        }
        if (tab === "fees") {
            const [f, a] = await Promise.all([this.call("fees"), this.call("arrears", [this.state.arrearsSearch])]);
            this.state.fees = f;
            this.state.arrears = a;
        }
    }

    async gotoFamily(id) {
        await this.go("fees");
        await this.openFamily(id);
    }
    async gotoStudent(id) {
        await this.go("students");
        await this.openStudent(id);
    }

    // ------------------------------------------------------------- setup
    toggleGrade(id) {
        this.state.setup.grades[id] = !this.state.setup.grades[id];
    }
    async startYear() {
        const s = this.state.setup;
        const ids = Object.keys(s.grades).filter((k) => s.grades[k]);
        if (!s.name || !s.start || !s.end || !ids.length) {
            this.notification.add("Name the year, give its dates and tick the grades you teach.", { type: "warning" });
            return;
        }
        await this.run(async () => {
            await this.call("start_year", [s.name, s.start, s.end, ids, s.per]);
            await this.boot();
        });
    }
    get allGrades() {
        return (this.state.c && this.state.c.grades) || [];
    }

    // ------------------------------------------------------------- students
    async loadStudents() {
        this.state.students = await this.call("students", [this.state.search, this.state.sectionFilter || false, this.state.filter]);
    }
    async setFilter(f) {
        this.state.filter = f;
        await this.loadStudents();
    }
    onSearch(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this._t);
        this._t = setTimeout(() => this.loadStudents(), 250);
    }
    async openStudent(id) {
        this.state.form = null;
        this.state.student = await this.call("student", [id]);
    }
    newStudent() {
        this.state.student = null;
        this.state.form = { name: "", name_am: "", sex: "m", birth_date: "", applied_grade_id: "", section_id: "", payer_name: "", payer_phone: "",
            payer_id: false, second_guardian: "", second_phone: "", scholarship_pct: 0, transport: false, medical: "", previous_school: "", state: "applicant" };
    }
    editStudent() {
        const s = this.state.student;
        this.state.form = { id: s.id, name: s.name, name_am: s.name_am, sex: s.sex, birth_date: s.birth_date || "", applied_grade_id: s.applied_grade_id || "",
            section_id: s.section_id || "", payer_id: s.payer_id, payer_name: s.payer, payer_phone: s.phone, second_guardian: s.second_guardian,
            second_phone: s.second_phone, scholarship_pct: s.scholarship, transport: s.transport, medical: s.medical, previous_school: s.previous_school };
    }
    async saveStudent() {
        const f = { ...this.state.form };
        if (!f.name || (!f.payer_id && !f.payer_name)) {
            this.notification.add("Give the student's name and the parent who pays.", { type: "warning" });
            return;
        }
        f.scholarship_pct = parseFloat(f.scholarship_pct) || 0;
        if (!f.id) delete f.id;
        await this.run(async () => {
            this.state.student = await this.call("save_student", [f]);
            this.state.form = null;
            await this.loadStudents();
            this.notification.add("Saved.", { type: "success" });
        });
    }
    async setState(state, sectionId) {
        await this.run(async () => {
            this.state.student = await this.call("set_state", [this.state.student.id, state, sectionId || false, ""]);
            await this.loadStudents();
        });
    }
    stateLabel(s) {
        return { applicant: "Applicant", admitted: "Admitted", enrolled: "Enrolled", left: "Left", graduated: "Graduated" }[s] || s;
    }
    sectionsOf(gradeId) {
        return this.state.c.sections.filter((s) => !gradeId || s.grade_id === parseInt(gradeId));
    }

    // ------------------------------------------------------------- classes
    async openSection(id) {
        this.state.sec = id;
        await this.openMode(this.state.mode);
    }
    get section() {
        return this.state.classes && this.state.classes.sections.find((s) => s.id === this.state.sec);
    }
    async openMode(mode) {
        this.state.mode = mode;
        const sec = this.state.sec;
        if (!sec) return;
        if (mode === "register") this.state.reg = await this.call("register", [sec, this.state.day]);
        if (mode === "marks") {
            const first = await this.call("marks", [sec, this.state.termId, this.state.subjectId || 0]);
            if (!this.state.subjectId || !first.subjects.find((x) => x.id === this.state.subjectId)) {
                this.state.subjectId = first.subjects.length ? first.subjects[0].id : null;
                this.state.marks = this.state.subjectId ? await this.call("marks", [sec, this.state.termId, this.state.subjectId]) : first;
            } else {
                this.state.marks = first;
            }
        }
        if (mode === "results") this.state.res = await this.call("results", [sec, this.state.termId]);
    }
    async setDay(ev) {
        this.state.day = ev.target.value;
        await this.openMode("register");
    }
    async setTerm(ev) {
        this.state.termId = parseInt(ev.target.value);
        await this.openMode(this.state.mode);
    }
    async setSubject(id) {
        this.state.subjectId = id;
        await this.openMode("marks");
    }
    mark(row, status) {
        row.status = status;
    }
    allPresent() {
        for (const r of this.state.reg.rows) r.status = "present";
    }
    get regCounts() {
        const out = { present: 0, absent: 0, late: 0, excused: 0 };
        for (const r of (this.state.reg && this.state.reg.rows) || []) out[r.status]++;
        return out;
    }
    async saveRegister() {
        await this.run(async () => {
            this.state.reg = await this.call("save_register", [this.state.sec, this.state.day, this.state.reg.rows]);
            this.notification.add("Register saved.", { type: "success" });
        });
    }
    rowTotal(row) {
        let t = 0, any = false;
        for (const k in row.scores) {
            const v = row.scores[k];
            if (v !== null && v !== "" && v !== undefined) {
                t += parseFloat(v) || 0;
                any = true;
            }
        }
        return any ? Math.round(t * 10) / 10 : "";
    }
    setScore(row, aid, ev) {
        const v = ev.target.value.trim();
        row.scores[aid] = v === "" ? null : v;
    }
    async saveMarks() {
        await this.run(async () => {
            this.state.marks = await this.call("save_marks", [this.state.sec, this.state.termId, this.state.subjectId, this.state.marks.rows]);
            this.notification.add("Marks saved.", { type: "success" });
        });
    }
    async saveConduct() {
        await this.run(async () => {
            await this.call("save_conduct", [this.state.termId, this.state.res.rows.map((r) => ({ id: r.id, conduct: r.conduct, comment: r.comment }))]);
            this.notification.add("Saved.", { type: "success" });
        });
    }
    printCards() {
        window.open(this.state.res.pdf, "_blank");
    }
    printOne(id) {
        window.open(`/report/pdf/sonador_school.report_card_document/${id}?term=${this.state.termId}`, "_blank");
    }
    editSection(s) {
        this.state.editSection = s ? { id: s.id, name: s.section, room: s.room, capacity: s.capacity, homeroom_id: s.homeroom_id || "" }
            : { name: "", room: "", capacity: 40, homeroom_id: "", grade_id: this.allGrades.length ? this.allGrades[0].id : "" };
    }
    async saveSection() {
        await this.run(async () => {
            this.state.classes = await this.call("save_section", [this.state.editSection]);
            this.state.editSection = null;
            this.state.c = await this.call("ctx");
        });
    }

    // ------------------------------------------------------------- fees
    async searchArrears(ev) {
        this.state.arrearsSearch = ev.target.value;
        clearTimeout(this._a);
        this._a = setTimeout(async () => (this.state.arrears = await this.call("arrears", [this.state.arrearsSearch])), 250);
    }
    async openFamily(id) {
        this.state.payForm = null;
        this.state.family = await this.call("family", [id]);
    }
    startPay() {
        const f = this.state.fees;
        this.state.payForm = { amount: this.state.family.owed || "", account_id: f.banks.length ? f.banks[0].id : "", reference: "", day: this.state.c.today };
    }
    async pay() {
        const p = this.state.payForm;
        await this.run(async () => {
            const r = await this.call("pay", [this.state.family.id, p.amount, p.account_id, p.reference, p.day]);
            this.state.family = r.family;
            this.state.payForm = null;
            this.state.arrears = await this.call("arrears", [this.state.arrearsSearch]);
            this.notification.add(`Receipt ${r.receipt} posted.` + (r.unapplied > 0 ? ` ${this.money(r.unapplied)} is held as credit.` : ""), { type: "success" });
        });
    }
    startRun() {
        this.state.run = { monthly: true, term: false, annual: false, period: "", day: this.state.c.today, due: "" };
    }
    async doRun() {
        const r = this.state.run;
        const kinds = ["monthly", "term", "annual"].filter((k) => r[k]);
        if (!r.period || !kinds.length) {
            this.notification.add("Name the period (for example Tikimt 2019) and pick the fees to bill.", { type: "warning" });
            return;
        }
        await this.run(async () => {
            const out = await this.call("bill_run", [kinds, r.period, r.day, r.due || r.day]);
            this.state.fees = out.fees;
            this.state.run = null;
            this.state.arrears = await this.call("arrears", [this.state.arrearsSearch]);
            this.notification.add(out.bills ? `${out.bills} bills, ${this.money(out.total)} in total.` : "Nobody left to bill for this period.", { type: "success" });
        });
    }
    async saveFee(item, gradeId, ev) {
        const v = parseFloat(ev.target.value) || 0;
        if (v === item.amounts[String(gradeId)]) return;
        this.state.fees = await this.call("save_fee", [item.id, gradeId, v]);
    }
    kindLabel(k) {
        return { monthly: "Monthly", term: "Each semester", annual: "Once a year", once: "Once" }[k] || k;
    }
}

registry.category("actions").add("sonador_school.app", SchoolApp);
