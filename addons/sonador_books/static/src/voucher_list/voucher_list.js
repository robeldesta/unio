/** @odoo-module **/
// Voucher lists (Receipts, Payments ...): a search that also matches journal entries with no voucher behind them (payments
// and receipts brought over from Odoo 18, such as CPV/10213) says so above the list, with links to those entries.
import { onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { ListRenderer } from "@web/views/list/list_renderer";

const SEARCH_FIELDS = ["sb_search", "name", "reference", "partner_id"];

export class SbVoucherListController extends ListController {
    static template = "sonador_books.SbVoucherListView";

    setup() {
        super.setup();
        this.sbOrm = useService("orm");
        this.ledger = useState({ moves: [], text: "" });
        onWillStart(() => this.findInLedger());
        useBus(this.env.searchModel, "update", () => this.findInLedger());
    }

    get searchText() {
        const sm = this.env.searchModel;
        for (const q of sm.query || []) {
            const item = sm.searchItems[q.searchItemId];
            if (item && item.type === "field" && SEARCH_FIELDS.includes(item.fieldName) && q.autocompleteValue) {
                return String(q.autocompleteValue.label || q.autocompleteValue.value || "").trim();
            }
        }
        return "";
    }

    async findInLedger() {
        const text = this.searchText;
        if (text === this.ledger.text) {
            return;
        }
        this.ledger.text = text;
        this.ledger.moves = text ? await this.sbOrm.call("sb.voucher", "sb_ledger_matches", [text]) : [];
    }

    openEntry(move) {
        this.actionService.doAction({
            type: "ir.actions.act_window", res_model: "account.move", res_id: move.id, views: [[false, "form"]],
        });
    }
}

// the totals under the list leave voided vouchers out: a void is not money in or out
export class SbVoucherListRenderer extends ListRenderer {
    computeAggregates() {
        const aggregates = super.computeAggregates();
        const list = this.props.list;
        if (list.isGrouped) {
            return aggregates;
        }
        const rows = list.selection.length ? list.selection : list.records;
        const voided = rows.filter((r) => r.data.state === "void");
        if (!voided.length) {
            return aggregates;
        }
        for (const column of this.columns) {
            const agg = column.type === "field" && column.attrs && column.attrs.sum && aggregates[column.name];
            if (agg && typeof agg.rawValue === "number") {
                agg.rawValue -= voided.reduce((s, r) => s + (r.data[column.name] || 0), 0);
                const type = this.fields[column.name].type;
                const formatter = registry.category("formatters").get(type, false);
                agg.value = formatter ? formatter(agg.rawValue, { escape: true }) : agg.rawValue;
                agg.help = (agg.help || "") + " (voided vouchers not counted)";
            }
        }
        return aggregates;
    }
}

registry.category("views").add("sb_voucher_list", { ...listView, Controller: SbVoucherListController, Renderer: SbVoucherListRenderer });
