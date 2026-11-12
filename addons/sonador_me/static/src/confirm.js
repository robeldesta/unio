/** @odoo-module **/
/**
 * Approval confirmations: before a step goes through, the server answers with the step's text instead
 * (exception ConfirmationRequired). Anywhere in the app, a dialog shows the text; the person ticks the box (and types
 * the phrase or their password when the step asks), the confirmation is recorded, then the same call runs again.
 * Me shows the text inline in the approval card instead (see me.js).
 */
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { NameAndSignature } from "@web/core/signature/name_and_signature";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export const CONFIRM_ERROR = "odoo.addons.sonador_me.models.confirm.ConfirmationRequired";

/** The step's text when an RPC error asks for a confirmation, else null. */
export function confirmationPayload(error) {
    const data = error && (error.data || (error.cause && error.cause.data));
    if (!data || data.name !== CONFIRM_ERROR) {
        return null;
    }
    return (data.arguments && data.arguments[1]) || null;
}

/** The drawn signature as base64 PNG, or false when the earlier one of this session is used. */
export function signatureOf(sig, reuse) {
    if (reuse || !sig.getSignatureImage || sig.isSignatureEmpty) {
        return false;
    }
    return sig.getSignatureImage();
}
export function signatureReady(sig, reuse) {
    return reuse || (sig.getSignatureImage && !sig.isSignatureEmpty);
}

export class ConfirmationDialog extends Component {
    static template = "sonador_me.ConfirmationDialog";
    static components = { Dialog, NameAndSignature };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.payload = this.props.data.arguments[1];
        this.state = useState({ ticked: false, phrase: "", password: "", busy: false, reuse: !!this.payload.reuse_signature });
        this.sig = useState({ name: "", isSignatureEmpty: true });
    }
    get ready() {
        const p = this.payload;
        if (!this.state.ticked || !signatureReady(this.sig, this.state.reuse)) {
            return false;
        }
        if (p.needs_phrase && this.state.phrase.trim().toUpperCase() !== (p.phrase || "").toUpperCase()) {
            return false;
        }
        return !(p.needs_password && !this.state.password);
    }
    money(v) {
        return (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    async confirm() {
        this.state.busy = true;
        try {
            await this.orm.call("sb.confirmation", "accept", [this.payload.token, true, this.state.phrase, this.state.password,
                signatureOf(this.sig, this.state.reuse), this.state.reuse]);
        } catch (e) {
            this.state.password = "";
            this.state.busy = false;
            throw e;
        }
        this.state.password = "";
        this.props.close();
        const r = this.payload.replay;
        if (!r) {
            this.notification.add("Confirmed. Click the button again to continue.", { type: "info" });
            return;
        }
        // run the same call again: if another step still needs its own text, the server asks again
        const result = await this.orm.call(r.model, r.method, r.args || [], r.kwargs || {});
        if (result && typeof result === "object" && result.type) {
            await this.action.doAction(result);
        } else {
            await this.action.doAction("soft_reload");
        }
        this.notification.add("Confirmed and done.", { type: "success" });
    }
}

registry.category("error_dialogs").add(CONFIRM_ERROR, ConfirmationDialog);
