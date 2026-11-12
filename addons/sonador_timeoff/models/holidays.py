"""Ethiopian public holidays (paid, Labour Proclamation 1156/2019 art. 73) as company-wide days off on the working
calendar (resource.calendar.leaves), so leave days, probation and payroll skip them.
Fixed dates come from the Ethiopian calendar; Fasika / Siklet from the Orthodox (Julian) Easter; the Muslim holidays
from the tabular Hijri calendar and are marked to be confirmed each year (the moon sighting can move them a day)."""
from datetime import date, datetime, time, timedelta

from odoo import api, fields, models

from odoo.addons.sonador_books.models.ethiopian_calendar import from_ethiopian


def orthodox_easter(year):
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = (d + e + 114) % 31 + 1
    return date(year, month, day) + timedelta(days=13)  # Julian -> Gregorian (1900-2099)


def hijri_to_gregorian(hy, hm, hd):
    """Tabular Islamic calendar (civil, epoch 16 July 622)."""
    jd = hd + int(29.5 * (hm - 1) + 0.99) + (hy - 1) * 354 + (3 + 11 * hy) // 30 + 1948440 - 1
    l = jd + 68569
    n = 4 * l // 146097
    l = l - (146097 * n + 3) // 4
    i = 4000 * (l + 1) // 1461001
    l = l - 1461 * i // 4 + 31
    j = 80 * l // 2447
    d = l - 2447 * j // 80
    l = j // 11
    m = j + 2 - 12 * l
    y = 100 * (n - 49) + i + l
    return date(y, m, d)


def western_easter(year):
    """Easter Sunday, Gregorian (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    return date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def _muslim(gyear, names):
    out = []
    for hy in range(gyear - 580, gyear - 577):
        for (m, d, name) in names:
            g = hijri_to_gregorian(hy, m, d)
            if g.year == gyear:
                out.append((g, name, False))
    return out


def uganda_holidays(gyear):
    """Public Holidays Act (Uganda)."""
    out = [(date(gyear, m, d), name, True) for m, d, name in (
        (1, 1, "New Year's Day"), (1, 26, 'NRM Liberation Day'), (2, 16, 'Archbishop Janani Luwum Day'),
        (3, 8, "International Women's Day"), (5, 1, 'Labour Day'), (6, 3, "Martyrs' Day"), (6, 9, "National Heroes' Day"),
        (10, 9, 'Independence Day'), (12, 25, 'Christmas Day'), (12, 26, 'Boxing Day'))]
    easter = western_easter(gyear)
    out += [(easter - timedelta(days=2), 'Good Friday', True), (easter + timedelta(days=1), 'Easter Monday', True)]
    out += _muslim(gyear, ((10, 1, 'Eid al-Fitr'), (12, 10, 'Eid al-Adha')))
    return sorted(set(out))


def holidays_for(gyear, country='ET'):
    """[(date, name, confirmed)] for a Gregorian year."""
    if country == 'UG':
        return uganda_holidays(gyear)
    out = []
    for ey in (gyear - 8, gyear - 7):
        for (m, d, name) in ((1, 1, 'Enkutatash (Ethiopian New Year)'), (1, 17, 'Meskel (Finding of the True Cross)'),
                             (5, 11, 'Timket (Epiphany)'), (6, 23, 'Adwa Victory Day'), (8, 27, "Patriots' Victory Day"),
                             (9, 20, 'Downfall of the Derg')):
            g = from_ethiopian(ey, m, d)
            if g.year == gyear:
                out.append((g, name, True))
    out.append((date(gyear, 1, 7), 'Genna (Ethiopian Christmas)', True))
    out.append((date(gyear, 5, 1), 'International Labour Day', True))
    easter = orthodox_easter(gyear)
    out += [(easter - timedelta(days=2), 'Siklet (Good Friday)', True), (easter, 'Fasika (Easter)', True)]
    for hy in range(gyear - 580, gyear - 577):
        for (m, d, name) in ((10, 1, 'Eid al-Fitr'), (12, 10, 'Eid al-Adha (Arafa)'), (3, 12, 'Mawlid (Birth of the Prophet)')):
            g = hijri_to_gregorian(hy, m, d)
            if g.year == gyear:
                out.append((g, name, False))
    return sorted(set(out))


class ResourceCalendarLeaves(models.Model):
    _inherit = 'resource.calendar.leaves'

    sb_holiday = fields.Boolean('Public holiday')
    sb_confirmed = fields.Boolean('Date confirmed', default=True,
                                  help='Muslim holidays follow the moon: confirm (or move) the date when it is announced.')


class SbHolidays(models.AbstractModel):
    _name = 'sb.holidays'
    _description = 'Public holidays'

    @api.model
    def ensure_year(self, gyear, company=None):
        company = company or self.env.company
        L = self.env['resource.calendar.leaves'].sudo()
        made = 0
        for d, name, confirmed in holidays_for(gyear, company.country_id.code or 'ET'):
            start = datetime.combine(d, time(0, 0))
            if L.search_count([('sb_holiday', '=', True), ('name', '=', name), ('date_from', '>=', datetime(gyear, 1, 1)),
                               ('date_from', '<', datetime(gyear + 1, 1, 1)), ('company_id', 'in', (company.id, False))]):
                continue
            L.create({'name': name, 'date_from': start, 'date_to': datetime.combine(d, time(23, 59, 59)),
                      'resource_id': False, 'calendar_id': False, 'company_id': company.id, 'time_type': 'leave',
                      'sb_holiday': True, 'sb_confirmed': confirmed})
            made += 1
        return made

    @api.model
    def list_year(self, gyear):
        self.ensure_year(int(gyear))
        L = self.env['resource.calendar.leaves'].sudo()
        rows = L.search([('sb_holiday', '=', True), ('date_from', '>=', datetime(int(gyear), 1, 1)),
                         ('date_from', '<', datetime(int(gyear) + 1, 1, 1)), ('company_id', 'in', (self.env.company.id, False))],
                        order='date_from')
        from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian
        return [{'id': r.id, 'name': r.name, 'date': fields.Date.to_string(r.date_from.date()),
                 'ec': format_ethiopian(r.date_from.date()), 'weekday': r.date_from.strftime('%A'), 'confirmed': r.sb_confirmed}
                for r in rows]

    @api.model
    def move(self, holiday_id, new_date):
        if not self.env['sb.people']._is_hr():
            from odoo.exceptions import AccessError
            raise AccessError(self.env._('Holidays are set by HR.'))
        r = self.env['resource.calendar.leaves'].sudo().browse(int(holiday_id))
        d = fields.Date.to_date(new_date)
        r.write({'date_from': datetime.combine(d, time(0, 0)), 'date_to': datetime.combine(d, time(23, 59, 59)), 'sb_confirmed': True})
        return True
