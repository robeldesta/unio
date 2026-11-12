"""Document series (pads): strict, gap-free numbering.

A document type can have several series: invoice pads, fiscal cash registers, regular and DRM goods received ...
Every series numbers strictly in order. A number is taken only when the document is posted / confirmed, under a
row lock, so two users can never get the same number and no number is ever skipped. A pad number that will not be
used (torn, spoiled, cancelled on the cash register) is recorded as spoiled, which uses it up in order too.
Posted documents are never deleted (they are voided and keep their number), so the series has no holes.

A prefix can contain {fy} (fiscal year, e.g. 26-27): each fiscal year then counts from its own first number.
"""
import re
import unicodedata

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import ConcurrencyError, UserError, ValidationError
from odoo.http import request as http_request

from .ethiopian_calendar import fiscal_periods, fiscal_year_of

DOC_TYPES = [
    ('sale', 'Sales Invoice'),
    ('cash_sale', 'Cash Sale'),
    ('sale_return', 'Sales Return / Credit Note'),
    ('receipt', 'Receipt (CRV)'),
    ('payment', 'Payment (CPV)'),
    ('journal', 'Journal Voucher (JV)'),
]
KINDS = [('regular', 'Regular'), ('drm', 'Direct raw material (DRM)')]
DOC_TYPE_KEYS = {t for t, _label in DOC_TYPES}
# seconds a posting waits for another user's posting to release a series counter
LOCK_WAIT = 5  # seconds one try waits for the posting ahead of it
LOCK_RETRIES = 2  # tries Odoo runs again (the whole request, from scratch) before the user is told

# letters of other alphabets that look like Latin ones ("FЅ00001848" with a Cyrillic S): read as the Latin letter
LOOK_ALIKES = str.maketrans(
    'АВСЕНІЈКМОРЅТХУаеіјорѕсху' 'ΑΒΕΖΗΙΚΜΝΟΡΤΥΧαβεικνορτυχ',
    'ABCEHIJKMOPSTXYaeijopscxy' 'ABEZHIKMNOPTYXabeiknoptux')
# "No", "Nº", "N°", "Nr", "Number" written next to the number of a fiscal receipt: not part of it
NUMBER_WORD = re.compile(r'(?i)(?:\bno\b\.?|n[o°º]\.?|nr\.?|num(?:ber)?\.?|#)(?=[\s.:#/\-]*\d)')
# a receipt number, its digits possibly grouped in thousands (1,848 / 1.848 / 1 848)
FISCAL_NUMBER = re.compile(r"\d+(?:[,.' ]\d{3}(?!\d))*")


def _ascii_text(value):
    """`value` as the Latin letters and digits it shows: compatibility forms (full-width, Nº) folded, accents dropped,
    look-alike letters of other alphabets read as Latin."""
    text = unicodedata.normalize('NFKC', str(value or '')).translate(LOOK_ALIKES)
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def _digits_before(head):
    """Digits written before a receipt number: None; 'register' when letters follow them (VAN-074-FS00000195, the van
    number in the register's name); 'split' when they do not (FS00001848-1: a number cut in two)."""
    if not re.search(r'\d', head or ''):
        return None
    return 'register' if re.search(r'[A-Za-z]', re.split(r'\d', head)[-1]) else 'split'


def _letters(text):
    """The Latin letters of `text`, upper case ({fy} left out)."""
    return re.sub(r'[^A-Z]', '', (text or '').replace('{fy}', '').upper())


def fy_label(day):
    periods = fiscal_periods(fiscal_year_of(day))
    a, b = periods[0][2].year, periods[-1][3].year
    return '%d' % a if a == b else '%02d-%02d' % (a % 100, b % 100)  # calendar-year books: INV/2025/0001


class SbDocSeries(models.Model):
    _name = 'sb.doc.series'
    _description = 'Document Series (pad)'
    _order = 'doc_type, kind, sequence, id'
    _check_company_auto = True

    name = fields.Char('Series', required=True, help='e.g. "Invoice pad A", "Cash register 2", "DRM GRN book".')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    doc_type = fields.Selection(DOC_TYPES, 'Document', required=True)
    kind = fields.Selection(KINDS, 'Kind', default='regular', required=True,
                            help='Goods received / issued: regular or direct raw material (DRM), each with its own series.')
    is_default = fields.Boolean('Default', help='Used when the user does not choose a series.')
    prefix = fields.Char(required=True, help='Text before the number. {fy} is replaced by the fiscal year (26-27) and the '
                                             'numbers then start again every fiscal year.')
    suffix = fields.Char(help='Text after the number, e.g. /FPDV for a second cash register: FS00001619/FPDV.')
    padding = fields.Integer(default=4, help='Digits of the number: 4 gives 0001.')
    number_from = fields.Integer('First number', default=1, help='First number of the pad (or of each fiscal year).')
    number_to = fields.Integer('Last number', help='Last number of the pad, if it is a printed pad. Leave 0 for no end.')
    sb_open_numbering = fields.Boolean(
        'Open numbering (typed by the user, any text, must be unique)',
        help='The user may type any number (JV/TB/SENE2053, JV/BB/BANKS/2026-07-08): it is kept as typed, must not be used by '
             'another document of the company, and no sequence is checked. Left blank: the next number of the series.')
    counter_ids = fields.One2many('sb.doc.series.counter', 'series_id', string='Counters')
    spoil_ids = fields.One2many('sb.doc.series.spoil', 'series_id', string='Spoiled numbers')
    next_preview = fields.Char('Next number', compute='_compute_next_preview')

    _sb_prefix_unique = models.Constraint('unique(company_id, prefix, suffix)', 'Two series cannot have the same prefix and suffix.')

    @api.depends('prefix', 'suffix', 'padding', 'counter_ids.next_number', 'number_from')
    def _compute_next_preview(self):
        today = fields.Date.context_today(self)
        for s in self:
            s.next_preview = s._format(s._next_number(today), today) if s.prefix else ''

    @api.constrains('is_default', 'doc_type', 'kind', 'company_id', 'active')
    def _check_one_default(self):
        for s in self.filtered(lambda x: x.is_default and x.active):
            if self.search_count([('id', '!=', s.id), ('is_default', '=', True), ('active', '=', True), ('doc_type', '=', s.doc_type),
                                  ('kind', '=', s.kind), ('company_id', '=', s.company_id.id)]):
                raise ValidationError(self.env._('Only one default series per document and kind.'))

    # ------------------------------------------------------------------ changing a series
    NUMBERING = ('prefix', 'suffix', 'padding', 'number_from', 'number_to', 'doc_type', 'kind', 'company_id', 'sb_open_numbering')

    def _sb_in_use(self):
        """Whether a document already carries a number of this series (or one was spoiled)."""
        self.ensure_one()
        return bool(self.spoil_ids) or any(c._sb_highest_taken() for c in self.counter_ids)

    def _sb_shape(self, prefix=None, suffix=None):
        """The series' numbers as the books compare them (JV/{fy}/ and jv-{fy}- give the same numbers)."""
        norm = lambda t: re.sub(r'[^A-Z0-9]', '', _ascii_text((t or '').replace('{fy}', '#FY#')).upper())  # noqa: E731
        return (norm(self.prefix if prefix is None else prefix), norm(self.suffix if suffix is None else suffix))

    # which series a document takes (the default) and whether it can be chosen at all (archived)
    CHOICE = ('is_default', 'active')

    def _sb_require_manager(self, what):
        if not self.env.user.has_group('sonador_books.group_sb_manager'):
            raise UserError(self.env._('Only an accounting manager can %s.', what))

    def _sb_check_shape_free(self, shape, company, exclude=None):
        other = self.with_context(active_test=False).search([('id', '!=', exclude or 0), ('company_id', '=', company)])
        clash = other.filtered(lambda o: o._sb_shape() == shape)
        if clash:
            raise UserError(self.env._('%(p)s would give the same numbers as the series "%(o)s". Choose another prefix.',
                                       p=shape[0], o=clash[0].name))

    def _sb_describe(self):
        self.ensure_one()
        return self.env._('%(name)s (%(prefix)s, first number %(first)s%(default)s%(active)s)', name=self.name,
                          prefix='%s...%s' % (self.prefix, self.suffix or ''), first=self.number_from,
                          default=self.env._(', default') if self.is_default else '',
                          active='' if self.active else self.env._(', archived'))

    @api.model_create_multi
    def create(self, vals_list):
        """A new series is a new run of document numbers: an accounting manager adds it, it cannot give the numbers of
        another series, and it is written to the period log."""
        if self.env.su:
            return super().create(vals_list)
        self._sb_require_manager(self.env._('add a document series'))
        for vals in vals_list:
            company = vals.get('company_id') or self.env.context.get('default_company_id') or self.env.company.id
            self._sb_check_shape_free(self._sb_shape(vals.get('prefix') or '', vals.get('suffix') or ''), company)
        records = super().create(vals_list)
        for series in records:
            self.env['sb.lock.log']._log(series.company_id, 'series', note=self.env._('New series %s', series._sb_describe()))
        return records

    def write(self, vals):
        """The numbering of a series that numbered documents (prefix, suffix, padding, first and last number, document
        type) is changed by an accounting manager only, written to the period log as was -> now; a prefix that would give
        the numbers of another series is refused. Making a series the default, archiving or restoring one: an accounting
        manager, logged."""
        choice = [f for f in self.CHOICE if f in vals and not self.env.su and any(s[f] != bool(vals[f]) for s in self)]
        old_choice = {}
        if choice:
            self._sb_require_manager(self.env._('change the default series or archive / restore a series'))
            if vals.get('active'):  # restored: it must not give another series' numbers
                for series in self.filtered(lambda x: not x.active):
                    self._sb_check_shape_free(series._sb_shape(vals.get('prefix'), vals.get('suffix')),
                                              series.company_id.id, exclude=series.id)
            old_choice = {s.id: {f: s[f] for f in choice} for s in self}
        res = self._sb_write_numbering(vals)
        labels = {'is_default': self.env._('Default'), 'active': self.env._('Active')}
        for series in self.filtered(lambda x: x.id in old_choice):
            changed = ['%s %s -> %s' % (labels[f], self.env._('yes') if old else self.env._('no'),
                                        self.env._('yes') if series[f] else self.env._('no'))
                       for f, old in old_choice[series.id].items() if old != series[f]]
            if changed:
                self.env['sb.lock.log']._log(series.company_id, 'series', note=self.env._(
                    '%(s)s: %(what)s', s=series._sb_describe(), what='; '.join(changed)))
        return res

    def _sb_write_numbering(self, vals):
        fields_ = [f for f in self.NUMBERING if f in vals]
        if not fields_ or self.env.su:
            return super().write(vals)
        before = {s.id: {f: s[f] for f in fields_} for s in self}
        for series in self:
            changing = [f for f in fields_ if (series[f].id if hasattr(series[f], 'id') else series[f]) != vals[f]]
            if not changing:
                continue
            if series._sb_in_use() and not self.env.user.has_group('sonador_books.group_sb_manager'):
                raise UserError(self.env._('"%(s)s" has numbered documents: only an accounting manager can change its '
                                           'numbering (%(f)s).', s=series.name,
                                           f=', '.join(self._fields[f].string for f in changing)))
            if {'prefix', 'suffix'} & set(changing):
                shape = series._sb_shape(vals.get('prefix'), vals.get('suffix'))
                company = vals.get('company_id') or series.company_id.id
                other = self.with_context(active_test=False).search([('id', '!=', series.id), ('company_id', '=', company)])
                clash = other.filtered(lambda o: o._sb_shape() == shape)
                if clash:
                    raise UserError(self.env._('%(p)s would give the same numbers as the series "%(o)s". Choose another '
                                               'prefix.', p=vals.get('prefix', series.prefix), o=clash[0].name))
        res = super().write(vals)
        for series in self:
            changed = []
            for f, old in before[series.id].items():
                new = series[f]
                if old != new:
                    show = lambda v: (v.display_name if hasattr(v, 'display_name') and not isinstance(v, (str, int)) else v)  # noqa: E731
                    changed.append('%s %s -> %s' % (self._fields[f].string, show(old) or '-', show(new) or '-'))
            if changed:
                self.env['sb.lock.log']._log(series.company_id, 'series', note=self.env._(
                    '%(s)s: %(what)s', s=series.name, what='; '.join(changed)))
        return res

    # ------------------------------------------------------------------ numbering
    def _key(self, day):
        return fy_label(day) if '{fy}' in (self.prefix or '') else ''

    def _format(self, number, day):
        return '%s%s%s' % ((self.prefix or '').replace('{fy}', fy_label(day)), str(number).zfill(self.padding or 0), self.suffix or '')

    def _counter(self, day, lock=False):
        self.ensure_one()
        key = self._key(day)
        Counter = self.env['sb.doc.series.counter'].sudo()
        counter = Counter.search([('series_id', '=', self.id), ('key', '=', key)], limit=1)
        if not counter:
            # a year (or series) seen for the first time continues after any number already used with this prefix
            counter = Counter.create({'series_id': self.id, 'key': key,
                                      'next_number': max(self.number_from or 1, self._existing_max(day) + 1)})
        if lock:
            # two users posting at once: the second waits for the first to finish. When the first is saved, PostgreSQL
            # refuses the second (its view of the counter is out of date) and Odoo runs the request again at once, with
            # the new number: nobody sees an error.
            # Gap-free: the number is taken in the posting's own transaction, so the counter stays locked until that
            # posting is saved (taken at the end instead, the number would be missing from the stock moves and entries the
            # posting writes). A long posting (a back-dated receipt re-costing a year of issues) makes the next one wait:
            # it waits LOCK_WAIT seconds, then says so plainly instead of failing on a database lock error.
            self.env.cr.execute('SHOW lock_timeout')
            before = self.env.cr.fetchone()[0]
            try:
                with self.env.cr.savepoint(flush=False):
                    self.env.cr.execute("SET LOCAL lock_timeout = '%ss'" % LOCK_WAIT)
                    self.env.cr.execute('SELECT id FROM sb_doc_series_counter WHERE id = %s FOR UPDATE', (counter.id,))
            except psycopg2.errors.LockNotAvailable:
                # wait again without the user seeing anything: Odoo rolls the whole request back and runs it again from
                # the start (nothing half-saved); after LOCK_RETRIES the user is told, and nothing was saved either
                req = http_request if http_request else None
                if req is not None:
                    tries = getattr(req, '_sb_series_lock_tries', 0) + 1
                    req._sb_series_lock_tries = tries
                    if tries <= LOCK_RETRIES:
                        raise ConcurrencyError('series %s busy' % self.id) from None
                raise UserError(self.env._(
                    'Another posting in "%(s)s" is still being saved (numbers are given strictly in order, so it must finish '
                    'first). Nothing was posted: try again in a moment.', s=self.name)) from None
            self.env.cr.execute('SELECT set_config(%s, %s, true)', ('lock_timeout', before))
            counter.invalidate_recordset(['next_number'])
        return counter

    def _existing_max(self, day):
        """Highest number already carried by a document with this prefix (documents numbered before the series)."""
        return max(self._existing_numbers(day), default=0)

    def _existing_numbers(self, day, key=None):
        """Numbers already carried by a document with this prefix and suffix, whoever numbered it: this series, Odoo's own
        journal sequence (entries posted outside a voucher), a data import. `key`: the fiscal year label instead of `day`."""
        self.ensure_one()
        head = (self.prefix or '').replace('{fy}', key if key is not None else fy_label(day))
        spec = self._sb_name_table()
        if not spec or not head:
            return set()
        table, column = spec
        # the same number written by another system counts too: 'CPV 10511', 'CPV/10511', 'cpv-10511' (separators and case
        # after the prefix's letters ignored), as Odoo 18 or a data import named it
        stem = head.rstrip(' /-_.')
        self.env.cr.execute('SELECT %s FROM %s WHERE %s ILIKE %%s AND company_id = %%s' % (column, table, column),
                            (stem.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%', self.company_id.id))
        pattern = re.compile(re.escape(stem) + r'[\s/\-_.]*(\d+)' + re.escape(self.suffix or '') + '$', re.IGNORECASE)
        numbers = {int(m.group(1)) for (n,) in self.env.cr.fetchall() if (m := pattern.match(n or ''))}
        return numbers | self._sb_type_numbers(key or fy_label(day or fields.Date.context_today(self)))

    def _sb_name_table(self):
        """(table, name column) of the documents this series numbers, or None."""
        spec = next((sp for sp in self._default_specs() if sp[0][0] == self.doc_type), None)
        return (spec[2], spec[3]) if spec else self._sb_extra_name_tables().get(self.doc_type)

    @api.model
    def _sb_extra_name_tables(self):
        """{doc_type: (table, name column)} of document types that have no series of their own unless the company adds
        one (gate passes, delivery orders, FPRR ...): their documents are numbered as before until it does."""
        return {}

    @api.model
    def _sb_plain_next(self, model, code, day, company, padding=4):
        """Next number of a document numbered without a series: CODE/26-27/0001, counting each fiscal year from 1; when the
        company numbers without the year (sb_number_without_year), CODE/0001 counting on after the highest number of
        CODE/, with or without a year (CODE/26-27/0012 and CODE/2026/0012 count as 12)."""
        Model = self.env[model]
        Model.flush_model(['name', 'company_id'])
        if not company.sb_number_without_year:
            prefix = '%s/%s/' % (code, fy_label(day))
            self.env.cr.execute('SELECT name FROM %s WHERE name LIKE %%s AND company_id = %%s ORDER BY name DESC LIMIT 1' % Model._table,
                                (prefix + '%', company.id))
            row = self.env.cr.fetchone()
            return '%s%04d' % (prefix, int(row[0].rsplit('/', 1)[1]) + 1 if row else 1)
        head = '%s/' % code
        self.env.cr.execute('SELECT name FROM %s WHERE name LIKE %%s AND company_id = %%s' % Model._table,
                            (head.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%', company.id))
        pattern = re.compile(re.escape(head) + r'(?:(?:\d{2}-\d{2}|(?:19|20)\d{2})/)?(\d+)$')
        last = max((int(m.group(1)) for (name,) in self.env.cr.fetchall() if (m := pattern.match(name or ''))), default=0)
        return '%s%s' % (head, str(last + 1).zfill(padding))

    def _sb_type_names(self):
        """[(name, date)] of documents of this series' type and kind, whatever prefix or separator numbered them
        (GRV/03183 for goods received, BILL/26-27/0003 for bills). Modules add their document types."""
        return []

    def _sb_type_numbers(self, label):
        """Numbers already carried by documents of this type and kind under any prefix, in the fiscal year `label`
        (26-27): the series continues after the highest one, so a number is never given twice."""
        year = re.compile(r'/(\d{2}-\d{2}|(?:19|20)\d{2})/')
        numbers = set()
        for name, day in self._sb_type_names():
            found = year.search(name or '')
            if found and found.group(1) != label:
                continue  # numbered in another fiscal year (BILL/25-26/0500): this year counts from its own numbers
            if not found and '{fy}' in (self.prefix or '') and day and fy_label(day) != label:
                continue
            number = re.search(r'\d+', year.sub('/', name or ''))
            if number:
                numbers.add(int(number.group(0)))
        return numbers

    @api.model
    def _sb_doc_code(self, name):
        """The letters before a document's number: 'GRV/03183' -> 'GRV', 'DRM GRN 2971' -> 'DRMGRN'."""
        return re.sub(r'[^A-Z]', '', re.match(r'\D*', (name or '').replace('{fy}', '')).group(0).upper())

    def _next_number(self, day):
        self.ensure_one()
        key = self._key(day)
        counter = self.counter_ids.filtered(lambda c: c.key == key)[:1]
        number = counter.next_number if counter else (self.number_from or 1)
        return max(number, max(self._sb_type_numbers(fy_label(day)), default=0) + 1)

    def _take(self, day, manual=None, what=''):
        """The next number of the series (locked). `manual`: the number printed on the pad / cash register,
        which must be exactly the next one."""
        self.ensure_one()
        counter = self._counter(day, lock=True)
        number = counter.next_number
        # a number already used by an entry numbered elsewhere is passed over (and the counter catches up below):
        # the same number is never given twice
        used = self._existing_numbers(day)
        # documents of this type numbered under another prefix (Odoo 18's GRV/03183): continue after the highest of them
        number = max(number, max(self._sb_type_numbers(fy_label(day)), default=0) + 1)
        while number in used:
            number += 1
        if self.number_to and number > self.number_to:
            raise UserError(self.env._('Series "%(s)s" is used up (last number %(n)s). Start the next pad: add a series.',
                                       s=self.name, n=self.number_to))
        if self.sb_open_numbering and manual not in (None, False, 0, '') and str(manual).strip():
            return self._sb_open_number(manual, what)
        if manual not in (None, False, 0, ''):
            manual = self._parse(manual)
            if manual != number:
                if manual > number:
                    raise UserError(self.env._(
                        '%(what)s: the next number in "%(s)s" is %(next)s, not %(given)s. Numbers %(next)s to %(prev)s '
                        'cannot be skipped: post those documents first, or record them as spoiled in the series.',
                        what=what or self.env._('Number'), s=self.name, next=self._format(number, day), given=self._format(manual, day),
                        prev=self._format(manual - 1, day)))
                raise UserError(self.env._('%(what)s: %(given)s is already used in "%(s)s"; the next number is %(next)s.',
                                           what=what or self.env._('Number'), s=self.name, given=self._format(manual, day),
                                           next=self._format(number, day)))
        counter.next_number = number + 1
        return self._format(number, day)

    OPEN_MAX = 40

    def _sb_open_number(self, typed, what=''):
        """A number typed on an open-numbering series: trimmed (inner spaces kept single), at most OPEN_MAX characters, used
        by no other document of the company (compared as the books compare numbers: case, spaces and separators aside)."""
        from .move_guard import NORM_SQL, norm_name
        name = ' '.join(str(typed).split())
        if len(name) > self.OPEN_MAX:
            raise UserError(self.env._('%(what)s: %(n)s is longer than %(max)s characters.', what=what or self.env._('Number'),
                                       n=name, max=self.OPEN_MAX))
        if not norm_name(name):
            raise UserError(self.env._('%(what)s: "%(n)s" has no letters or digits.', what=what or self.env._('Number'), n=name))
        self.env['account.move'].flush_model(['name', 'state', 'company_id'])
        self.env.cr.execute("SELECT id, name FROM account_move WHERE company_id = %%s AND name IS NOT NULL AND name != '/' "
                            "AND state != 'draft' AND (lower(regexp_replace(name, '\\s', '', 'g')) = %%s OR %s = %%s) LIMIT 1" % NORM_SQL,
                            (self.company_id.id, re.sub(r'\s', '', name.lower()), norm_name(name)))
        row = self.env.cr.fetchone()
        if row:
            doc = self.env['sb.voucher'].sudo().search([('move_id', '=', row[0])], limit=1)
            raise UserError(self.env._('%(what)s: %(n)s is already used by %(doc)s. Type another number, or leave it blank for the '
                                       'next number of "%(s)s".', what=what or self.env._('Number'), n=name, s=self.name,
                                       doc=doc.display_name if doc else row[1]))
        return name

    def _parse(self, value):
        """'FS00001568', '1568' or 1568 -> 1568"""
        if isinstance(value, int):
            return value
        value = str(value).strip()
        if self and self.suffix and value.endswith(self.suffix):
            value = value[:-len(self.suffix)]
        digits = re.findall(r'\d+', value)
        if not digits:
            raise UserError(self.env._('"%s" is not a number.', value))
        return int(digits[-1])

    @api.model
    def _sb_fiscal_parse(self, value):
        """(text before the number, the number, text after it) of a fiscal receipt number, or None without a number.
        The number is the last one written; digits grouped by commas, dots or spaces are one number (FS 1,848 is 1848)."""
        text = NUMBER_WORD.sub(' ', _ascii_text(value)).strip()
        found = list(FISCAL_NUMBER.finditer(text))
        if not found:
            return None
        m = found[-1]
        return text[:m.start()], int(re.sub(r'\D', '', m.group(0))), text[m.end():]

    @api.model
    def _fiscal_key(self, value, series=None):
        """What makes two fiscal receipt numbers the same receipt: (letters before the number, the number, letters after).
        Case, spaces, separators, dots, "No." and digit grouping are ignored: FS00001847, FS-00001847, F.S. 1847,
        "FS No. 00001847" and FS 1,847 are one receipt; FS00001847/CLA is another register's. A bare number (00001847) takes
        the letters of `series`. "FSNo 00001847", "FS Nº 00001847" and a look-alike letter of another alphabet (a Cyrillic S)
        are the same receipt too."""
        parsed = self._sb_fiscal_parse(value)
        if not parsed:
            return (_letters(_ascii_text(value)), None, '')
        head, number, tail = _letters(parsed[0]), parsed[1], _letters(parsed[2])
        if not head and series:
            head, tail = _letters(series.prefix), tail or _letters(series.suffix)
        return (head, number, tail)

    @api.model
    def _sb_fiscal_registers(self, company):
        """{(letters before, letters after): whether the register's numbers carry other digits before them (VAN-074-FS...)}
        of the company's fiscal registers: its sales series, the registers of the receipts brought over from the previous
        system, and the ones listed in Accounting Settings."""
        from .fiscal_print import SALE_VOUCHERS
        registers = {}
        for s in self.with_context(active_test=False).sudo().search([('company_id', '=', company.id),
                                                                     ('doc_type', 'in', SALE_VOUCHERS)]):
            registers[(_letters(s.prefix), _letters(s.suffix))] = False
        self.env.cr.execute("SELECT name FROM sb_voucher WHERE company_id = %s AND sb_migrated AND voucher_type IN %s "
                            "AND state IN ('posted', 'void')", (company.id, tuple(SALE_VOUCHERS)))
        seen = {}
        for (name,) in self.env.cr.fetchall():
            parsed = self._sb_fiscal_parse(name)
            if parsed and _letters(parsed[0]) and _digits_before(parsed[0]) != 'split':
                key = (_letters(parsed[0]), _letters(parsed[2]))
                count, digits = seen.get(key, (0, False))
                seen[key] = (count + 1, digits or _digits_before(parsed[0]) == 'register')
        # a register seen once in the old books is more likely a typing slip (FS00001335/FDDV) than a register
        for key, (count, digits) in seen.items():
            if count > 1:
                registers[key] = registers.get(key, False) or digits
        for token in re.split(r'[,;\n]+', company.sudo().sb_fiscal_registers or ''):
            head, _sep, tail = token.partition('/')
            if _letters(head):
                registers.setdefault((_letters(head), _letters(tail)), False)
        return registers

    @api.model
    def _sb_check_fiscal_new(self, value, series, company):
        """A fiscal receipt number typed for a new document is the number as one of the company's registers prints it:
        letters or numbers that are not part of a known register (FS00001848A, "FS 1848 COPY", FSS00001848,
        FS00001848/XYZ) are refused, naming the document that already carries the number if there is one."""
        if self.env.su:
            return  # the system's own postings (the sync from the previous system) keep the numbers they bring
        parsed = self._sb_fiscal_parse(value)
        if not parsed:
            raise UserError(self.env._('"%s" has no receipt number. Type the fiscal receipt number as the register prints it '
                                       '(FS00001848).', value))
        head_text, number, tail_text = parsed
        head, tail = _letters(head_text), _letters(tail_text)
        if not head and series:
            head, tail = _letters(series.prefix), tail or _letters(series.suffix)
        registers = self._sb_fiscal_registers(company)
        extra_digits = _digits_before(head_text)
        if (head, tail) in registers and (not extra_digits or (extra_digits == 'register' and registers[(head, tail)])):
            return
        from .fiscal_print import SALE_VOUCHERS
        same = self.env['sb.voucher'].sudo().search([
            ('company_id', '=', company.id), ('voucher_type', 'in', SALE_VOUCHERS), ('name', 'ilike', str(number)),
            '|', ('state', 'in', ('posted', 'void')), ('sb_editing', '=', True)])
        same = same.filtered(lambda v: self._fiscal_key(v.name, v.sb_series_id or series)[1] == number)
        same = same.sorted(lambda v: (self._fiscal_key(v.name, v.sb_series_id or series)[0] != head, v.id))[:1]
        known = ', '.join(sorted('%s…%s' % (h, '/' + t if t else '') for h, t in registers if not registers[(h, t)]))
        what = (self.env._('the number written before it') if extra_digits else
                self.env._('"%s" before the number', head) if (head, tail) not in registers and not any(h == head for h, _t in registers)
                else self.env._('"%s" after the number', tail) if tail else self.env._('the letters around the number'))
        raise UserError(self.env._(
            '%(value)s is not a fiscal receipt number of this company: %(what)s does not belong to any of its registers '
            '(%(known)s).%(same)s Type the number exactly as the register prints it. A new register is added in Accounting '
            'Settings (Other fiscal registers).', value=value, what=what, known=known,
            same=self.env._(' Receipt %(n)s is already recorded as %(doc)s.', n=number, doc=same.name) if same else ''))

    @api.model
    def _sb_check_fiscal_text(self, value):
        """A fiscal receipt number is typed in Latin letters and digits, as the register prints it."""
        text = NUMBER_WORD.sub(' ', unicodedata.normalize('NFKC', str(value or '')))
        odd = sorted({c for c in text if ord(c) > 127})
        if odd:
            raise UserError(self.env._('Type the fiscal receipt number with Latin letters and digits only, as the register '
                                       'prints it (FS00001848): "%(value)s" has %(chars)s.',
                                       value=value, chars=', '.join('"%s"' % c for c in odd)))

    @api.model
    def _for(self, doc_type, kind='regular', company=None, series=None):
        """The series to number a document with: the one chosen, else the default for the type / kind."""
        if series:
            return series
        company = company or self.env.company
        dom = [('doc_type', '=', doc_type), ('company_id', '=', company.id), ('active', '=', True)]
        return self.search(dom + [('kind', '=', kind or 'regular'), ('is_default', '=', True)], limit=1) or \
            self.search(dom + [('kind', '=', kind or 'regular')], limit=1)

    # ------------------------------------------------------------------ audit
    @api.model
    def _used_numbers(self, series, key):
        """{number: document name} of documents numbered in the series (modules register their models)."""
        return {}

    def action_open_spoil(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.doc.series.spoil', 'view_mode': 'form', 'target': 'new',
                'name': self.env._('Spoiled numbers'), 'context': {'default_series_id': self.id}}


class SbDocSeriesCounter(models.Model):
    _name = 'sb.doc.series.counter'
    _description = 'Document Series Counter'
    _order = 'key desc'

    series_id = fields.Many2one('sb.doc.series', required=True, ondelete='cascade', index=True)
    key = fields.Char('Fiscal year', help='Empty when the series does not restart every year.')
    next_number = fields.Integer(required=True, default=1)

    _sb_counter_unique = models.Constraint('unique(series_id, key)', 'One counter per series and year.')

    def _sb_highest_taken(self):
        """The highest number of this counter's series and year already used by a document or recorded as spoiled."""
        self.ensure_one()
        s, key = self.series_id, self.key or ''
        numbers = set(self.env['sb.doc.series']._used_numbers(s, key)) | s._existing_numbers(None, key)
        for sp in s.spoil_ids.filtered(lambda x: (x.key or '') == key):
            numbers.add(s._parse(sp.number_to))
        return max(numbers, default=0)

    def write(self, vals):
        """Numbering takes numbers as the system. A counter changed by hand (a pad started at another number) never goes
        back over a number already used or spoiled, and every change is written to the period log."""
        if self.env.su:
            return super().write(vals)
        if set(vals) - {'next_number'}:
            raise UserError(self.env._('Only the next number of a series counter can be changed.'))
        new = int(vals['next_number'] or 0)
        for counter in self:
            highest = counter._sb_highest_taken()
            if new <= highest:
                s = counter.series_id
                raise UserError(self.env._(
                    'The next number of "%(s)s" cannot be %(new)s: %(used)s is already used or spoiled. A number is never '
                    'given twice.', s=s.name, new=new,
                    used=(s.prefix or '').replace('{fy}', counter.key or '') + str(highest).zfill(s.padding or 0) + (s.suffix or '')))
        before = {c.id: c.next_number for c in self}
        res = super().write(vals)
        for counter in self.filtered(lambda c: c.next_number != before[c.id]):
            self.env['sb.lock.log']._log(counter.series_id.company_id, 'series', note=self.env._(
                '%(s)s%(year)s: next number %(old)s -> %(new)s', s=counter.series_id.name,
                year=' (%s)' % counter.key if counter.key else '', old=before[counter.id], new=counter.next_number))
        return res


class SbDocSeriesSpoil(models.Model):
    """Numbers of a pad that will never carry a document: torn, misprinted, cancelled on the cash register.
    They are used up in order like any document, so the series stays without holes."""
    _name = 'sb.doc.series.spoil'
    _description = 'Spoiled Numbers'
    _order = 'id desc'

    series_id = fields.Many2one('sb.doc.series', required=True, ondelete='restrict', index=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    key = fields.Char('Fiscal year', readonly=True)
    number_from = fields.Char('From number', readonly=True)
    number_to = fields.Char('To number', readonly=True)
    count = fields.Integer('How many', default=1, required=True)
    reason = fields.Char(required=True, help='Torn, misprinted, cancelled on the cash register ...')
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = self.browse()
        for vals in vals_list:
            if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_accountant'):
                raise UserError(self.env._('Only accountants can record spoiled numbers.'))
            series = self.env['sb.doc.series'].browse(vals['series_id'])
            day = fields.Date.to_date(vals.get('date')) or fields.Date.context_today(self)
            count = max(int(vals.get('count') or 1), 1)
            first = last = None
            for _i in range(count):
                last = series._take(day)
                first = first or last
            vals.update(key=series._key(day), number_from=first, number_to=last)
            records |= super().create([vals])
        return records

    def unlink(self):
        raise UserError(self.env._('Spoiled numbers stay on record.'))


class SbVoucherSeries(models.Model):
    _inherit = 'sb.doc.series'

    @api.model
    def _sb_is_fiscal(self, series):
        """Ethiopia: sales documents carry the fiscal register's (or the Ministry's) number, not one the series hands out."""
        from .fiscal_print import SALE_VOUCHERS, fiscal_only
        return series.doc_type in SALE_VOUCHERS and fiscal_only(series.company_id)

    @api.model
    def _sb_fiscal_used(self, series, key):
        """{number: [voucher names]} of the fiscal receipts of this series' register, whatever the voucher (cash sale,
        credit invoice, credit note) and however the number was typed. A receipt voided and entered again counts once."""
        from .fiscal_print import SALE_VOUCHERS
        default = self._for('cash_sale', company=series.company_id)
        mine = self._fiscal_key(series._format(1, fields.Date.context_today(self)), series)
        posted, void = {}, {}
        for v in self.env['sb.voucher'].sudo().search([('company_id', '=', series.company_id.id), ('voucher_type', 'in', SALE_VOUCHERS),
                                                       '|', ('state', 'in', ('posted', 'void')), ('sb_editing', '=', True)]):
            k = self._fiscal_key(v.name, v.sb_series_id or default)
            if k[1] is None or (k[0], k[2]) != (mine[0], mine[2]) or series._key(v.date) != key:
                continue
            (void if v.state == 'void' else posted).setdefault(k[1], []).append(v.name)
        posted.update({n: names[:1] for n, names in void.items() if n not in posted})
        return posted

    def _sb_type_names(self):
        """Vouchers numbered by another system or another separator (Odoo 18's CPV/10610 for the CPV series, JV-156 ...):
        entries whose letters before the number are this series' (PCPV is not CPV). The series continues after the highest
        of them and never fills a gap below it. Fiscal receipts (numbered by the register) and pads with a suffix keep to
        their own numbers."""
        res = super()._sb_type_names()
        if self.doc_type not in DOC_TYPE_KEYS or self.suffix or self._sb_is_fiscal(self):
            return res
        code = self._sb_doc_code(self.prefix)
        lead = re.match(r'[A-Za-z]*', self.prefix or '').group(0)
        if not code or not lead:
            return res
        self.env['account.move'].flush_model(['name', 'date', 'company_id'])
        self.env.cr.execute("SELECT name, date FROM account_move WHERE company_id = %s AND name ILIKE %s",
                            (self.company_id.id, lead + '%'))
        return res + [(n, d) for n, d in self.env.cr.fetchall() if self._sb_doc_code(n).removeprefix('VOID') == code]

    @api.model
    def _used_numbers(self, series, key):
        res = super()._used_numbers(series, key)
        if self._sb_is_fiscal(series):
            res.update(self._sb_fiscal_used(series, key))
            return res
        for v in self.env['sb.voucher'].sudo().search([('sb_series_id', '=', series.id), ('state', 'in', ('posted', 'void'))]):
            if series._key(v.date) == key and v.name:
                if series.sb_open_numbering:
                    # a number typed freely (JV/TB/SENE2053) is not one of the series' own numbers
                    m = re.fullmatch(r'%s(\d+)%s' % (re.escape((series.prefix or '').replace('{fy}', key or '')),
                                                      re.escape(series.suffix or '')), v.name.removeprefix('VOID'))
                    if m:
                        res.setdefault(int(m.group(1)), []).append(v.name)
                    continue
                res.setdefault(series._parse(v.name), []).append(v.name)
        return res

    @api.model
    def _ensure_defaults(self):
        """One default series per document type, numbered like before ({code}/{fy}/0001), continuing from the
        documents already there."""
        for company in self.env['res.company'].search([]):
            for doc_type, prefix, table, column in self._default_specs():
                if self.with_context(active_test=False).search_count([('doc_type', '=', doc_type[0]), ('kind', '=', doc_type[1]),
                                                                      ('company_id', '=', company.id)]):
                    continue
                label = dict(self._fields['doc_type']._description_selection(self.env)).get(doc_type[0])
                s = self.create({'name': '%s%s' % (label, ' (DRM)' if doc_type[1] == 'drm' else ''), 'doc_type': doc_type[0],
                                 'kind': doc_type[1], 'prefix': prefix, 'padding': 4, 'is_default': True, 'company_id': company.id})
                today = fields.Date.context_today(self)
                head = prefix.replace('{fy}', fy_label(today))
                self.env.cr.execute('SELECT %s FROM %s WHERE %s LIKE %%s AND company_id = %%s' % (column, table, column),
                                    (head + '%', company.id))
                numbers = [int(m.group(1)) for (n,) in self.env.cr.fetchall() if (m := re.match(re.escape(head) + r'(\d+)$', n or ''))]
                s._counter(today).next_number = max(numbers + [s._existing_max(today)]) + 1

    @api.model
    def _default_specs(self):
        """[((doc_type, kind), prefix, table, name column)]"""
        return [(('sale', 'regular'), 'INV/{fy}/', 'account_move', 'name'),
                (('cash_sale', 'regular'), 'CS/{fy}/', 'account_move', 'name'),
                (('sale_return', 'regular'), 'CN/{fy}/', 'account_move', 'name'),
                (('receipt', 'regular'), 'CRV/{fy}/', 'account_move', 'name'),
                (('payment', 'regular'), 'CPV/{fy}/', 'account_move', 'name'),
                (('journal', 'regular'), 'JV/{fy}/', 'account_move', 'name')]


class SbReportSeries(models.AbstractModel):
    _inherit = 'sb.report'

    def _extra_catalog(self):
        from .report_catalog import E
        return super()._extra_catalog() + [
            E('numbering', 'company', 'Document Numbering Check', 'Every series (pad): first and last number used, next number, '
              'spoiled numbers, and any gap or duplicate.', 1)]

    def _report_numbering(self, opts):
        Series = self.env['sb.doc.series']
        rows = []
        problems = 0
        for s in Series.with_context(active_test=False).search([('company_id', '=', self.env.company.id)]):
            keys = set(s.counter_ids.mapped('key')) or {s._key(fields.Date.context_today(self))}
            for key in sorted(keys):
                used = Series._used_numbers(s, key)
                spoiled = set()
                for sp in s.spoil_ids.filtered(lambda x: (x.key or '') == key):
                    a, b = s._parse(sp.number_from), s._parse(sp.number_to)
                    spoiled.update(range(a, b + 1))
                counter = s.counter_ids.filtered(lambda c: c.key == key)[:1]
                nxt = counter.next_number if counter else (s.number_from or 1)
                start = s.number_from or 1
                if used and Series._sb_is_fiscal(s):
                    # fiscal receipts are numbered by the register, not the counter: from the first to the last one recorded
                    nxt = max(nxt, max(used) + 1)
                    start = max(start, min(used))
                # numbers carried by entries numbered outside the series (Odoo's journal sequence, imports) are used, not missing
                existing = {n for n in s._existing_numbers(None, key or '') if n >= start}
                outside = {n for n in existing if n < nxt} - set(used)
                taken = set(used) | spoiled | outside
                # open numbering: numbers are typed freely, a number not used is no gap
                gaps = [] if s.sb_open_numbering else [n for n in range(start, nxt) if n not in taken]
                taken |= existing  # numbers past the counter, given elsewhere: the series passes over them
                nxt = max(nxt, max(s._sb_type_numbers(key or fy_label(fields.Date.context_today(self))), default=0) + 1)
                while nxt in existing:
                    nxt += 1
                dups = [n for n, docs in used.items() if len(docs) > 1] + [n for n in spoiled if n in used]
                problems += len(gaps) + len(dups)
                fmt = lambda n: s._format(n, fields.Date.context_today(self)) if not key else \
                    (s.prefix or '').replace('{fy}', key) + str(n).zfill(s.padding or 0)  # noqa: E731
                rows.append({'id': 's%d%s' % (s.id, key), 'parent': None, 'level': 0, 'kind': 'line', 'label': s.name,
                             'values': {'series': s.name, 'doc': dict(Series._fields['doc_type']._description_selection(self.env)).get(s.doc_type),
                                        'year': key or '—', 'first': fmt(min(taken)) if taken else '', 'last': fmt(max(taken)) if taken else '',
                                        'next': fmt(nxt), 'docs': len(used), 'spoiled': len(spoiled),
                                        'status': 'OK' if not gaps and not dups else 'Fix',
                                        'gaps': ', '.join(fmt(n) for n in gaps[:20]) + (' …' if len(gaps) > 20 else ''),
                                        'dups': ', '.join(fmt(n) for n in dups[:20])},
                             'open': {'model': 'sb.doc.series', 'id': s.id}})
        cols = [{'key': 'series', 'label': 'Series / pad', 'type': 'text'}, {'key': 'doc', 'label': 'Document', 'type': 'text'},
                {'key': 'year', 'label': 'Fiscal year', 'type': 'text'}, {'key': 'first', 'label': 'First', 'type': 'text'},
                {'key': 'last', 'label': 'Last used', 'type': 'text'}, {'key': 'next', 'label': 'Next', 'type': 'text'},
                {'key': 'docs', 'label': 'Documents', 'type': 'number_int'}, {'key': 'spoiled', 'label': 'Spoiled', 'type': 'number_int'},
                {'key': 'status', 'label': 'Check', 'type': 'text'}, {'key': 'gaps', 'label': 'Missing numbers', 'type': 'text'},
                {'key': 'dups', 'label': 'Used twice', 'type': 'text'}]
        return self._doc('numbering', opts, cols, rows,
                         'No gaps, no duplicates' if not problems else '%d numbers to look at' % problems, 'Document Numbering Check')
