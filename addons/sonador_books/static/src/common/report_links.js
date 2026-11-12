import { download } from "@web/core/network/download";

/** Build the client-action context for a Report Center catalog entry. */
export function reportContext(entry) {
    const options = { ...(entry.options || {}), _title: entry.name };
    const ui = { ...(entry.ui || {}) };
    if (options.mode) {
        ui.mode = options.mode;
        delete options.mode;
    }
    return { report: entry.report, options, ui, catalog_key: entry.custom ? false : entry.key, preset: true };
}

export function openReport(action, entry) {
    return action.doAction({
        type: "ir.actions.client",
        tag: "sonador_books.report",
        name: entry.name,
        context: reportContext(entry),
    });
}

/** Export a catalog entry for the current period (fiscal year for 'fy' presets). */
export async function exportReport(orm, entry, fmt, inline = false) {
    const periods = await orm.call("sb.report", "get_periods", []);
    const ctx = reportContext(entry);
    const options = { ...ctx.options };
    if (!options.date_from) {
        const fy = periods.years.find((y) => y.fy === periods.current_fy);
        const range = ctx.ui.mode === "fy" ? fy : periods.current_period || fy;
        options.date_from = range.date_from;
        options.date_to = range.date_to;
    }
    const data = { report: entry.report, fmt, options: JSON.stringify(options), expanded: "[]" };
    if (inline) {
        const form = document.createElement("form");
        form.method = "POST";
        form.action = "/sonador_books/report/export";
        form.target = "_blank";
        for (const [k, v] of Object.entries({ ...data, inline: 1, csrf_token: odoo.csrf_token })) {
            const i = document.createElement("input");
            i.type = "hidden";
            i.name = k;
            i.value = v;
            form.appendChild(i);
        }
        document.body.appendChild(form);
        form.submit();
        form.remove();
        return;
    }
    return download({ url: "/sonador_books/report/export", data });
}
