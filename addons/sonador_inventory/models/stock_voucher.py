"""Inventory documents: opening stock, goods received (GRN), goods issued (GIV), transfers and stock counts.

Every document moves stock with standard Odoo stock moves (so FIFO / average layers, warehouses and later
manufacturing all share one engine) and posts its accounting entry in the Inventory journal on the document date,
valued at what the stock moves actually took.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

import psycopg2
from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import ConcurrencyError, UserError, ValidationError
from odoo.http import request as http_request
from odoo.tools import float_compare, float_is_zero
from odoo.tools.misc import format_date

from odoo.addons.account.models.mail_message import bypass_token
from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian
from odoo.addons.sonador_books.models.move_guard import controlled

STOCK_TYPES = [
    ('opening', 'Opening Stock'),
    ('grn', 'Goods Received'),
    ('giv', 'Goods Issued (GIV)'),
    ('transfer', 'Stock Transfer'),
    ('adjustment', 'Stock Count / Adjustment'),
]
# cost recalculation entries written straight into the ledger (_insert_entries): from this many in one action, with
# lines of these fields only
INSERT_FROM = 20
INSERT_LINE_FIELDS = {'account_id', 'name', 'partner_id', 'debit', 'credit', 'product_id', 'sb_recost_move_id', 'analytic_distribution'}
PREFIX = {'opening': 'OS', 'grn': 'GRN', 'giv': 'GIV', 'transfer': 'TRF', 'adjustment': 'ADJ'}
# one posting at a time per item: how long one try waits for the posting ahead of it, and how many times Odoo runs the
# request again (from scratch) before the user is told (as the document numbering does)
ITEM_LOCK_WAIT = 5
ITEM_LOCK_RETRIES = 2


class SbStockItemLock(models.Model):
    """One row per item and company, written by every posting that moves or re-costs the item. Two postings of the same
    item never run side by side: the second waits for the first, and when the first is saved PostgreSQL refuses the
    second's out-of-date view of the stock, so Odoo runs it again at once and it sees the first (its availability check
    and its costs then count it)."""
    _name = 'sb.stock.item.lock'
    _description = 'Stock item posting lock'
    _log_access = False

    company_id = fields.Many2one('res.company', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, ondelete='cascade')
    postings = fields.Integer()

    _item_uniq = models.Constraint('unique (company_id, product_id)', 'One lock row per item and company.')


def fiscal_label(day):
    from odoo.addons.sonador_books.models.series import fy_label
    return fy_label(day)


class SbStock(models.AbstractModel):
    """The one place stock is moved: documents, sales and purchases all call this."""
    _name = 'sb.stock'
    _description = 'Stock engine'

    @api.model
    def _locations(self, company):
        def ref(xid):
            return self.env.ref(xid, raise_if_not_found=False)
        Location = self.env['stock.location']
        inv = Location.search([('usage', '=', 'inventory'), ('sb_giv_location', '=', False),
                               ('company_id', 'in', (company.id, False))], limit=1)
        giv = Location.search([('sb_giv_location', '=', True), ('company_id', 'in', (company.id, False))], limit=1)
        return {'supplier': ref('stock.stock_location_suppliers'), 'customer': ref('stock.stock_location_customers'),
                'inventory': inv, 'giv': giv or inv}

    @api.model
    def _on_hand(self, product, location, at_date=None):
        """Quantity in `location` (and the places inside it) now, or at the end of `at_date`. A date is worked out from
        the moves: Odoo's own quantity at a date ignores any date after today and gives today's stock, so a sale dated
        next week would see a receipt dated the week after."""
        if not at_date:
            return product.with_context(location=location.id).qty_available
        return sum(net for d, net in self._daily_net(product, location) if d <= at_date)

    @api.model
    def _daily_net(self, product, location, before=None):
        """[(day, quantity in - out)] of `product` in `location` and the places inside it, in date order (one query).
        `before`: the stock moves of a document; only the moves posted before it are counted (the stock as it stood
        just before the document was first posted)."""
        self.env['stock.move'].flush_model(['product_id', 'state', 'date', 'quantity', 'location_id', 'location_dest_id'])
        self.env['stock.location'].flush_model(['parent_path'])
        path = location.sudo().parent_path + '%'
        self.env.cr.execute("""
            SELECT m.date::date,
                   SUM(CASE WHEN dst.parent_path LIKE %(path)s THEN m.quantity ELSE 0 END
                       - CASE WHEN src.parent_path LIKE %(path)s THEN m.quantity ELSE 0 END)
              FROM stock_move m
              JOIN stock_location src ON src.id = m.location_id
              JOIN stock_location dst ON dst.id = m.location_dest_id
             WHERE m.product_id = %(p)s AND m.state = 'done' AND (src.parent_path LIKE %(path)s OR dst.parent_path LIKE %(path)s)
               AND (%(first)s IS NULL OR m.id < %(first)s)
             GROUP BY 1 ORDER BY 1""", {'p': product.id, 'path': path, 'first': min(before.ids) if before else None})
        return [(d, float(net)) for d, net in self.env.cr.fetchall()]

    @api.model
    def _lowest_on_hand(self, product, location, day, before=None):
        """The lowest quantity in `location` at the end of `day` or any later day (receipts first within a day): a
        back-dated issue takes stock from every day after its date, not only from its own. `before`: see _daily_net."""
        qty, later = 0.0, []
        for d, net in self._daily_net(product, location, before=before):
            if d <= day:
                qty += net
            else:
                later.append(net)
        low = qty
        for net in later:
            qty += net
            low = min(low, qty)
        return low

    @api.model
    def _lock_items(self, company, products):
        """Take the posting lock of `products` (sorted, so two postings never wait for each other crosswise) until the
        transaction ends. Called before the stock is read for an availability check or a re-costing: a second posting of
        the same item waits, then runs again with the first one in view (see SbStockItemLock). A long posting ahead (a
        back-dated receipt re-costing a year of issues) is waited for ITEM_LOCK_WAIT seconds a try, then the user is told
        plainly; nothing is saved."""
        ids = sorted(set(products.ids))
        if not ids:
            return
        cr = self.env.cr
        cr.execute('SHOW lock_timeout')
        before = cr.fetchone()[0]
        try:
            with cr.savepoint(flush=False):
                cr.execute("SET LOCAL lock_timeout = '%ss'" % ITEM_LOCK_WAIT)
                cr.execute("""INSERT INTO sb_stock_item_lock (company_id, product_id, postings)
                              SELECT %s, u.p, 1 FROM unnest(%s::int[]) WITH ORDINALITY AS u(p, n) ORDER BY u.n
                              ON CONFLICT (company_id, product_id) DO UPDATE SET postings = sb_stock_item_lock.postings + 1""",
                           (company.id, ids))
        except psycopg2.errors.LockNotAvailable:
            req = http_request if http_request else None
            if req is not None:
                tries = getattr(req, '_sb_item_lock_tries', 0) + 1
                req._sb_item_lock_tries = tries
                if tries <= ITEM_LOCK_RETRIES:
                    raise ConcurrencyError('stock items %s busy' % ids) from None
            raise UserError(self.env._(
                'Another posting of %s is still being saved (one posting at a time per item, so its stock and cost are '
                'right). Nothing was posted: try again in a moment.',
                ', '.join(self.env['product.product'].sudo().browse(ids).mapped('display_name')))) from None
        cr.execute('SELECT set_config(%s, %s, true)', ('lock_timeout', before))

    @api.model
    def _check_available(self, company, wanted, location, what, day=None):
        """wanted: {product: qty}. Raises with every short item listed. With `day`: what is on hand on that date and
        every date after it (a back-dated issue must not take stock a later issue already used): the refusal names the
        later documents that used it."""
        self._lock_items(company, self.env['product.product'].union(*wanted))
        if company.sb_allow_negative_stock:
            return
        short = []
        fmt = '{:,.2f}'.format
        for product, qty in wanted.items():
            have = self._lowest_on_hand(product, location, day) if day else self._on_hand(product, location)
            if float_compare(qty, have, precision_rounding=product.uom_id.rounding) > 0:
                line = self.env._('%(item)s: %(have)s on hand, %(qty)s needed', item=product.display_name, have=fmt(have), qty=fmt(qty))
                on_day = self._on_hand(product, location, day) if day else have
                if float_compare(on_day, have, precision_rounding=product.uom_id.rounding) > 0:
                    # back-dated: more was there that day, but later documents already used it
                    users = self._stock_users(product, location, day, -qty)
                    line = self.env._('%(item)s: %(qty)s needed on %(day)s. %(on_day)s on hand that day, but only %(have)s is '
                                      'free: later documents already used the rest: %(docs)s',
                                      item=product.display_name, qty=fmt(qty), day=format_date(self.env, day), on_day=fmt(on_day),
                                      have=fmt(have), docs=', '.join(users) if users else self.env._('later issues'))
                short.append(line)
        if short:
            raise UserError(self.env._('Not enough stock in %(loc)s for %(what)s:\n%(items)s',
                                       loc=location.display_name, what=what, items='\n'.join(short)))

    @api.model
    def _check_take_back(self, company, changes, day, what, out=None, doc_moves=None):
        """changes: {(product, location): quantity} added (+) or taken back (-) on `day`, against the stock as the
        document was posted. A void or a correction of a receipt must not take stock below zero on that date or any later
        date (when the company does not issue without stock), as an issue must not: the message names the issues that
        already used the stock. Where stock was already below zero before the document was posted (history brought over
        that dips below zero), it must not end lower than it was then: a receipt that only lifted it for a while can go
        back. A change that takes nothing back (a new cost on the same quantity) is always allowed.
        out: {(product, location): quantity} of the posted document already out of the stock (a correction in draft).
        doc_moves: the document's stock moves (all of them): the stock as it stood before it was first posted is the floor."""
        self._lock_items(company, self.env['product.product'].union(*(p for p, _loc in changes)))
        if company.sb_allow_negative_stock:
            return
        out = out or {}
        problems = []
        for (product, location), change in changes.items():
            rounding = product.uom_id.rounding
            was = self._lowest_on_hand(product, location, day) + out.get((product, location), 0.0)
            low = was + change
            if float_compare(change, 0.0, precision_rounding=rounding) >= 0:
                continue
            # the floor: zero, or, for an item whose history brought over from the previous system dips below zero, the
            # lowest the stock already stood at before the document was posted (a receipt that only lifted it can go back)
            floor = min(was, 0.0)
            if doc_moves and self._has_brought_over(product, company):
                floor = min(self._lowest_on_hand(product, location, day, before=doc_moves), 0.0)
            if float_compare(low, floor, precision_rounding=rounding) >= 0:
                continue
            fmt = '{:,.2f}'.format
            users = self._stock_users(product, location, day, change + out.get((product, location), 0.0), floor=floor,
                                      after=doc_moves if floor < 0 else None)
            problems.append(self.env._(
                '%(item)s: stock in %(loc)s would go down to %(low)s. Already used by %(docs)s.',
                item=product.display_name, loc=location.display_name, low=fmt(low),
                docs=', '.join(users) if users else self.env._('later issues')))
        if problems:
            raise UserError(self.env._('%(what)s cannot take back stock that later issues have used:\n%(items)s\n\nVoid or '
                                       'correct those issues first.', what=what, items='\n'.join(problems)))

    @api.model
    def _has_brought_over(self, product, company):
        """The item has stock history brought over from the previous system (moves with fixed values, not voids)."""
        return bool(self.env['stock.move'].sudo().search_count([
            ('product_id', '=', product.id), ('company_id', '=', company.id), ('state', '=', 'done'),
            ('sb_fixed_value', '=', True), ('sb_reversal_of_id', '=', False)], limit=1))

    @api.model
    def _stock_users(self, product, location, day, change, limit=12, floor=0.0, after=None):
        """The issues from `day` on that take `location` below `floor` (zero) once `change` is applied: the issues up to
        the first date the stock would be short, and every later one while it stays short ('GIV/2018/0003 (12/03/2026)'),
        the first `limit` of them named and the rest counted. `after`: a document's stock moves, when the stock was already
        below zero before it: only the issues posted after it are named (the others were there before it)."""
        inside = set(self.env['stock.location'].search([('id', 'child_of', location.id)]).ids)
        was = self._on_hand(product, location, day - timedelta(days=1))
        qty = was + change
        since = day
        first = min(after.ids) if after else 0
        if was < -1e-9 and not after:
            # already below zero before `day`: the stock taken back covered issues dated earlier (a sale that went below
            # zero, trued up by this receipt). They are named first: from the day after the stock last stood at zero or more.
            running, last_ok = 0.0, None
            for d, net in self._daily_net(product, location):
                if d >= day:
                    break
                running += net
                if running > -1e-9:
                    last_ok = d
            since = last_ok + timedelta(days=1) if last_ok else datetime.min.date()
        moves = self.env['stock.move'].sudo().search([
            ('product_id', '=', product.id), ('state', '=', 'done'), ('date', '>=', datetime.combine(since, datetime.min.time())),
            '|', ('location_id', 'in', list(inside)), ('location_dest_id', 'in', list(inside))], order='date, id')
        per_day = defaultdict(lambda: [0.0, []])
        used = []
        for m in moves:
            net = m.quantity * ((m.location_dest_id.id in inside) - (m.location_id.id in inside))
            mine = net < 0 and not m.sb_cancelled and m.id > first  # voided / corrected pairs net to zero: they used nothing
            if m.date.date() < day:
                if mine:
                    used.append(m)
                continue
            per_day[m.date.date()][0] += net
            if mine:
                per_day[m.date.date()][1].append(m)
        pending = []
        for d in sorted(per_day):
            qty += per_day[d][0]
            pending += per_day[d][1]
            if qty < floor - 1e-9:  # short on this date: every issue up to it used the stock
                used += pending
                pending = []
        names = []
        for m in used:
            doc = m.sb_stock_voucher_id or m.sb_voucher_id
            label = '%s (%s)' % (doc.name if doc else (m.origin or m.reference or ''), m.date.date().strftime('%d/%m/%Y'))
            if label not in names:
                names.append(label)
        if len(names) > limit:
            names = names[:limit] + [self.env._('and %s more', len(names) - limit)]
        return names

    @api.model
    def _move(self, product, qty, src, dst, day, origin, company, unit_cost=None, link=None, reverses=None):
        vals = {
            'product_id': product.id, 'product_uom_qty': qty, 'product_uom': product.uom_id.id,
            'location_id': src.id, 'location_dest_id': dst.id, 'company_id': company.id, 'origin': origin,
            'description_picking': origin,
        }
        if unit_cost is not None:
            vals.update(sb_has_cost=True, sb_unit_cost=unit_cost)
        if reverses:
            vals['sb_reversal_of_id'] = reverses.id
        vals.update(link or {})
        move = self.env['stock.move'].sudo().create(vals)
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        when = datetime.combine(day, fields.Datetime.now().time())
        move.write({'date': when})
        move.move_line_ids.write({'date': when})
        return move

    @api.model
    def _valuation_account(self, product, company):
        acc = product.with_company(company).product_tmpl_id.get_product_accounts()
        account = acc.get('stock_valuation') or company.account_stock_valuation_id
        if not account:
            raise UserError(self.env._('%s has no inventory (stock valuation) account: set it on its item class.', product.display_name))
        return account

    @api.model
    def _cogs_account(self, product, company):
        acc = product.with_company(company).product_tmpl_id.get_product_accounts()
        if not acc.get('expense'):
            raise UserError(self.env._('%s has no cost of sales account: set it on its item class.', product.display_name))
        return acc['expense']

    @api.model
    def _post_entry(self, company, day, ref, lines, partner=None, move=None):
        """lines: [(account, amount, name, extra)] where amount > 0 is a debit. Returns the posted move or nothing.
        `move`: the document's own entry, reset to draft by a correction: it is rewritten and keeps its number."""
        vals = self._entry_lines(company, lines, partner)
        if not vals:
            if move:
                controlled(move).with_context(bypass_audit=bypass_token).unlink()
            return self.env['account.move']
        journal = self._stock_journal(company)
        if move:
            move = controlled(move).sudo()
            move.write({'date': day, 'ref': ref, 'line_ids': [Command.clear()] + vals, 'sb_stock_engine': True})
        else:
            move = self.env['account.move'].sudo().create({'move_type': 'entry', 'journal_id': journal.id, 'date': day,
                                                           'ref': ref, 'line_ids': vals, 'sb_stock_engine': True})
        move.action_post()
        return move

    @api.model
    def _post_entries(self, company, entries, partner=None):
        """Several entries created and posted together: [(day, ref, lines)]. A cost recalculation of a busy item touches a
        thousand dates (one entry per date), and Odoo's create and post take about a tenth of a second an entry: the
        numbering, some sixty computed fields, checks meant for invoices. The first entry goes through Odoo (journal,
        period, accounts, the shape of a posted entry); the others are balanced lines on the same journal in open
        periods, written straight into the ledger in that shape and numbered in the journal's series (_insert_entries)."""
        journal = self._stock_journal(company)
        prepared = [(day, ref, vals) for day, ref, vals in ((d, r, self._entry_lines(company, l, partner)) for d, r, l in entries) if vals]
        if not prepared:
            return self.env['account.move']
        # balanced by construction, no invoice, no chatter
        quiet = self.env['account.move'].sudo().with_context(tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True,
                                                               mail_notrack=True, skip_invoice_sync=True, check_move_validity=False)
        plain = [e for e in prepared if all(set(c[2]) <= INSERT_LINE_FIELDS for c in e[2])]
        through_odoo = [e for e in prepared if e not in plain]
        if len(plain) < INSERT_FROM or self.env['account.analytic.distribution.model'].sudo().search_count([], limit=1):
            through_odoo, plain = prepared, []  # few entries, or analytic rules to apply: Odoo's own way
        else:
            through_odoo.append(plain.pop(0))  # the first one sets the shape of the others
        moves = quiet.create([{'move_type': 'entry', 'journal_id': journal.id, 'date': day, 'ref': ref, 'line_ids': vals,
                               'sb_stock_engine': True}
                              for day, ref, vals in through_odoo])
        moves.action_post()
        if plain:
            moves |= self._insert_entries(company, moves[-1], plain)
        return moves.with_env(self.env)

    @api.model
    def _insert_entries(self, company, template, entries):
        """Write posted entries [(day, ref, line commands)] straight into the ledger, as copies of `template` (a posted
        entry of the same journal made by Odoo) with their own date, reference, number, amounts and lines. Every other
        column takes the template's value; amounts are scaled from it. Checked here as Odoo checks a posting: open
        period, active accounts of the company."""
        Move, Line = self.env['account.move'].sudo(), self.env['account.move.line'].sudo()
        journal, cur = template.journal_id, company.currency_id
        lock = company._get_user_fiscal_lock_date(journal)
        if any(day <= lock for day, _r, _v in entries):
            raise UserError(self.env._('A cost recalculation falls on a date in a closed period (closed through %s).', lock))
        accounts = self.env['account.account'].sudo().browse({c[2]['account_id'] for _d, _r, vals in entries for c in vals})
        bad = accounts.filtered(lambda a: not a.active or company not in a.company_ids)
        if bad:
            raise UserError(self.env._('The cost recalculation uses accounts that are archived or of another company: %s',
                                       ', '.join(bad.mapped('display_name'))))
        self.env.flush_all()
        total_of = lambda vals: cur.round(sum(c[2]['debit'] for c in vals))
        base = template.amount_total or 1.0
        self.env.cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'account_move'")
        move_cols = [c for (c,) in self.env.cr.fetchall() if c != 'id']
        self.env.cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'account_move_line'")
        line_cols = [c for (c,) in self.env.cr.fetchall() if c != 'id']
        amount_cols = [c for c in move_cols if c.startswith('amount_')]
        now, uid = fields.Datetime.now(), self.env.uid
        for attempt in range(5):
            names = self._entry_numbers(template, [day for day, _r, _v in entries])
            # the entries: the template's columns, with their own date, reference, number and (scaled) amounts
            own = {'name': 'v.name', 'sequence_prefix': 'v.prefix', 'sequence_number': 'v.number', 'date': 'v.day', 'ref': 'v.ref',
                   'inalterable_hash': 'NULL', 'secure_sequence_number': 'NULL', 'access_token': 'NULL',
                   'message_main_attachment_id': 'NULL',
                   'create_uid': '%(uid)s', 'write_uid': '%(uid)s', 'create_date': '%(now)s', 'write_date': '%(now)s'}
            own.update({c: 'ROUND(t.%s * v.total / %%(base)s, %%(digits)s)' % c for c in amount_cols})
            select = ', '.join(own.get(c, 't.%s' % c) for c in move_cols)
            rows = [(names[i][0], names[i][1], names[i][2], day, ref, total_of(vals)) for i, (day, ref, vals) in enumerate(entries)]
            try:
                with self.env.cr.savepoint():
                    self.env.cr.execute(
                        'INSERT INTO account_move (%s) SELECT %s FROM account_move t, '
                        'unnest(%%(names)s::varchar[], %%(prefixes)s::varchar[], %%(numbers)s::int[], %%(days)s::date[], %%(refs)s::varchar[], '
                        '%%(totals)s::numeric[]) AS v(name, prefix, number, day, ref, total) WHERE t.id = %%(t)s RETURNING id, name'
                        % (', '.join('"%s"' % c for c in move_cols), select),
                        {'names': [r[0] for r in rows], 'prefixes': [r[1] for r in rows], 'numbers': [r[2] for r in rows],
                         'days': [r[3] for r in rows], 'refs': [r[4] for r in rows], 'totals': [r[5] for r in rows],
                         't': template.id, 'base': base, 'digits': cur.decimal_places, 'uid': uid, 'now': now})
                    id_of = dict((name, mid) for mid, name in self.env.cr.fetchall())
                break
            except psycopg2.errors.UniqueViolation:
                Move._get_sequence_cache().clear()  # another posting took a number meanwhile: number again
        else:
            raise UserError(self.env._('The cost recalculation entries could not be numbered (another posting keeps taking '
                                       'the same numbers). Try again in a moment.'))
        # the lines: the template's first line, with their own entry, account, item, label and amounts
        t_line = template.line_ids.sorted('id')[:1]
        reconcilable = set(accounts.filtered(lambda a: a.reconcile or a.account_type in ('asset_cash', 'liability_credit_card')).ids)
        products = self.env['product.product'].sudo().browse({c[2]['product_id'] for _d, _r, vals in entries for c in vals
                                                               if c[2].get('product_id')})
        uom_of = {p.id: p.uom_id.id for p in products}
        cols = ['move_id', 'account_id', 'partner_id', 'product_id', 'product_uom_id', 'sb_recost_move_id', 'name', 'date', 'ref',
                'move_name', 'debit', 'credit', 'balance', 'amount_currency', 'amount_residual', 'amount_residual_currency',
                'analytic_distribution']
        data = {c: [] for c in cols}
        for i, (day, ref, vals) in enumerate(entries):
            name = names[i][0]
            for _op, _id, v in vals:
                amount = cur.round(v['debit'] - v['credit'])
                residual = amount if v['account_id'] in reconcilable else 0.0
                for c, x in (('move_id', id_of[name]), ('account_id', v['account_id']), ('partner_id', v.get('partner_id') or None),
                             ('product_id', v.get('product_id') or None), ('product_uom_id', uom_of.get(v.get('product_id'))),
                             ('sb_recost_move_id', v.get('sb_recost_move_id') or None), ('name', v['name']), ('date', day),
                             ('ref', ref), ('move_name', name),
                             ('debit', max(amount, 0.0)), ('credit', max(-amount, 0.0)), ('balance', amount), ('amount_currency', amount),
                             ('amount_residual', residual), ('amount_residual_currency', residual),
                             ('analytic_distribution', json.dumps(v['analytic_distribution']) if v.get('analytic_distribution') else None)):
                    data[c].append(x)
        own = {c: 'v.%s' % c for c in cols}
        own.update({'price_subtotal': 'v.amount_currency', 'price_total': 'v.amount_currency', 'full_reconcile_id': 'NULL',
                    'matching_number': 'NULL', 'reconciled': 'FALSE', 'statement_line_id': 'NULL', 'statement_id': 'NULL', 'payment_id': 'NULL', 'sb_rec_id': 'NULL',
                    'create_uid': '%(uid)s', 'write_uid': '%(uid)s', 'create_date': '%(now)s', 'write_date': '%(now)s'})
        types = {'move_id': 'int', 'account_id': 'int', 'partner_id': 'int', 'product_id': 'int', 'product_uom_id': 'int',
                 'sb_recost_move_id': 'int', 'name': 'varchar', 'date': 'date', 'ref': 'varchar', 'move_name': 'varchar',
                 'analytic_distribution': 'jsonb'}
        self.env.cr.execute(
            'INSERT INTO account_move_line (%s) SELECT %s FROM account_move_line t, unnest(%s) AS v(%s) WHERE t.id = %%(t)s'
            % (', '.join('"%s"' % c for c in line_cols), ', '.join(own.get(c, 't.%s' % c) for c in line_cols),
               ', '.join('%%(%s)s::%s[]' % (c, types.get(c, 'numeric')) for c in cols), ', '.join(cols)),
            dict(data, t=t_line.id, uid=uid, now=now))
        Move.invalidate_model()
        Line.invalidate_model()
        if any(c[2].get('analytic_distribution') for _d, _r, vals in entries for c in vals):
            # the job / cost centre lines, as Odoo's posting makes them
            self.env.cr.execute('SELECT id FROM account_move_line WHERE move_id = ANY(%s) AND analytic_distribution IS NOT NULL',
                                (list(id_of.values()),))
            Line.browse([r[0] for r in self.env.cr.fetchall()])._create_analytic_lines()
        return self.env['account.move'].browse(sorted(id_of.values()))

    @api.model
    def _entry_numbers(self, template, days):
        """[(name, prefix, number)] for entries of the template's journal dated `days`, next in the journal's series as Odoo
        numbers them (period by period, in date order); the last numbers are kept for Odoo's next posting in this
        transaction, as its own numbering does."""
        Move = self.env['account.move'].sudo()
        cache = Move._get_sequence_cache()
        period, numbered, top = None, {}, {}
        for i in sorted(range(len(days)), key=lambda j: (days[j], j)):
            day = days[i]
            if not period or not (period['start'] <= day <= period['end']):
                probe = Move.new({'journal_id': template.journal_id.id, 'company_id': template.company_id.id, 'date': day,
                                  'move_type': 'entry'})
                fmt, values = probe._get_next_sequence_format()
                last = values.pop('seq')
                reset = probe._deduce_sequence_number_reset(fmt.format(**values, seq=last + 1))
                start, end = probe._get_sequence_date_range(reset)[:2]
                key = (fmt.format(**values, seq=0), template.journal_id)
                prefix = fmt[:fmt.index('{seq')].format(**values)
                if prefix not in top:
                    # the highest number already carried by the prefix, whatever its dates: with the fiscal year in the
                    # number, 1-7 July and the July a year before share STK/26-27/07/ (Odoo looks only at the month)
                    self.env.cr.execute('SELECT MAX(sequence_number) FROM account_move WHERE journal_id = %s AND sequence_prefix = %s',
                                        (template.journal_id.id, prefix))
                    top[prefix] = self.env.cr.fetchone()[0] or 0
                period = {'start': start, 'end': end, 'fmt': fmt, 'values': values, 'key': key,
                          'seq': max(last, cache.get(key, 0), top[prefix]), 'prefix': prefix}
            period['seq'] += 1
            cache[period['key']] = top[period['prefix']] = period['seq']
            numbered[i] = (period['fmt'].format(**period['values'], seq=period['seq']), period['prefix'], period['seq'])
        return [numbered[i] for i in range(len(days))]

    @api.model
    def _stock_journal(self, company):
        journal = company.account_stock_journal_id
        if not journal:
            raise UserError(self.env._('Set the Inventory journal (Inventory setup).'))
        return journal

    @api.model
    def _entry_lines(self, company, lines, partner=None):
        """lines: [(account, amount, name, extra)] -> line commands, merged per account and label and balanced."""
        cur = company.currency_id
        merged = defaultdict(float)
        extras = {}
        for account, amount, name, extra in lines:
            key = (account.id, name, repr(sorted(extra.items())) if extra else '')
            merged[key] += amount
            extras[key] = extra or {}
        rows = [(key, cur.round(amount)) for key, amount in merged.items() if not cur.is_zero(amount)]
        if not rows:
            return []
        # per-line rounding can leave a cent: put it on the biggest line so the entry balances
        diff = cur.round(sum(a for _k, a in rows))
        if diff:
            i = max(range(len(rows)), key=lambda j: abs(rows[j][1]))
            rows[i] = (rows[i][0], rows[i][1] - diff)
        return [Command.create(dict({'account_id': key[0], 'name': key[1], 'partner_id': partner.id if partner else False,
                                     'debit': amount if amount > 0 else 0.0, 'credit': -amount if amount < 0 else 0.0},
                                    **extras[key])) for key, amount in rows]


class SbStockVoucher(models.Model):
    _name = 'sb.stock.voucher'
    _description = 'Inventory Voucher'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(default='/', copy=False, readonly=True, index=True)
    voucher_type = fields.Selection(STOCK_TYPES, string='Type', required=True, default='giv', readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('posted', 'Posted'), ('void', 'Void')], default='draft',
                             tracking=True, copy=False, index=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_ec = fields.Char('E.C. Date', compute='_compute_date_ec')
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse', required=True, check_company=True,
                                   default=lambda s: s.env['stock.warehouse'].search([('company_id', '=', s.env.company.id)], limit=1))
    warehouse_dest_id = fields.Many2one('stock.warehouse', 'To warehouse', check_company=True)
    partner_id = fields.Many2one('res.partner', 'Supplier', check_company=True)
    department = fields.Char('Department / issued to', help='Who takes the goods: department, site, person.')
    received_by = fields.Char('Received / requested by')
    account_id = fields.Many2one('account.account', 'Account', check_company=True,
                                 help='GIV: expense charged. GRN: credit account (goods received not invoiced). '
                                      'Count: gains and losses. Opening: opening balance equity.')
    analytic_account_id = fields.Many2one('account.analytic.account', 'Job / cost centre', check_company=True)
    reference = fields.Char(help='Delivery note, waybill, request or count sheet number.')
    memo = fields.Char()
    line_ids = fields.One2many('sb.stock.voucher.line', 'voucher_id', string='Items', copy=True)
    stock_move_ids = fields.One2many('stock.move', 'sb_stock_voucher_id', string='Stock moves')
    move_id = fields.Many2one('account.move', 'Journal entry', copy=False, readonly=True)
    void_move_id = fields.Many2one('account.move', 'Void entry', copy=False, readonly=True)
    bill_ids = fields.One2many('sb.voucher', 'sb_grn_id', string='Bills')
    bill_many_ids = fields.Many2many('sb.voucher', 'sb_voucher_grn_rel', 'grn_id', 'voucher_id', string='Bills (several GRNs)',
                                     copy=False)
    billed = fields.Boolean(compute='_compute_billed', store=True)
    amount_total = fields.Monetary('Value', compute='_compute_amount', store=True)
    sb_migrated = fields.Boolean('Brought over', copy=False, readonly=True,
                                 help='Brought over from the previous system with its journal entry as it was.')
    reason_id = fields.Many2one('sb.adjust.reason', 'Reason', check_company=True,
                                help='Why the stock is adjusted. Sets the document numbers and the gain / loss account.')
    sb_correction_before = fields.Text(copy=False, readonly=True, help='The figures as posted, while a correction is in draft.')
    sb_void_reason = fields.Char('Reason for voiding', copy=False, tracking=True,
                                 help='Why this document is voided: asked before voiding, kept in its history.')
    is_count = fields.Boolean('Physical count', default=True,
                              help='On: enter what was counted, the difference with the books is posted.\n'
                                   'Off: enter the quantity to add (+) or write off (-).')

    @api.depends('date')
    def _compute_date_ec(self):
        for v in self:
            v.date_ec = format_ethiopian(v.date)

    @api.depends('bill_ids.state', 'bill_many_ids.state')
    def _compute_billed(self):
        for v in self:
            v.billed = any(b.state == 'posted' for b in v.bill_ids | v.bill_many_ids)

    @api.depends('line_ids.value')
    def _compute_amount(self):
        for v in self:
            v.amount_total = sum(v.line_ids.mapped('value'))

    @api.onchange('voucher_type', 'company_id')
    def _onchange_default_account(self):
        c = self.company_id
        self.account_id = {'giv': c.sb_giv_account_id, 'grn': c.sb_grni_account_id, 'adjustment': c.sb_stock_adjust_account_id,
                           'opening': c.sb_opening_equity_account_id}.get(self.voucher_type) or False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for v in records.filtered(lambda r: not r.account_id):
            v._onchange_default_account()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get('move_id'):
            self._sb_name_entry()
        return res

    def _sb_name_entry(self):
        """A document's own entry carries the document's number (GRV/03184, GIV/02953, RET/00012 ...), never the next
        number of the journal: two documents never share a series, so their entries never take each other's numbers.
        Cost recalculations and other system entries keep the journal's own series."""
        for v in self:
            move = v.move_id
            if not move or not v.name or v.name == '/' or move.name == v.name:
                continue
            clash = self.env['account.move'].sudo().search_count([('journal_id', '=', move.journal_id.id), ('name', '=', v.name),
                                                                  ('id', '!=', move.id)])
            if clash:
                raise UserError(self.env._('%(n)s is already the number of another entry in the %(j)s journal: the document '
                                           'and its entry must carry the same number.', n=v.name, j=move.journal_id.name))
            controlled(move).sudo().with_context(sb_open_numbering=True).write({'name': v.name})

    # ------------------------------------------------------------------ posting
    def _locations(self):
        return self.env['sb.stock']._locations(self.company_id)

    def _check(self):
        self.ensure_one()
        lock = self.company_id._get_user_fiscal_lock_date(self.company_id.account_stock_journal_id)
        if self.date <= lock:
            raise ValidationError(self.env._('The books are closed through %(lock)s (%(ec)s). Change the date or reopen the period.',
                                             lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
        lines = self.line_ids.filtered('product_id')
        if not lines:
            raise ValidationError(self.env._('Add at least one item.'))
        if any(not l.product_id.is_storable for l in lines):
            raise ValidationError(self.env._('Only stock items can be received, issued, transferred or counted.'))
        if self.voucher_type == 'transfer':
            if not self.warehouse_dest_id or self.warehouse_dest_id == self.warehouse_id:
                raise ValidationError(self.env._('Choose a different warehouse to transfer to.'))
        elif self.voucher_type != 'adjustment' and any(l.quantity <= 0 for l in lines):
            raise ValidationError(self.env._('Quantities must be more than zero.'))
        if self.voucher_type in ('opening', 'grn') and any(l.unit_cost < 0 for l in lines):
            raise ValidationError(self.env._('Unit costs cannot be negative.'))
        if self.voucher_type in ('giv', 'grn', 'adjustment', 'opening') and not self.account_id:
            raise ValidationError(self.env._('Choose the account.'))
        if self.voucher_type == 'grn' and not self.partner_id:
            raise ValidationError(self.env._('Choose the supplier.'))

    def _next_name(self):
        self.ensure_one()
        # PREFIX/26-27/0001, or PREFIX/0001 for a company numbering without the year (numbers given earlier in this
        # transaction are flushed first, so the query sees them)
        return self.env['sb.doc.series']._sb_plain_next('sb.stock.voucher', self._name_prefix(), self.date, self.company_id)

    def _name_prefix(self):
        if self.voucher_type == 'adjustment' and self.reason_id:
            return self.reason_id.code
        return PREFIX.get(self.voucher_type, 'INV')

    @api.onchange('reason_id')
    def _onchange_reason(self):
        if self.voucher_type == 'adjustment':
            self.account_id = self.reason_id.account_id or self.company_id.sb_stock_adjust_account_id
            if self.reason_id and self.reason_id.code != 'CC':
                self.is_count = False

    def _type_label(self):
        return dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type, '')

    def action_post(self):
        for v in self:
            if v.state != 'draft':
                continue
            v._sb_require()
            v._check()
            # one posting at a time per item, taken before any stock is read (all items at once, in a fixed order)
            v.env['sb.stock']._lock_items(v.company_id, v._sb_items())
            v._sb_check_correction()
            v._sb_check_reposted_receipt()
            if v.name == '/':
                v.name = v._next_name()
            v._post_stock()
            if v.sb_correction_before:
                v._sb_recost_corrected(json.loads(v.sb_correction_before), v.name)
            v.state = 'posted'
            v._sb_log_correction()
        return True

    def _sb_items(self):
        """Every item the document moves (or moved): its posting lock covers them all."""
        doc = self.sudo()
        items = doc.line_ids.product_id | doc.stock_move_ids.product_id
        if 'build_product_id' in self._fields:
            items |= doc.build_product_id
        return items.with_env(self.env)

    def _store_types(self):
        """Documents a store clerk (Inventory app) may post."""
        return ('grn', 'giv', 'transfer')

    def _sb_require(self):
        user = self.env.user
        if not self.env.su and not user.has_group('sonador_books.group_sb_clerk') and not (
                user.has_group('sonador_inventory.group_sb_store_user') and self.voucher_type in self._store_types()):
            raise UserError(self.env._('Only users with the Clerk role (or higher) can post inventory vouchers.'))
        if self.voucher_type in ('opening', 'adjustment') and not self.env.su \
                and not self.env.user.has_group('sonador_books.group_sb_accountant'):
            raise UserError(self.env._('Only accountants can post opening stock and stock adjustments.'))

    def _post_stock(self):
        self.ensure_one()
        Stock = self.env['sb.stock']
        company, loc = self.company_id, self._locations()
        stock = self.warehouse_id.lot_stock_id
        link = {'sb_stock_voucher_id': self.id}
        lines = self.line_ids.filtered('product_id')
        if self.voucher_type in ('giv', 'transfer'):
            wanted = defaultdict(float)
            for l in lines:
                wanted[l.product_id] += l.quantity
            Stock._check_available(company, wanted, stock, self.display_name, self.date)
        if self.voucher_type == 'adjustment':
            for l in lines:
                l.qty_on_hand = Stock._on_hand(l.product_id, stock)
                if self.is_count:
                    l.quantity = l.qty_counted - l.qty_on_hand
            out = {l.product_id: -l.quantity for l in lines if l.quantity < 0}
            Stock._check_available(company, out, stock, self.display_name, self.date)
        entry = []
        label = self.memo or self.reference or self.name
        for l in lines:
            p, qty = l.product_id, l.quantity
            if float_is_zero(qty, precision_rounding=p.uom_id.rounding):
                continue
            link = {'sb_stock_voucher_id': self.id, 'sb_stock_line_id': l.id}
            inv_acc = Stock._valuation_account(p, company)
            name = '%s - %s' % (self.name, p.display_name)
            if self.voucher_type in ('opening', 'grn'):
                src = loc['supplier'] if self.voucher_type == 'grn' else loc['inventory']
                m = Stock._move(p, qty, src, stock, self.date, self.name, company, unit_cost=l._sb_unit_cost(), link=link)
                value = m.value
                entry += [(inv_acc, value, name, None), (self.account_id, -value, name, None)]
            elif self.voucher_type == 'giv':
                m = Stock._move(p, qty, stock, loc['giv'], self.date, self.name, company, link=link)
                value = abs(m.value)
                extra = Stock._counter_extra(m, {}) or None  # the job / cost centre
                entry += [(l.account_id or self.account_id, value, name, extra), (inv_acc, -value, name, None)]
            elif self.voucher_type == 'transfer':
                m = Stock._move(p, qty, stock, self.warehouse_dest_id.lot_stock_id, self.date, self.name, company, link=link)
                value = qty * p.standard_price
            else:  # adjustment
                if qty > 0:
                    # Sage rule: a gain takes the cost entered, or the average cost on the adjustment date
                    cost = l.unit_cost or Stock._avg_on(p, company, self.date)
                    m = Stock._move(p, qty, loc['inventory'], stock, self.date, self.name, company, unit_cost=cost, link=link)
                    value = m.value
                    entry += [(inv_acc, value, name, None), (self.account_id, -value, name, None)]
                else:
                    m = Stock._move(p, -qty, stock, loc['inventory'], self.date, self.name, company, link=link)
                    value = -abs(m.value)
                    entry += [(self.account_id, -value, name, None), (inv_acc, value, name, None)]
            if self.voucher_type in ('opening', 'grn'):
                l.value = value  # the cost typed stays as typed (quantity x cost is rounded once, on the value)
            else:
                l.write({'value': value, 'unit_cost': abs(value / qty) if qty else l.unit_cost})
        partner = self.partner_id if self.voucher_type == 'grn' else None
        self.move_id = Stock._post_entry(company, self.date, '%s %s' % (self.name, label if label != self.name else ''), entry, partner,
                                         move=self._sb_entry_to_reuse())

    def action_void(self):
        """Stock goes back at exactly the value it was posted at, the entry is reversed, and every item is re-costed from
        the document date as if the document had never been posted (later issues take the difference on their dates)."""
        for v in self:
            if v._sb_correcting():
                v._sb_void_correction()
                continue
            if v.state != 'posted':
                continue
            if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_accountant'):
                raise UserError(self.env._('Only accountants can void inventory vouchers.'))
            if v.billed:
                raise UserError(self.env._('%s is already billed. Void the bill first.', v.name))
            v._sb_require_void_reason()
            v._sb_check_own_entry()
            v.env['sb.stock']._lock_items(v.company_id, v._sb_items())
            v._sb_check_take_back(self.env._('Voiding %s', v.name))
            company = v.company_id
            lock = company._get_user_fiscal_lock_date(company.account_stock_journal_id)
            day = v.date if v.date > lock else fields.Date.context_today(v)
            if v.move_id:
                v.move_id.line_ids.filtered('reconciled').remove_move_reconcile()
            warnings = v._sb_undo_stock(day, self.env._('Void %s', v.name))
            if v.move_id:
                v.void_move_id = controlled(v.move_id)._sb_void(day, self.env._('Void of %s', v.name))
            v.state = 'void'
            v.message_post(body=Markup('<br/>').join([self.env._('Voided on %(date)s. Reason: %(reason)s', date=format_ethiopian(day),
                                                                  reason=v.sb_void_reason)] + warnings))
        return True

    def _sb_require_void_reason(self):
        """A void needs a reason, as an accounting voucher's does (a flow that voids passes it in the context)."""
        self.ensure_one()
        if not (self.sb_void_reason or '').strip() and self.env.context.get('sb_void_reason'):
            controlled(self).sb_void_reason = self.env.context['sb_void_reason']
        if not (self.sb_void_reason or '').strip():
            raise UserError(self.env._('Write why %s is voided in "Reason for voiding" (under the title), save, then click Void '
                                       'again.', self.name))

    def _sb_correcting(self):
        """A posted document reset to draft for a correction: its stock is out of the books until it is posted again."""
        return self.state == 'draft' and bool(self.sb_correction_before)

    def _sb_void_correction(self):
        """Void a document left in draft by a correction instead of posting it again: its stock and its re-costing were
        already taken out by the correction; its entry (back in draft, with its number) is cancelled, not deleted."""
        if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_accountant'):
            raise UserError(self.env._('Only accountants can void inventory vouchers.'))
        self._sb_require_void_reason()
        self.env['sb.stock']._lock_items(self.company_id, self._sb_items())
        before = json.loads(self.sb_correction_before)
        out = self._sb_out_in_correction(before)
        if out:  # the correction took its stock out for the time being: voided, it stays out
            self.env['sb.stock']._check_take_back(self.company_id, {k: -q for k, q in out.items()}, self.date,
                                                  self.env._('Voiding %s', self.name), out=out, doc_moves=self.sudo().stock_move_ids)
        if self.move_id and self.move_id.state == 'draft':
            controlled(self.move_id).button_cancel()
        self._sb_recost_corrected(before, self.env._('Void %s', self.name))
        self.write({'state': 'void', 'sb_correction_before': False})
        self.message_post(body=self.env._('Voided on %(date)s while in correction. Reason: %(reason)s. As posted: %(doc)s',
                                          date=format_ethiopian(self.date), reason=self.sb_void_reason, doc=self._sb_describe(before)))

    def _sb_check_take_back(self, what):
        """Stock this document brought in goes back on a void: refused if later issues used it (negative stock not allowed)."""
        changes = defaultdict(float)
        for m in self._sb_live_moves().filtered(lambda m: m.location_dest_id.usage == 'internal'):
            changes[(m.product_id, m.location_dest_id)] -= m.quantity
        if changes:
            self.env['sb.stock']._check_take_back(self.company_id, changes, self.date, what, doc_moves=self.sudo().stock_move_ids)

    def _sb_receipt_types(self):
        """Documents that bring stock in: posted again after a correction, they must cover what later issues used."""
        return ('grn', 'opening', 'fprr')

    def _sb_brings_in(self):
        """{(item, location): quantity} the document brings into stock when posted."""
        stock = self.warehouse_id.lot_stock_id
        qty = defaultdict(float)
        if self.voucher_type in self._sb_receipt_types():
            for l in self.line_ids.filtered('product_id'):
                qty[(l.product_id, stock)] += l.quantity
        return qty

    def _sb_out_in_correction(self, before):
        """{(item, location): quantity} a correction in draft took out of stock (what the document had brought in)."""
        Product, Location = self.env['product.product'], self.env['stock.location']
        return {(Product.browse(p), Location.browse(loc)): qty for p, loc, qty in before.get('stock', [])}

    def _sb_check_reposted_receipt(self):
        """A corrected receipt posted again with less (or without an item) must still cover the issues after it."""
        if not self._sb_correcting():
            return
        out = self._sb_out_in_correction(json.loads(self.sb_correction_before))
        changes = defaultdict(float, {k: -q for k, q in out.items()})
        for key, qty in self._sb_brings_in().items():
            changes[key] += qty
        if changes:
            self.env['sb.stock']._check_take_back(self.company_id, changes, self.date, self.env._('%s, corrected,', self.name),
                                                  out=out, doc_moves=self.sudo().stock_move_ids)

    def _sb_live_moves(self):
        """The document's stock moves that are still in effect (not reversed by a void or a correction)."""
        return self.stock_move_ids.filtered(lambda m: m.state == 'done' and not m.sb_reversal_of_id and not m.sb_cancelled)

    def _sb_undo_stock(self, day, origin):
        """Reverse the document's stock at its own value and re-cost what follows. Returns warnings (stock below zero)."""
        self.ensure_one()
        # a correction too re-costs what follows at once (without the document, until it is posted again): the stock
        # card and the valuation stay consistent meanwhile (no quantity of zero worth -909.50), and equal the ledger
        return self.env['sb.stock']._undo_moves(self._sb_live_moves(), day, origin, {'sb_stock_voucher_id': self.id}, self.company_id)

    def _sb_recost_corrected(self, before, origin):
        """A correction re-costed the later issues without the document: re-cost every item the document had, from its
        date, now that it is posted again (an item posted again was re-costed by its own move: nothing more changes) or
        voided (nothing changes)."""
        Stock = self.env['sb.stock']
        company = self.company_id
        day = fields.Date.from_string(before['date'])
        products = self.env['product.product'].browse(before.get('items') or [p for p, _loc, _q in before.get('stock', [])]).exists()
        for product in products:
            if Stock._uses_daily_average(product, company):
                Stock._recost(product, company, day, reason=origin)

    # ------------------------------------------------------------------ correction (reset to draft)
    def _correctable_types(self):
        return ('grn', 'giv')

    def action_correct(self):
        """Correct a posted document on its own date and number: its stock moves are reversed (and left out of the
        average), its entry goes back to draft and the later issues are re-costed without it, so the stock card, the
        valuation and the ledger agree, quantity and value, while it is in draft. Change the quantities or costs and post
        again: the entry is rewritten with the same number and everything after is re-costed from its date (if it is
        voided instead, it stays as if it had never been posted). A receipt whose stock later issues used can be corrected:
        posted again with less than they used (or voided), it is refused when the company does not allow negative stock,
        as its void is; a new cost on the same quantity, or more, is always allowed."""
        for v in self:
            if v.state != 'posted':
                continue
            if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_accountant'):
                raise UserError(self.env._('Only accountants can correct posted inventory vouchers.'))
            if v.voucher_type not in v._correctable_types():
                raise UserError(self.env._('%s cannot be corrected: void it and enter a new one.', v.name))
            if v.sb_migrated:
                raise UserError(self.env._('%s was brought over from the previous system: correct it with a new voucher.', v.name))
            if v.billed:
                raise UserError(self.env._('%s is already billed. Void the bill first.', v.name))
            v._sb_check_own_entry()
            company = v.company_id
            lock = company._get_user_fiscal_lock_date(company.account_stock_journal_id)
            if v.date <= lock:
                raise UserError(self.env._('%(doc)s is dated in a closed period (closed through %(lock)s, %(ec)s). '
                                           'Reopen the period to correct it.', doc=v.name, lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
            # the stock it brought in is out until it is posted again (or voided): a new cost on the same quantity takes
            # nothing back, so the check is made when it is posted again (_sb_check_reposted_receipt) or voided
            # (_sb_void_correction), against what it then brings in; meanwhile an issue that needs that stock waits
            v.env['sb.stock']._lock_items(v.company_id, v._sb_items())
            before = v._sb_snapshot()
            if v.move_id:
                v.move_id.line_ids.filtered('reconciled').remove_move_reconcile()
            # the later issues are re-costed without it until it is posted again (or voided)
            warnings = v.with_context(sb_correcting=True)._sb_undo_stock(v.date, self.env._('Correction of %s', v.name))
            if v.move_id:
                controlled(v.move_id).button_draft()
            v.write({'state': 'draft', 'sb_correction_before': json.dumps(before)})
            v.message_post(body=Markup('<br/>').join(
                [self.env._('Reset to draft for correction by %(user)s. As posted: %(doc)s', user=self.env.user.name,
                            doc=v._sb_describe(before))] + warnings))
        return True

    def _sb_snapshot(self):
        brought = defaultdict(float)
        for m in self._sb_live_moves().filtered(lambda m: m.location_dest_id.usage == 'internal' and m.location_id.usage != 'internal'):
            brought[(m.product_id.id, m.location_dest_id.id)] += m.quantity
        return {'date': fields.Date.to_string(self.date), 'total': self.amount_total,
                'items': self._sb_live_moves().product_id.ids,
                'lines': [{'item': l.product_id.display_name, 'qty': l.quantity, 'cost': l.unit_cost, 'value': l.value}
                          for l in self.line_ids.filtered('product_id')],
                'stock': [[p, loc, qty] for (p, loc), qty in brought.items()]}

    def _sb_describe(self, snap):
        fmt = '{:,.2f}'.format
        return '; '.join('%s %s @ %s = %s' % (l['item'], fmt(l['qty']), '{:,.4f}'.format(l['cost']), fmt(l['value']))
                         for l in snap['lines']) + self.env._(' (total %s)', fmt(snap['total']))

    def _sb_check_own_entry(self):
        """A correction rewrites the document's entry and a void reverses it: refused when another document has the same
        entry (history brought over with two documents on one entry), which would change that document's books too."""
        self.ensure_one()
        if not self.move_id:
            return
        others = self.sudo().search([('move_id', '=', self.move_id.id), ('id', '!=', self.id)])
        if others:
            raise UserError(self.env._('%(doc)s has the same journal entry (%(entry)s) as %(others)s: correcting or voiding it '
                                       'would change theirs too. Enter a stock adjustment instead, or ask the accountant to '
                                       'give each document its own entry.', doc=self.name, entry=self.move_id.name,
                                       others=', '.join(others.mapped('name'))))

    def _sb_entry_to_reuse(self):
        """A corrected document rewrites its own entry (reset to draft by the correction), keeping its number."""
        return self.move_id if self.move_id and self.move_id.state == 'draft' else None

    def _sb_check_correction(self):
        if self.sb_correction_before:
            was = fields.Date.from_string(json.loads(self.sb_correction_before)['date'])
            if self.date != was:
                raise ValidationError(self.env._('A correction keeps the date of the document (%s). To move it to another '
                                                 'date, void it and enter a new one.', was))

    def _sb_log_correction(self):
        if not self.sb_correction_before:
            return
        before, after = json.loads(self.sb_correction_before), self._sb_snapshot()
        self.sb_correction_before = False
        self.message_post(body=Markup('%s<br/>%s<br/>%s') % (
            self.env._('Corrected by %s and posted again.', self.env.user.name),
            self.env._('Was: %s', self._sb_describe(before)), self.env._('Now: %s', self._sb_describe(after))))

    def action_copy_draft(self):
        self.ensure_one()
        new = self.copy({'date': self.date})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': new.id, 'view_mode': 'form',
                'name': new._type_label()}

    def action_post_close(self):
        self.action_post()
        if self.env.context.get('sb_in_drawer'):
            return {'type': 'ir.actions.act_window_close'}
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.back'}

    def action_post_new(self):
        self.action_post()
        if self.env.context.get('sb_in_drawer'):
            return {'type': 'ir.actions.act_window_close'}
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'view_mode': 'form', 'target': 'current',
                'name': self[:1]._type_label(), 'context': {'default_voucher_type': self[:1].voucher_type}}

    def action_view_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id, 'view_mode': 'form',
                'name': self.env._('Journal Entry')}

    def action_create_bill(self):
        """Enter the vendor bill for this GRN: the lines clear what the GRN credited (goods received not invoiced)."""
        self.ensure_one()
        if self.voucher_type != 'grn' or self.state != 'posted':
            raise UserError(self.env._('Only a posted GRN can be billed.'))
        account = self.account_id or self.company_id.sb_grni_account_id  # clear what the GRN credited
        lines = [Command.create(dict(part, product_id=l.product_id.id, account_id=account.id,
                                     tax_ids=[Command.set(l.product_id.supplier_taxes_id.filtered(lambda t: t.company_id == self.company_id).ids)]))
                 for l in self.line_ids.filtered('product_id') for part in l._sb_bill_lines()]
        bill = self.env['sb.voucher'].create({'voucher_type': 'bill', 'partner_id': self.partner_id.id, 'date': self.date,
                                              'reference': self.reference, 'sb_grn_id': self.id,
                                              'sb_warehouse_id': self.warehouse_id.id,
                                              'memo': self.env._('Bill for %s', self.name), 'line_ids': lines})
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': bill.id, 'view_mode': 'form',
                'name': self.env._('Purchase Bill')}

    def unlink(self):
        if any(v.state != 'draft' for v in self):
            raise UserError(self.env._('Posted inventory vouchers cannot be deleted. Use Void.'))
        posted_before = self.filtered(lambda v: v.name != '/' or v.move_id or v.sb_correction_before)
        if posted_before:
            raise UserError(self.env._('%s was posted before and keeps its number: post it again or void it. It cannot be '
                                       'deleted.', ', '.join(posted_before.mapped('name'))))
        return super().unlink()


class SbStockVoucherLine(models.Model):
    _name = 'sb.stock.voucher.line'
    _description = 'Inventory Voucher Line'
    _order = 'sequence, id'

    voucher_id = fields.Many2one('sb.stock.voucher', required=True, ondelete='cascade', index=True)
    voucher_type = fields.Selection(related='voucher_id.voucher_type')
    state = fields.Selection(related='voucher_id.state')
    company_id = fields.Many2one(related='voucher_id.company_id', store=True)
    currency_id = fields.Many2one(related='voucher_id.currency_id')
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', 'Item', check_company=True,
                                 domain="[('is_storable', '=', True)]")
    name = fields.Char('Description')
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unit')
    qty_on_hand = fields.Float('On hand', digits='Product Unit', help='Quantity in the warehouse when the count was posted.')
    qty_counted = fields.Float('Counted', digits='Product Unit')
    quantity = fields.Float(digits='Product Unit', default=1.0)
    unit_cost = fields.Float('Unit cost', digits=(16, 4))  # 2 decimals made 3 @ 3.3333 post 9.99
    value = fields.Monetary(help='Posted: the value the stock moves actually took (FIFO / average).\n'
                                 'Receipts: type the line total (e.g. the invoice amount) instead of the unit cost if you prefer.')
    value_entered = fields.Boolean('Value typed', help='The line total was typed: the unit cost is worked out from it, exactly.')
    account_id = fields.Many2one('account.account', 'Charge to', check_company=True,
                                 help='GIV only: overrides the voucher account for this line.')

    cost_locked = fields.Boolean(compute='_compute_cost_locked')

    @api.depends('voucher_type', 'quantity', 'state')
    def _compute_cost_locked(self):
        # Sage: only a positive adjustment takes a unit cost; a negative one is valued at the average cost
        for l in self:
            l.cost_locked = l.state != 'draft' or l.voucher_type in ('giv', 'transfer', 'build') or \
                (l.voucher_type == 'adjustment' and l.quantity <= 0)

    def _avg_cost(self):
        v = self.voucher_id
        day = v.date or fields.Date.context_today(self)
        return self.env['sb.stock']._avg_on(self.product_id, v.company_id or self.env.company, day)

    @api.onchange('product_id')
    def _onchange_product(self):
        if not self.product_id:
            return
        self.name = self.product_id.display_name
        self.unit_cost = self._avg_cost() if self.voucher_type == 'adjustment' else self.product_id.standard_price
        if self.voucher_type == 'adjustment':
            wh = self.voucher_id.warehouse_id
            self.qty_on_hand = self.env['sb.stock']._on_hand(self.product_id, wh.lot_stock_id) if wh else 0.0
            self.qty_counted = self.qty_on_hand
            self.quantity = 0.0

    @api.onchange('qty_counted')
    def _onchange_counted(self):
        if self.voucher_type == 'adjustment' and self.voucher_id.is_count:
            self.quantity = self.qty_counted - self.qty_on_hand

    @api.onchange('quantity')
    def _onchange_adjust_cost(self):
        if self.voucher_type == 'adjustment' and self.product_id and (self.quantity <= 0 or not self.unit_cost):
            self.unit_cost = self._avg_cost()

    @api.onchange('quantity', 'unit_cost')
    def _onchange_value(self):
        if self.state == 'posted':
            return
        if self.value_entered and abs(self.quantity * self.unit_cost - self.value) <= abs(self.quantity) * 0.00005 + 1e-9:
            return  # the unit cost shown was worked out from the value typed: the value stays as typed
        self.value_entered = False
        self.value = self.quantity * self.unit_cost

    @api.onchange('value')
    def _onchange_value_entered(self):
        """The line total typed (e.g. the invoice amount): the unit cost follows, unrounded behind the 4 decimals shown."""
        if self.state == 'posted' or self.cost_locked or not self.quantity:
            return
        if abs(self.quantity * self.unit_cost - self.value) > abs(self.quantity) * 0.00005 + 1e-9:
            self.value_entered = True
            self.unit_cost = self.value / self.quantity

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # a line total given without a unit cost (imports, scripts): the cost follows the value
            if vals.get('value') and not vals.get('unit_cost') and vals.get('quantity'):
                vals.update(value_entered=True, unit_cost=vals['value'] / vals['quantity'])
        return super().create(vals_list)

    def _sb_unit_cost(self):
        """The unit cost a receipt is posted at: the exact value typed / quantity when the value was typed (300 for
        1,000.00 posts 1,000.00, not 300 x 3.3333 = 999.99), else the unit cost."""
        if self.value_entered and self.quantity:
            return self.value / self.quantity
        return self.unit_cost

    def _sb_bill_lines(self):
        """[{name, quantity, price_unit}]: the bill line(s) for this GRN line, with its real quantity, giving back the value
        of the line exactly. A bill line's total is quantity x price rounded to the cent, and bill prices have fewer
        decimals than costs: a price is good when that product is within less than half a cent of the value (never exactly
        half: 1.5 x 6.67 = 10.005 could round either way). 3.3333 for 10.00 is 3.3333 x 3.00 (9.9999); 0.007 for 1.00 is
        0.007 x 142.86. Otherwise the whole units at the rounded price and the rest (one unit, or the fraction) at the
        price that gives what is left: 3 for 100.00 is 2 x 33.33 and 1 x 33.34. The same quantity in all, so the bill and
        the stock agree on what was bought."""
        cur = self.currency_id or self.voucher_id.currency_id
        digits = self.env['decimal.precision'].precision_get('Product Price')
        name = self.name or self.product_id.display_name
        qty, value = self.quantity, self.value
        if not qty:
            return [{'name': name, 'quantity': qty, 'price_unit': 0.0}]

        def exact(q, total):
            """The price (Product Price decimals) whose line total q x price rounds to `total` without doubt, or None."""
            price = round(total / q, digits)
            return price if abs(price * q - total) < cur.rounding / 2 - 1e-6 else None

        price = exact(qty, value)
        if price is not None:
            return [{'name': name, 'quantity': qty, 'price_unit': price}]
        # the whole units but one at the rounded price; the rest (one unit, or the fraction) takes what is left
        price = round(value / qty, digits)
        rest = qty - int(qty) if qty - int(qty) > 1e-9 else 1.0
        first = qty - rest
        if first > 1e-9:
            left = cur.round(value - cur.round(price * first))
            rest_price = exact(rest, left)
            if rest_price is not None and rest_price >= 0:
                return [{'name': name, 'quantity': first, 'price_unit': price},
                        {'name': name, 'quantity': rest, 'price_unit': rest_price}]
        # a value too small to share that way (7 for 0.05): some whole units a step dearer than the others (5 x 0.01 and
        # 2 x 0.00), the fraction at the price that gives what is left
        step = 10 ** -digits
        whole, frac = int(qty + 1e-9), qty - int(qty + 1e-9)
        frac = frac if frac > 1e-9 else 0.0
        if whole:
            low = int((value / qty) / step + 1e-9) * step
            target = cur.round(value - frac * value / qty) if frac else value
            dear = min(max(int(round((target - whole * low) / step)), 0), whole)
            done = cur.round(whole * low + dear * step)
            left = cur.round(value - done)
            rest_price = exact(frac, left) if frac else (0.0 if cur.is_zero(left) else None)
            if rest_price is not None and rest_price >= 0:
                lines = [{'name': name, 'quantity': float(dear), 'price_unit': round(low + step, digits)},
                         {'name': name, 'quantity': float(whole - dear), 'price_unit': round(low, digits)}]
                if frac:
                    lines.append({'name': name, 'quantity': frac, 'price_unit': rest_price})
                return [l for l in lines if l['quantity'] > 1e-9]
        # not met in practice: the real quantity at the nearest price
        return [{'name': name, 'quantity': qty, 'price_unit': price}]
