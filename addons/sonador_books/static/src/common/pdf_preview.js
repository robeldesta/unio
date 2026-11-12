import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { getReportUrl } from "@web/webclient/actions/reports/utils";

/** A printout opened in the browser's PDF viewer (look, then print or save), instead of only downloading it. */
export class SbPdfPreview extends Component {
    static template = "sonador_books.SbPdfPreview";
    static components = { Dialog };
    static props = ["url", "title", "close"];
}

// our printouts (vouchers, stock documents, receipts): preview first; other reports download as before
registry.category("ir.actions.report handlers").add("sonador_books.pdf_preview", async (action, options, env) => {
    if (action.report_type !== "qweb-pdf" || !(action.report_name || "").startsWith("sonador_")) {
        return false;
    }
    let url = getReportUrl(action, "pdf");
    if (!url.includes("?")) {
        url += `?context=${encodeURIComponent(JSON.stringify({ ...user.context, ...(action.context || {}) }))}`;
    }
    env.services.dialog.add(SbPdfPreview, { url, title: action.name || "" });
    return true;
});
