/** @odoo-module **/
// Accountants read dates with their year: "Oct 20, 2026", never "Oct 20", in lists and forms alike.
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { DateTimeField } from "@web/views/fields/datetime/datetime_field";
import { formatDate as numericDate, formatDateTime as numericDateTime } from "@web/core/l10n/dates";

const { DateTime } = luxon;

function dateWithYear(value, options = {}) {
    if (!value) {
        return "";
    }
    return options.numeric ? numericDate(value, options) : value.toLocaleString(DateTime.DATE_MED);
}

function dateTimeWithYear(value, options = {}) {
    if (!value) {
        return "";
    }
    if (options.numeric) {
        return options.showTime === false ? numericDate(value, options) : numericDateTime(value, options);
    }
    const format = { ...DateTime.DATETIME_MED };
    if (options.showTime === false) {
        delete format.hour;
        delete format.minute;
    }
    if (options.showDate === false) {
        delete format.day;
        delete format.month;
        delete format.year;
    }
    return value.setZone(options.tz || "default").toLocaleString(format);
}

const formatters = registry.category("formatters");
const oldDate = formatters.get("date");
const oldDateTime = formatters.get("datetime");
dateWithYear.extractOptions = oldDate.extractOptions;
dateTimeWithYear.extractOptions = oldDateTime.extractOptions;
formatters.add("date", dateWithYear, { force: true });
formatters.add("datetime", dateTimeWithYear, { force: true });

patch(DateTimeField.prototype, {
    getFormattedValue(valueIndex, numeric = this.props.numeric) {
        const value = this.values[valueIndex];
        if (numeric || !value) {
            return super.getFormattedValue(valueIndex, numeric);
        }
        if (this.field.type === "date") {
            return dateWithYear(value);
        }
        const values = this.values;
        const showDate = !this.props.showTime || valueIndex !== 1 || !values[0] || !values[0].hasSame(value, "day");
        return dateTimeWithYear(value, { showTime: this.props.showTime, showDate });
    },
});
