"""Ethiopian (Amete Mihret) calendar conversion via Julian Day Number, and the calendar the database works in.

Each database (one company, one country) works either in the Ethiopian calendar (fiscal year Hamle - Sene) or in the
Gregorian calendar with a fiscal year starting on the 1st of a chosen month (Uganda and Kenya: July). The setting is the
system parameter 'sonador.calendar' ('ethiopian' or 'gregorian:<start month>'); every date label, fiscal year and period
in the apps goes through the functions below, so they follow it."""
import calendar as _pycal
import threading
from datetime import date, timedelta

_JD_OFFSET = 1723856  # JDN of the Amete Mihret epoch minus 365
_ORDINAL_TO_JDN = 1721425

MONTHS = [
    'Meskerem', 'Tikimt', 'Hidar', 'Tahsas', 'Tir', 'Yekatit', 'Megabit',
    'Miazia', 'Ginbot', 'Sene', 'Hamle', 'Nehase', 'Pagume',
]

# Fiscal year runs Hamle 1 - Sene 30. Pagume is folded into Nehase.
FISCAL_MONTHS = [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


GREGORIAN_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
                    'November', 'December']
_MODES = {}  # dbname -> ('ethiopian', None) | ('gregorian', start_month)


def _read_mode(dbname):
    from odoo import sql_db
    try:
        with sql_db.db_connect(dbname).cursor() as cr:
            cr.execute("SELECT value FROM ir_config_parameter WHERE key = 'sonador.calendar'")
            row = cr.fetchone()
    except Exception:  # noqa: BLE001 - a database being created has no parameters yet
        return ('ethiopian', None)
    return parse_mode(row[0] if row else '')


def parse_mode(value):
    value = (value or '').strip()
    if value.startswith('gregorian'):
        try:
            start = int(value.split(':')[1])
        except (IndexError, ValueError):
            start = 1
        return ('gregorian', min(max(start, 1), 12))
    return ('ethiopian', None)


def mode():
    dbname = getattr(threading.current_thread(), 'dbname', None)
    if not dbname:
        return ('ethiopian', None)
    if dbname not in _MODES:
        _MODES[dbname] = _read_mode(dbname)
    return _MODES[dbname]


def set_mode(dbname, value):
    """Called when the setting changes (this worker); other workers read it on their next start."""
    _MODES[dbname] = parse_mode(value)


def gregorian():
    return mode()[0] == 'gregorian'


def to_ethiopian(d):
    """Gregorian date -> (year, month, day) in the Ethiopian calendar."""
    jdn = d.toordinal() + _ORDINAL_TO_JDN
    r = (jdn - _JD_OFFSET) % 1461
    n = r % 365 + 365 * (r // 1460)
    year = 4 * ((jdn - _JD_OFFSET) // 1461) + r // 365 - r // 1460
    return year, n // 30 + 1, n % 30 + 1


def from_ethiopian(year, month, day):
    jdn = _JD_OFFSET + 365 + 365 * (year - 1) + year // 4 + 30 * month + day - 31
    return date.fromordinal(jdn - _ORDINAL_TO_JDN)


def format_ethiopian(d):
    """A date as people read it in this database: 'Tikimt 9, 2019' or '19 Oct 2026'."""
    if not d:
        return ''
    if gregorian():
        return '%d %s %d' % (d.day, GREGORIAN_MONTHS[d.month - 1][:3], d.year)
    y, m, dd = to_ethiopian(d)
    return '%s %d, %d' % (MONTHS[m - 1], dd, y)


def fiscal_year_of(d):
    """Fiscal year containing d, named by the year it ends in (Ethiopian or Gregorian)."""
    md = mode()
    if md[0] == 'gregorian':
        return d.year + 1 if md[1] > 1 and d.month >= md[1] else d.year
    y, m, _dd = to_ethiopian(d)
    return y + 1 if m in (11, 12, 13) else y


def fiscal_periods(fy):
    """12 periods of fiscal year fy: list of (no, name, date_from, date_to)."""
    periods = []
    md = mode()
    if md[0] == 'gregorian':
        start = md[1]
        y = fy - 1 if start > 1 else fy
        for i in range(12):
            m = (start - 1 + i) % 12 + 1
            yy = y if m >= start else y + 1
            last = _pycal.monthrange(yy, m)[1]
            periods.append((i + 1, '%s %d' % (GREGORIAN_MONTHS[m - 1], yy), date(yy, m, 1), date(yy, m, last)))
        return periods
    for i, month in enumerate(FISCAL_MONTHS):
        ec_year = fy - 1 if month >= 11 else fy
        start = from_ethiopian(ec_year, month, 1)
        if i + 1 < len(FISCAL_MONTHS):
            nxt = FISCAL_MONTHS[i + 1]
            end = from_ethiopian(fy - 1 if nxt >= 11 else fy, nxt, 1) - timedelta(days=1)
        else:
            end = from_ethiopian(fy, 11, 1) - timedelta(days=1)
        periods.append((i + 1, '%s %d' % (MONTHS[month - 1], ec_year), start, end))
    return periods


def fy_label(fy):
    """'FY 2019 E.C.', 'FY 2026/27' (July start) or 'FY 2026' (January start)."""
    md = mode()
    if md[0] == 'gregorian':
        return 'FY %d' % fy if md[1] == 1 else 'FY %d/%02d' % (fy - 1, fy % 100)
    return 'FY %d E.C.' % fy


def fy_description(fy):
    """How the fiscal year runs, for report cover pages."""
    md = mode()
    if md[0] == 'gregorian':
        p = fiscal_periods(fy)
        return '%s - %s (%s)' % (p[0][1], p[-1][1], fy_label(fy))
    return 'Hamle 1 - Sene 30 (%s)' % fy_label(fy)
