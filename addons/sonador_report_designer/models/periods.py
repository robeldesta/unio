"""When each report is written (Robel, 5 Nov 2026), the same for every role:
- weekly: opens Friday 10:00, closes Saturday 12:00 (the week that ends that Friday);
- monthly: opens on the month's 30th (Pagume 5 for the month that carries Pagume), stays 5 days into the next month;
- quarterly, half-yearly and yearly: never close, the current one to date and the earlier ones (this fiscal year and
  the last).
A missed week or month: the general manager may reopen the one just before the current one for its writer (late).
Months, quarters and halves are the fiscal periods of the books (Hamle start)."""
from datetime import datetime, timedelta

from odoo.addons.sonador_books.models.ethiopian_calendar import (fiscal_periods, fiscal_year_of, format_ethiopian, fy_label,
                                                                 gregorian, to_ethiopian)
from odoo.addons.sonador_planning.models.service import week_start

KINDS = [('week', 'Weekly'), ('month', 'Monthly'), ('quarter', 'Quarterly'), ('half', 'Half-yearly'), ('year', 'Yearly')]
NOUN = {'week': 'week', 'month': 'month', 'quarter': 'quarter', 'half': 'half year', 'year': 'year'}
STAYS = {'month': 5}  # days into the next period
ALWAYS = ('quarter', 'half', 'year')  # never close
# the comparison tables a longer report carries, by the size of the rows
GRAINS = {'quarter': ['month', 'week'], 'half': ['quarter', 'month', 'week'], 'year': ['half', 'quarter', 'month', 'week']}


def _short(name):
    return name.rsplit(' ', 1)[0]


def spans(kind, fy):
    """The months, quarters, halves or the year of fiscal year fy: [(date_from, date_to, label)]."""
    p = fiscal_periods(fy)
    if kind == 'month':
        return [(a, b, name) for _n, name, a, b in p]
    if kind == 'year':
        return [(p[0][2], p[-1][3], fy_label(fy))]
    size, tag = (3, 'Q') if kind == 'quarter' else (6, 'H')
    out = []
    for i in range(0, 12, size):
        part = p[i:i + size]
        out.append((part[0][2], part[-1][3], '%s%d %s (%s – %s)' % (tag, i // size + 1, fy_label(fy), _short(part[0][1]), _short(part[-1][1]))))
    return out


def week_of(day, company):
    a = week_start(day, company)
    return a, a + timedelta(days=6), '%s – %s' % (format_ethiopian(a), format_ethiopian(a + timedelta(days=6)))


def label(kind, a, b, company=None):
    if kind == 'week':
        return '%s – %s' % (format_ethiopian(a), format_ethiopian(b))
    for x, y, name in spans(kind, fiscal_year_of(a)):
        if x == a:
            return name
    return '%s – %s' % (format_ethiopian(a), format_ethiopian(b))


def _opens_on(b):
    """The month that carries Pagume opens on Pagume 5 (its 6th day in a leap year is not waited for)."""
    if not gregorian():
        _y, m, d = to_ethiopian(b)
        if m == 13 and d == 6:
            return b - timedelta(days=1)
    return b


def window(kind, a, b):
    """(opens, closes) as local datetimes; quarters, halves and years: (None, None), always open."""
    if kind in ALWAYS:
        return None, None
    if kind == 'week':
        friday = a + timedelta(days=(4 - a.weekday()) % 7)
        return datetime(friday.year, friday.month, friday.day, 10), datetime(friday.year, friday.month, friday.day, 12) + timedelta(days=1)
    end = b + timedelta(days=STAYS[kind] + 1)
    o = _opens_on(b)
    return datetime(o.year, o.month, o.day), datetime(end.year, end.month, end.day)


def is_open(kind, a, b, now):
    opens, closes = window(kind, a, b)
    return (opens is None or opens <= now) and (closes is None or now < closes)


def open_periods(kind, now, company):
    """The periods of `kind` open at `now` (local), newest first: [(date_from, date_to, label)]. Quarters, halves and years:
    every one of this fiscal year and the last that has started, the running one 'to date'."""
    today = now.date()
    if kind == 'week':
        cands = [week_of(today - timedelta(days=d), company) for d in (0, 1)]
    else:
        fy = fiscal_year_of(today)
        cands = spans(kind, fy - 1) + spans(kind, fy)
    seen, out = set(), []
    for a, b, name in sorted(cands, key=lambda x: x[0], reverse=True):
        if a in seen or a > today:
            continue
        seen.add(a)
        if is_open(kind, a, b, now):
            out.append((a, b, name + (' (to date)' if kind in ALWAYS and b >= today else '')))
    return out


def previous(kind, now, company):
    """The week or month just before the current one, once its time has closed: the one the general manager may reopen."""
    today = now.date()
    if kind == 'week':
        a, b, name = week_of(today - timedelta(days=7), company)
    else:
        cur = [x for x in spans('month', fiscal_year_of(today)) if x[0] <= today <= x[1]][0]
        prev = cur[0] - timedelta(days=1)
        a, b, name = [x for x in spans('month', fiscal_year_of(prev)) if x[0] <= prev <= x[1]][0]
    return (a, b, name) if window(kind, a, b)[1] <= now else None


def next_opening(kind, now, company):
    """When the next report of `kind` opens, as text, for the reports screen."""
    today = now.date()
    if kind in ALWAYS:
        return ''
    if kind == 'week':
        for d in range(0, 8):
            a, b, _n = week_of(today + timedelta(days=d), company)
            opens, _c = window('week', a, b)
            if opens > now:
                return '%s, 10:00' % format_ethiopian(opens.date())
        return ''
    fy = fiscal_year_of(today)
    for a, b, _n in spans(kind, fy) + spans(kind, fy + 1):
        opens, _c = window(kind, a, b)
        if opens > now:
            return format_ethiopian(opens.date())
    return ''


def sub_spans(grain, a, b, upto, company):
    """The rows of a comparison table: the weeks, months, quarters or halves from a to min(b, upto), each clipped."""
    end = min(b, upto)
    out = []
    if grain == 'week':
        d = week_start(a, company)
        while d <= end:
            x, y = max(d, a), min(d + timedelta(days=6), end)
            out.append((x, y, '%s – %s' % (format_ethiopian(x), format_ethiopian(y))))
            d += timedelta(days=7)
        return out
    for fy in sorted({fiscal_year_of(a), fiscal_year_of(end)}):
        for x, y, name in spans(grain, fy):
            if y >= a and x <= end:
                out.append((max(x, a), min(y, end), name if y <= end else '%s (to %s)' % (name, format_ethiopian(end))))
    return out
