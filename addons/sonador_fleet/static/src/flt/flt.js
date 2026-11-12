import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SbChart } from "@sonador_books/common/chart";

const RISK = { ok: "OK", watch: "Watch", alert: "Alert", theft: "Possible theft" };
const FSTATE = { request: "Requested", approved: "Approved", filled: "Filled", rejected: "Rejected" };
const TSTATE = { planned: "Planned", out: "On the road", back: "Back", cancel: "Cancelled" };
const PSTATE = { none: "", requested: "Per diem asked", approved: "Per diem approved", paid: "Per diem paid" };
const DSTATE = { ok: "Valid", soon: "Expires soon", expired: "Expired", history: "Replaced", none: "No date" };

/** Fleet: dashboard, vehicles, fuel, trips, maintenance, costs, settings, and the driver's own page. */
export class FleetApp extends Component {
    static template = "sonador_fleet.App";
    static components = { SbChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        const p = (this.props.action && this.props.action.params) || {};
        this.state = useState({
            ctx: null, page: p.page || "home", busy: false,
            dash: null, my: null, vlist: null, vf: { state: "", search: "", archived: false },
            veh: null, vid: null, tab: "overview", vedit: null,
            fuel: null, ff: { state: "", risk: "", vehicle_id: "" }, fedit: null, rejectNote: "",
            trips: null, tf: { state: "", perdiem: "", vehicle_id: "" }, tedit: null, gate: null,
            maint: null, sedit: null, schedEdit: null, docEdit: null, hits: [],
            costs: null, cf: { from: "", to: "" }, set: null,
        });
        onWillStart(async () => {
            this.state.ctx = await this.orm.call("sb.fleet", "ctx", []);
            if (this.state.page === "home") {
                this.state.page = this.ctx.is_officer ? "dashboard" : "my";
            }
            const t = this.ctx.today;
            this.state.cf = { from: t.slice(0, 8) + "01", to: t };
            await this.load();
        });
    }
    get ctx() {
        return this.state.ctx;
    }
    money(v) {
        return v === null || v === undefined || v === "" ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }
    num(v, d = 1) {
        return v === null || v === undefined || v === "" ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });
    }
    riskLabel(r) {
        return RISK[r] || r;
    }
    fState(s) {
        return FSTATE[s] || s;
    }
    tState(s) {
        return TSTATE[s] || s;
    }
    pState(s) {
        return PSTATE[s] || "";
    }
    dState(s) {
        return DSTATE[s] || s;
    }
    sel(list, id) {
        const x = (list || []).find((i) => i.id === id);
        return x ? x.name : id;
    }
    async go(page, extra) {
        Object.assign(this.state, { page, fedit: null, tedit: null, sedit: null, vedit: null, docEdit: null, schedEdit: null, gate: null }, extra || {});
        await this.load();
    }
    async load() {
        const s = this.state;
        s.busy = true;
        try {
            if (s.page === "dashboard") {
                s.dash = await this.orm.call("sb.fleet", "dashboard", []);
            } else if (s.page === "my") {
                s.my = await this.orm.call("sb.fleet", "my", []);
            } else if (s.page === "vehicles") {
                s.vlist = await this.orm.call("sb.fleet", "vehicles", [s.vf]);
            } else if (s.page === "vehicle" && s.vid) {
                s.veh = await this.orm.call("sb.fleet", "vehicle", [s.vid]);
            } else if (s.page === "fuel") {
                s.fuel = await this.orm.call("sb.fleet", "fuel_list", [s.ff]);
            } else if (s.page === "trips") {
                s.trips = await this.orm.call("sb.fleet", "trips", [s.tf]);
            } else if (s.page === "maintenance") {
                s.maint = await this.orm.call("sb.fleet", "maintenance", [{}]);
            } else if (s.page === "costs") {
                s.costs = await this.orm.call("sb.fleet", "costs", [s.cf.from, s.cf.to]);
            } else if (s.page === "settings") {
                s.set = await this.orm.call("sb.fleet", "settings", []);
            }
        } finally {
            s.busy = false;
        }
    }
    async reloadCtx() {
        this.state.ctx = await this.orm.call("sb.fleet", "ctx", []);
    }
    async setFilter(key, k, v) {
        this.state[key][k] = v;
        await this.load();
    }

    // ------------------------------------------------------------------ vehicles
    async openVehicle(id, tab) {
        Object.assign(this.state, { vid: id, tab: tab || "overview" });
        await this.go("vehicle");
    }
    newVehicle() {
        this.state.page = "vehicle";
        this.state.vid = null;
        this.state.veh = null;
        this.state.vedit = { plate: "", make: "", model: "", year: "", kind: "pickup", ownership: "owned", state: "active", fuel: "diesel", tank: "",
            target_kml: "", odometer: "", driver_id: "", user_ids: [], chassis: "", engine: "", colour: "", seats: "", capacity: "", acquired: "",
            value: "", charge_rate_km: "", gps_imei: "", fuel_sensor: false, note: "" };
    }
    editVehicle() {
        const v = this.state.veh;
        this.state.vedit = { plate: v.plate, make: v.make, model: v.model, year: v.year, kind: v.kind, ownership: v.ownership, state: v.state, fuel: v.fuel,
            tank: v.tank, target_kml: v.target_kml, odometer: v.odometer, driver_id: v.driver_id, user_ids: [...v.user_ids], chassis: v.chassis,
            engine: v.engine, colour: v.colour, seats: v.seats, capacity: v.capacity, acquired: v.acquired || "", value: v.value,
            charge_rate_km: v.charge_rate_km, gps_imei: v.gps_imei, fuel_sensor: v.fuel_sensor, note: v.note, active: v.active };
    }
    setV(k, v) {
        this.state.vedit[k] = v;
    }
    toggleUser(id) {
        const m = this.state.vedit.user_ids;
        const i = m.indexOf(id);
        if (i >= 0) {
            m.splice(i, 1);
        } else {
            m.push(id);
        }
    }
    async saveVehicle() {
        const v = await this.orm.call("sb.fleet", "save_vehicle", [this.state.vid || false, this.state.vedit]);
        Object.assign(this.state, { vid: v.id, veh: v, vedit: null });
        await this.reloadCtx();
        this.notification.add(`${v.plate} saved.`, { type: "success" });
    }
    cancelVehicle() {
        if (!this.state.vid) {
            this.go("vehicles");
        }
        this.state.vedit = null;
    }
    async archiveVehicle(active) {
        await this.orm.call("sb.fleet", "save_vehicle", [this.state.vid, { active }]);
        await this.reloadCtx();
        await this.go("vehicles");
    }
    get monthChart() {
        const m = this.state.veh.months;
        return { labels: m.map((x) => x.label), series: [{ label: "Fuel", data: m.map((x) => x.fuel) }, { label: "Maintenance", data: m.map((x) => x.maintenance) },
            { label: "Other", data: m.map((x) => x.other) }] };
    }

    // documents
    editDoc(d) {
        this.state.docEdit = d ? { ...d, date_start: d.start || "", date_end: d.end || "" } : { id: false, kind: "bolo", number: "", partner_id: "", partner: "",
            date_start: this.ctx.today, date_end: "", amount: "", note: "" };
    }
    async findPartner(target, v) {
        this.state[target].partner = v;
        this.state[target].partner_id = "";
        this.state.hits = v.length > 1 ? await this.orm.call("sb.fleet", "partners", [v]) : [];
    }
    pickPartner(target, p) {
        Object.assign(this.state[target], { partner_id: p.id, partner: p.name });
        this.state.hits = [];
    }
    async saveDoc() {
        const d = { ...this.state.docEdit };
        delete d.partner;
        this.state.veh = await this.orm.call("sb.fleet", "save_doc", [this.state.vid, d]);
        this.state.docEdit = null;
    }
    async removeDoc(d) {
        if (window.confirm(`Delete this ${d.kind_name}?`)) {
            this.state.veh = await this.orm.call("sb.fleet", "remove_doc", [d.id]);
        }
    }
    // schedules
    editSched(s) {
        this.state.schedEdit = s ? { ...s } : { id: false, name: "Oil and filter change", every_km: 5000, every_months: 6, last_km: this.state.veh.odometer, last_date: this.ctx.today };
    }
    async saveSched() {
        this.state.veh = await this.orm.call("sb.fleet", "save_schedule", [this.state.vid, this.state.schedEdit]);
        this.state.schedEdit = null;
    }
    async removeSched(s) {
        if (window.confirm(`Stop the schedule "${s.name}"?`)) {
            this.state.veh = await this.orm.call("sb.fleet", "remove_schedule", [s.id]);
            this.state.schedEdit = null;
        }
    }

    // ------------------------------------------------------------------ fuel
    vehicleInfo(id) {
        return this.ctx.vehicles.find((v) => v.id === Number(id)) || {};
    }
    newFuel(mode, vehicleId) {
        const vid = vehicleId || (this.ctx.vehicles[0] || {}).id || "";
        const v = this.vehicleInfo(vid);
        this.state.fedit = { id: false, mode, vehicle_id: vid, driver_id: this.ctx.me || v.driver_id || "", liters_req: "", level_now: "", reason: "",
            date: this.ctx.now, liters: "", price: this.ctx.prices[v.fuel] || "", amount: "", odometer: "", full: true, station: "", pay: "telebirr",
            receipt: "", gps_liters: "" };
    }
    fillRequest(f) {
        const v = this.vehicleInfo(f.vehicle_id);
        this.state.fedit = { id: f.id, mode: "fill", vehicle_id: f.vehicle_id, driver_id: f.driver_id, liters_req: f.liters_req, level_now: f.level_now,
            date: this.ctx.now, liters: f.liters_req, price: this.ctx.prices[v.fuel] || "", amount: "", odometer: "", full: true, station: "",
            pay: "telebirr", receipt: "", gps_liters: "" };
    }
    editFuel(f) {
        this.state.fedit = { ...f, mode: f.state === "filled" ? "fill" : "request" };
    }
    setFu(k, v) {
        const e = this.state.fedit;
        e[k] = v;
        if (k === "vehicle_id") {
            e.price = this.ctx.prices[this.vehicleInfo(v).fuel] || e.price;
        }
        if (k === "liters" || k === "price") {
            e.amount = Math.round((Number(e.liters) || 0) * (Number(e.price) || 0) * 100) / 100 || "";
        }
    }
    async saveFuel() {
        const r = await this.orm.call("sb.fleet", "save_fuel", [this.state.fedit]);
        this.state.fedit = null;
        this.afterFuel(r);
        await this.load();
    }
    afterFuel(r) {
        if (r.risk === "ok") {
            this.notification.add(`${r.name} saved.`, { type: "success" });
        } else {
            this.notification.add(`${r.name}: ${r.flags.map((f) => f.msg).join(" ")}`, { type: r.risk === "watch" ? "warning" : "danger", sticky: r.risk === "theft" });
        }
    }
    async verify(f) {
        const r = await this.orm.call("sb.fleet", "verify_receipt", [f.id]);
        this.notification.add(`Receipt: ${this.money(r.receipt_amount)} ${this.ctx.currency} to ${r.receipt_payee || "?"}${r.receipt_plate ? ", plate " + r.receipt_plate : ""}.`, { type: "info" });
        this.afterFuel(r);
        await this.load();
    }
    async decideFuel(f, approve) {
        if (!approve && !this.state.rejectNote) {
            this.notification.add("Write why it is refused in the box first.", { type: "warning" });
            return;
        }
        await this.orm.call("sb.fleet", "decide_fuel", [[f.id], approve, this.state.rejectNote]);
        this.state.rejectNote = "";
        await this.load();
    }
    async removeFuel(f) {
        if (window.confirm(`Delete ${f.name}?`)) {
            await this.orm.call("sb.fleet", "remove_fuel", [f.id]);
            await this.load();
        }
    }

    // ------------------------------------------------------------------ trips
    newTrip(vehicleId) {
        const vid = vehicleId || (this.ctx.vehicles[0] || {}).id || "";
        const v = this.vehicleInfo(vid);
        this.state.tedit = { id: false, action: "out", vehicle_id: vid, driver_id: this.ctx.me || v.driver_id || "", purpose: "", origin: "", destination: "",
            cargo: "", out_at: this.ctx.now, odo_out: v.odometer || "", gate_by: "", job_id: "" };
    }
    backTrip(t) {
        this.state.tedit = { ...t, action: "back", back_at: this.ctx.now, odo_in: "" };
    }
    perdiemTrip(t) {
        const days = t.hours ? Math.max(1, Math.ceil(t.hours / 24)) : 1;
        this.state.tedit = { ...t, action: "perdiem", days: t.days || days };
    }
    editTrip(t) {
        this.state.tedit = { ...t, action: "save" };
    }
    setT(k, v) {
        const e = this.state.tedit;
        e[k] = v;
        if (k === "vehicle_id" && e.action === "out") {
            e.odo_out = this.vehicleInfo(v).odometer || "";
        }
    }
    async saveTrip() {
        const e = this.state.tedit;
        let r;
        if (e.action === "perdiem") {
            r = await this.orm.call("sb.fleet", "request_perdiem", [e.id, e.days, e.note || ""]);
            this.notification.add(`Per diem ${this.money(r.perdiem)} ${this.ctx.currency} asked.`, { type: "success" });
        } else {
            r = await this.orm.call("sb.fleet", "save_trip", [e]);
            this.notification.add(e.action === "out" ? `${r.name}: on the road.` : `${r.name} saved.`, { type: "success" });
        }
        this.state.tedit = null;
        await this.reloadCtx();
        if (e.action === "out") {
            this.state.gate = r;
        }
        await this.load();
    }
    async cancelTrip(t) {
        if (window.confirm(`Cancel ${t.name}?`)) {
            await this.orm.call("sb.fleet", "save_trip", [{ id: t.id, action: "cancel" }]);
            await this.load();
        }
    }
    async decidePerdiem(t, approve) {
        const r = await this.orm.call("sb.fleet", "decide_perdiem", [t.id, approve]);
        this.notification.add(approve ? `Approved: draft payment of ${this.money(r.perdiem)} ${this.ctx.currency} in Accounting.` : "Sent back.", { type: "success" });
        await this.load();
    }
    showGate(t) {
        this.state.gate = t;
    }
    printGate() {
        window.print();
    }
    openVoucher(v) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "sb.voucher", res_id: v.id, views: [[false, "form"]] });
    }

    // ------------------------------------------------------------------ maintenance
    newService(vehicleId, sched) {
        const vid = vehicleId || (this.ctx.vehicles[0] || {}).id || "";
        this.state.sedit = { id: false, vehicle_id: vid, kind: "service", state: "open", date: this.ctx.today, odometer: this.vehicleInfo(vid).odometer || "",
            partner_id: "", partner: "", description: sched ? sched.name : "", amount: "", schedule_id: sched ? sched.id : "" };
    }
    editService(s) {
        this.state.sedit = { ...s };
    }
    setS(k, v) {
        this.state.sedit[k] = v;
    }
    async saveService() {
        const e = { ...this.state.sedit };
        delete e.partner;
        await this.orm.call("sb.fleet", "save_service", [e]);
        this.state.sedit = null;
        await this.reloadCtx();
        await this.load();
    }

    // ------------------------------------------------------------------ costs and settings
    async setCost(k, v) {
        this.state.cf[k] = v;
        await this.load();
    }
    exportCosts() {
        const head = ["Vehicle", "Plate", "Fuel", "Litres", "Maintenance", "Documents", "Per diem", "Other", "Total", "km", "Cost per km"];
        const rows = this.state.costs.rows.map((r) => [r.name, r.plate, r.fuel, r.liters, r.maintenance, r.documents, r.perdiem, r.other, r.total, r.km, r.per_km]);
        const csv = [head, ...rows].map((l) => l.map((v) => `"${String(v === null || v === undefined ? "" : v).replace(/"/g, '""')}"`).join(",")).join("\n");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        a.download = "fleet-costs.csv";
        a.click();
    }
    setSet(k, v) {
        this.state.set[k] = v;
    }
    async saveSettings() {
        this.state.set = await this.orm.call("sb.fleet", "save_settings", [this.state.set]);
        await this.reloadCtx();
        this.notification.add("Saved.", { type: "success" });
    }
    async saveDriver(d) {
        this.state.set = await this.orm.call("sb.fleet", "save_driver", [d]);
        this.notification.add(`${d.name} saved.`, { type: "success" });
    }
}

registry.category("actions").add("sonador_fleet.app", FleetApp);
