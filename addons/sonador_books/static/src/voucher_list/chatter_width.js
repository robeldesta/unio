/** @odoo-module **/
// Below about 1700 px the chatter beside a form squeezes its lines (amounts, taxes and items cut off at 1440 px): there it
// goes below the form instead, whatever the user's chatter setting says. Wider screens keep it beside the form when the
// user chose so. Read when the forms are first drawn (a reload after resizing the window applies a new width).
import { session } from "@web/session";

const WIDE = 1700;

if (session.chatter_position !== "bottom" && window.innerWidth < WIDE) {
    session.chatter_position = "bottom";
}
