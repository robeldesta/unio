"""Match old items: open credits (receipts, advances, credit notes brought over unmatched) set against open debits
(invoices) of the same customer / vendor on the same receivable / payable account (or another account kept per customer /
vendor), oldest first.

It only links what is already in the books with Odoo's own reconciliation: no entry is posted, nothing is written off, no
amount changes. Preview lists every match before anything is done; Match does exactly the matches of a fresh preview and
keeps the run (who, when, what) as the log. Lines in a foreign currency are left alone (their matching books exchange
differences): match them on a receipt or payment.
"""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError

ACCOUNT_TYPES = {'receivable': ('asset_receivable',), 'payable': ('liability_payable',),
                 'both': ('asset_receivable', 'liability_payable')}
# accounts kept per customer / vendor that are not of the receivable / payable type (e.g. "Purchase Payable - Raw
# Material"): the ageing reports count them, so they are matched too
PARTNER_SIDES = {'receivable': ('customer',), 'payable': ('vendor',), 'both': ('customer', 'vendor')}


class SbMatchOld(models.Model):
    _name = 'sb.match.old'
    _description = 'Match old items'
    _inherit = ['mail.thread']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', string='Customer / Vendor', check_company=True,
                                 help='Empty: every customer / vendor.')
    kind = fields.Selection([('receivable', 'Customers (receivables)'), ('payable', 'Vendors (payables)'),
                             ('both', 'Both')], default='receivable', required=True)
    state = fields.Selection([('draft', 'Preview'), ('done', 'Matched')], default='draft', readonly=True, tracking=True)
    line_ids = fields.One2many('sb.match.old.line', 'run_id', string='Matches', readonly=True)
    match_count = fields.Integer(compute='_compute_totals', store=True, string='Matches')
    partner_count = fields.Integer(compute='_compute_totals', store=True, string='Customers / vendors')
    amount_total = fields.Monetary(compute='_compute_totals', store=True, string='Amount matched')
    skipped_note = fields.Text(readonly=True)
    done_by = fields.Many2one('res.users', readonly=True)
    done_at = fields.Datetime(readonly=True)

    @api.depends('partner_id', 'kind', 'create_date')
    def _compute_name(self):
        for r in self:
            r.name = '%s: %s' % (self.env._('Match old items'),
                                 r.partner_id.display_name or dict(self._fields['kind']._description_selection(self.env))[r.kind])

    @api.depends('line_ids.amount', 'line_ids.partner_id')
    def _compute_totals(self):
        for r in self:
            r.match_count = len(r.line_ids)
            r.partner_count = len(r.line_ids.partner_id)
            r.amount_total = sum(r.line_ids.mapped('amount'))

    def _sb_check_rights(self):
        self.env['sb.voucher']._sb_require('group_sb_accountant', self.env._('match old items'))

    def write(self, vals):
        if self.filtered(lambda r: r.state == 'done') and set(vals) - {'message_follower_ids', 'message_main_attachment_id'}:
            raise UserError(self.env._('A finished matching run is kept as it is: it is the log of what was matched.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda r: r.state == 'done'):
            raise UserError(self.env._('A finished matching run is kept as the log of what was matched.'))
        return super().unlink()

    # ------------------------------------------------------------------ the plan
    def _sb_open_lines(self):
        self.ensure_one()
        company = self.company_id
        domain = [('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                  '|', ('account_id.account_type', 'in', ACCOUNT_TYPES[self.kind]),
                  ('account_id.sb_partner_side', 'in', PARTNER_SIDES[self.kind]), ('account_id.reconcile', '=', True),
                  ('partner_id', '!=', False), ('amount_residual', '!=', 0.0)]
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        return self.env['account.move.line'].search(domain, order='date, id')

    def _sb_plan(self):
        """[(debit line, credit line, amount)]: per customer / vendor and account, open credits allocated to open debits,
        oldest first (by date, then entry), in the company currency. Returns (plan, foreign lines skipped)."""
        self.ensure_one()
        cur = self.company_id.currency_id
        lines = self._sb_open_lines()
        foreign = lines.filtered(lambda l: l.currency_id and l.currency_id != cur)
        groups = {}
        for line in lines - foreign:
            groups.setdefault((line.partner_id.id, line.account_id.id), []).append(line)
        plan = []
        for key in sorted(groups):
            group = groups[key]
            debits = [[l, l.amount_residual] for l in group if l.amount_residual > 0]
            credits = [[l, -l.amount_residual] for l in group if l.amount_residual < 0]
            di = ci = 0
            while di < len(debits) and ci < len(credits):
                d, c = debits[di], credits[ci]
                amount = cur.round(min(d[1], c[1]))
                if not cur.is_zero(amount):
                    plan.append((d[0], c[0], amount))
                d[1] = cur.round(d[1] - amount)
                c[1] = cur.round(c[1] - amount)
                if cur.is_zero(d[1]):
                    di += 1
                if cur.is_zero(c[1]):
                    ci += 1
        return plan, foreign

    def action_preview(self):
        self._sb_check_rights()
        for r in self:
            if r.state != 'draft':
                raise UserError(self.env._('This run is finished. Start a new one to match again.'))
            plan, foreign = r._sb_plan()
            r.line_ids = [Command.clear()] + [Command.create({
                'debit_line_id': d.id, 'credit_line_id': c.id, 'amount': amount}) for d, c, amount in plan]
            r.skipped_note = self.env._(
                '%(n)s open lines in a foreign currency are left alone: match them on a receipt or payment.',
                n=len(foreign)) if foreign else False
        return True

    def action_match(self):
        """Match exactly what a fresh preview shows; refuse when the books changed since the preview was made."""
        self._sb_check_rights()
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(self.env._('This run is finished. Start a new one to match again.'))
        plan, _foreign = self._sb_plan()
        cur = self.currency_id
        if not plan:
            raise UserError(self.env._('Nothing to match: no customer / vendor here has both open debits and open credits.'))
        if not self.line_ids:
            raise UserError(self.env._('Press Preview first and check the matches.'))
        shown = [(l.debit_line_id.id, l.credit_line_id.id, cur.round(l.amount)) for l in self.line_ids]
        if [(d.id, c.id, cur.round(a)) for d, c, a in plan] != shown:
            raise UserError(self.env._('The open items changed since the preview. Press Preview again, check it, then Match.'))
        Partial = self.env['account.partial.reconcile']
        before = Partial.search_count([])
        for debit, credit, amount in plan:
            (debit | credit).with_context(no_exchange_difference=True).reconcile()
        made = Partial.search_count([]) - before
        self.write({'state': 'done', 'done_by': self.env.user.id, 'done_at': fields.Datetime.now()})
        self.message_post(body=self.env._(
            'Matched by %(user)s: %(n)s matches, %(amt)s, %(p)s customers / vendors (%(partials)s reconciliation links). '
            'Nothing posted, nothing written off.', user=self.env.user.name, n=len(plan),
            amt='{:,.2f}'.format(sum(a for _d, _c, a in plan)), p=len({d.partner_id.id for d, _c, _a in plan}), partials=made))
        # invoices brought over as plain entries: their payment status follows their open lines
        moves = self.line_ids.debit_line_id.move_id | self.line_ids.credit_line_id.move_id
        self.env['sb.voucher'].search([('move_id', 'in', moves.ids)])._compute_payment_state()
        return True


class SbMatchOldLine(models.Model):
    _name = 'sb.match.old.line'
    _description = 'Match old items: one match'
    _order = 'id'

    run_id = fields.Many2one('sb.match.old', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='run_id.currency_id')
    # the log outlives its items: a document corrected later rewrites its entry (and drops the match), the run still
    # says what was matched
    debit_line_id = fields.Many2one('account.move.line', string='Debit item', ondelete='set null')
    credit_line_id = fields.Many2one('account.move.line', string='Credit item', ondelete='set null')
    partner_id = fields.Many2one('res.partner', compute='_compute_docs', store=True, string='Customer / Vendor')
    account_id = fields.Many2one('account.account', compute='_compute_docs', store=True, string='Account')
    debit_doc = fields.Char(compute='_compute_docs', store=True, string='Debit')
    debit_date = fields.Date(compute='_compute_docs', store=True, string='Debit date')
    credit_doc = fields.Char(compute='_compute_docs', store=True, string='Credit')
    credit_date = fields.Date(compute='_compute_docs', store=True, string='Credit date')
    amount = fields.Monetary()

    @api.depends('debit_line_id', 'credit_line_id')
    def _compute_docs(self):
        def doc(line):
            return line.move_id.name if line.name in (False, line.move_id.name) else '%s %s' % (line.move_id.name, line.name)
        for l in self:
            # an item rewritten since (its document corrected) leaves what was matched as it was
            d, c = l.debit_line_id, l.credit_line_id
            l.update({'debit_doc': doc(d) if d else l.debit_doc, 'debit_date': d.date if d else l.debit_date,
                      'partner_id': d.partner_id if d else l.partner_id, 'account_id': d.account_id if d else l.account_id,
                      'credit_doc': doc(c) if c else l.credit_doc, 'credit_date': c.date if c else l.credit_date})
