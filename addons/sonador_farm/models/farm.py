"""The farm: sites, their houses / greenhouses / fields, and the cycles that run in them (a flock of layers, a batch of
broilers; later a planting, a season). Every cycle is a job of the books (sb.job) and has its own cost centre
(analytic account): what is bought, issued from the store, paid, worked and sold for it lands on it, so its cost and
profit come from what is really posted. The daily record posts the store movements itself: feed and medicine issued
to the flock, eggs and manure received into the store."""
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

KINDS = [('layer', 'Layers (eggs)'), ('broiler', 'Broilers (meat)')]
UNIT_KINDS = [('house', 'Poultry house'), ('pen', 'Pen'), ('greenhouse', 'Greenhouse'), ('field', 'Field / block'),
              ('store', 'Store / other')]
STATES = [('planned', 'Planned'), ('active', 'Running'), ('closed', 'Closed')]


def _store_locked(env, company):
    """The company makes its store documents (GIV, FPRR) in its main system for now (moving to Unio): the farm records the
    day but does not move the store here."""
    Stock = env['sb.stock.voucher'].with_company(company)
    if not hasattr(Stock, '_sb_create_locked'):
        return False
    return Stock._sb_create_locked([{'voucher_type': 'giv'}]) or Stock._sb_create_locked([{'voucher_type': 'fprr'}])


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_farm_bio_account_id = fields.Many2one('account.account', 'Layer flocks (biological assets)',
                                             help='Bought pullets (or reared layers) are kept here and wear off over the laying period.')
    sb_farm_bio_dep_account_id = fields.Many2one('account.account', 'Layer flocks: worn off to date',
                                                 help='What of the layer flocks has worn off (accumulated depreciation).')
    sb_farm_dep_account_id = fields.Many2one('account.account', 'Layer flock depreciation (cost)',
                                             help="Each month's share of the birds' cost: part of the cost of the eggs.")
    sb_farm_cost_account_id = fields.Many2one('account.account', 'Feed, medicine and farm inputs used',
                                              help='Where the feed and medicine issued to a flock are charged.')
    sb_farm_birds_account_id = fields.Many2one('account.account', 'Broiler chicks bought (cost)',
                                               help='Day-old chicks of a broiler batch: a cost of the batch.')


class SbFarm(models.Model):
    _name = 'sb.farm'
    _description = 'Farm'
    _order = 'name'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    location = fields.Char('Where')
    manager_id = fields.Many2one('hr.employee', 'Farm manager')
    worker_ids = fields.Many2many('hr.employee', string='Farm workers')
    warehouse_id = fields.Many2one('stock.warehouse', 'Farm store', check_company=True,
                                   help='Feed and medicine are issued from it; eggs and manure are received into it.')
    setup_job_id = fields.Many2one('sb.job', 'Setup project', help='Building the houses, ventilation, water, lights: a project; '
                                                                   'what it builds becomes fixed assets.')
    unit_ids = fields.One2many('sb.farm.unit', 'farm_id')
    flock_ids = fields.One2many('sb.farm.flock', 'farm_id')
    active = fields.Boolean(default=True)


class SbFarmUnit(models.Model):
    _name = 'sb.farm.unit'
    _description = 'Farm house / greenhouse / field'
    _order = 'farm_id, sequence, name'

    farm_id = fields.Many2one('sb.farm', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='farm_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection(UNIT_KINDS, default='house', required=True)
    capacity = fields.Integer('Capacity (birds)')
    area = fields.Float('Area (m²)')
    active = fields.Boolean(default=True)


class SbFarmBreed(models.Model):
    _name = 'sb.farm.breed'
    _description = 'Breed and its targets'
    _order = 'kind, name'

    name = fields.Char(required=True)
    kind = fields.Selection(KINDS + [('dual', 'Dual purpose')], required=True, default='layer')
    lay_until_week = fields.Integer('Kept until (weeks of age)', default=80, help='Layers: when the flock is usually sold.')
    target_ids = fields.One2many('sb.farm.breed.target', 'breed_id')
    note = fields.Char()

    def _target(self, week):
        """The breed's target for an age in weeks (the nearest week given at or below it)."""
        self.ensure_one()
        rows = self.target_ids.filtered(lambda t: t.week <= week).sorted('week')
        return rows[-1:] if rows else self.target_ids.sorted('week')[:1]


class SbFarmBreedTarget(models.Model):
    _name = 'sb.farm.breed.target'
    _description = 'Breed target by week of age'
    _order = 'breed_id, week'

    breed_id = fields.Many2one('sb.farm.breed', required=True, ondelete='cascade', index=True)
    week = fields.Integer('Week of age', required=True)
    hen_day = fields.Float('Laying % (hen-day)')
    weight = fields.Float('Body weight (g)')
    feed = fields.Float('Feed (g per bird per day)')


class SbFarmFlock(models.Model):
    _name = 'sb.farm.flock'
    _description = 'Flock / batch'
    _inherit = ['mail.thread']
    _order = 'state, date_placed desc, id desc'

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(related='farm_id.company_id', store=True, index=True)
    farm_id = fields.Many2one('sb.farm', required=True, index=True, tracking=True)
    unit_id = fields.Many2one('sb.farm.unit', 'House', domain="[('farm_id', '=', farm_id)]", tracking=True)
    kind = fields.Selection(KINDS, required=True, default='layer', tracking=True)
    breed_id = fields.Many2one('sb.farm.breed', tracking=True)
    state = fields.Selection(STATES, default='active', required=True, tracking=True, index=True)
    date_placed = fields.Date('Placed on', required=True, default=fields.Date.context_today, tracking=True)
    age_placed = fields.Integer('Age when placed (days)', help='Day-old chicks 1; pullets bought at 16 weeks: 112.')
    birds_placed = fields.Integer('Birds placed', required=True, tracking=True)
    supplier_id = fields.Many2one('res.partner', 'Bought from')
    date_closed = fields.Date('Closed on', tracking=True)
    lay_until = fields.Date('Kept until (planned)', help='Layers: the bird cost wears off from the day they are placed to this day.')
    job_id = fields.Many2one('sb.job', 'Cost centre (job)', readonly=True, copy=False)
    analytic_id = fields.Many2one('account.analytic.account', readonly=True, copy=False)
    feed_product_id = fields.Many2one('product.product', 'Feed', domain=[('is_storable', '=', True)])
    record_ids = fields.One2many('sb.farm.record', 'flock_id')
    health_ids = fields.One2many('sb.farm.health', 'flock_id')
    note = fields.Text()

    _birds = models.Constraint('CHECK(birds_placed > 0)', 'A flock has birds.')

    @api.model_create_multi
    def create(self, vals_list):
        flocks = super().create(vals_list)
        for f in flocks:
            f._sb_cost_centre()
            if f.kind == 'layer' and not f.lay_until and f.breed_id:
                f.lay_until = f.date_placed + timedelta(days=max(f.breed_id.lay_until_week * 7 - (f.age_placed or 0), 30))
            f.health_ids = [Command.create(v) for v in f._sb_schedule()]
        return flocks

    def _sb_cost_centre(self):
        """Its job (costs, sales, profit in Projects too) and its cost centre (analytic account) for the books."""
        for f in self:
            if not f.job_id:
                f.job_id = self.env['sb.job'].sudo().create({
                    'name': '%s (%s)' % (f.name, f.farm_id.name), 'kind': 'internal', 'state': 'active',
                    'company_id': f.company_id.id, 'manager_id': f.farm_id.manager_id.id or False,
                    'member_ids': [Command.set(f.farm_id.worker_ids.ids)], 'date_start': f.date_placed,
                    'site': f.farm_id.location or f.farm_id.name})
            if not f.analytic_id:
                plan = self.env['account.analytic.plan'].sudo().search([], limit=1) or \
                    self.env['account.analytic.plan'].sudo().create({'name': 'Cost centres'})
                f.analytic_id = self.env['account.analytic.account'].sudo().create({
                    'name': f.job_id.display_name, 'plan_id': plan.id, 'company_id': f.company_id.id})

    def _sb_schedule(self):
        """The health calendar of a new flock from the templates of its kind: what is due at an age the flock has not
        passed yet (pullets bought at 16 weeks skip the chick vaccines)."""
        self.ensure_one()
        out = []
        for t in self.env['sb.farm.health.template'].search([('kind', '=', self.kind), ('company_id', 'in', (False, self.company_id.id))]):
            ages = [t.age_day]
            if t.repeat_days:
                last = (self.breed_id.lay_until_week or 80) * 7
                a = t.age_day + t.repeat_days
                while a <= last:
                    ages.append(a)
                    a += t.repeat_days
            for age in ages:
                if age < (self.age_placed or 1):
                    continue
                out.append({'template_id': t.id, 'name': t.name, 'product_id': t.product_id.id or False, 'method': t.method or False,
                            'date_due': self.date_placed + timedelta(days=age - (self.age_placed or 1)),
                            'withdrawal_days': t.withdrawal_days, 'dose': t.dose})
        return out

    def age_days(self, day=None):
        self.ensure_one()
        day = day or fields.Date.context_today(self)
        return (day - self.date_placed).days + (self.age_placed or 1)

    def alive(self, day=None):
        """Birds alive at the end of `day` (today): placed less dead, culled and sold or moved out."""
        self.ensure_one()
        recs = self.record_ids.filtered(lambda r: not day or r.date <= day)
        return self.birds_placed - sum(recs.mapped('dead')) - sum(recs.mapped('culled')) - sum(recs.mapped('birds_out'))

    def action_close(self, day=None):
        for f in self:
            f.write({'state': 'closed', 'date_closed': day or fields.Date.context_today(self)})
            f.job_id.sudo().write({'state': 'done', 'date_done': f.date_closed})
            f.health_ids.filtered(lambda h: h.state == 'due').write({'state': 'skipped'})
        return True

    # ------------------------------------------------------------------ the birds' cost wears off (layers)
    def _sb_bird_cost(self):
        """Layers: the birds' cost (pullets, their transport) kept on the flock's biological asset account: the posted
        lines tagged with the flock."""
        self.ensure_one()
        acc = self.company_id.sb_farm_bio_account_id
        if not acc or not self.job_id:
            return 0.0
        vl = self.env['sb.voucher.line'].sudo().search([('job_id', '=', self.job_id.id), ('voucher_id.state', '=', 'posted'),
                                                       ('account_id', '=', acc.id)])
        total = 0.0
        for l in vl:
            vt = l.voucher_id.voucher_type
            if vt in ('bill', 'cash_purchase'):
                total += l.price_subtotal
            elif vt == 'purchase_return':
                total -= l.price_subtotal
            elif vt == 'payment':
                total += l.amount
            else:
                total += l.debit - l.credit
        return total

    def _sb_depreciated(self):
        self.ensure_one()
        acc = self.company_id.sb_farm_dep_account_id
        if not acc or not self.job_id:
            return 0.0
        vl = self.env['sb.voucher.line'].sudo().search([('job_id', '=', self.job_id.id), ('voucher_id.state', '=', 'posted'),
                                                       ('voucher_id.voucher_type', '=', 'journal'), ('account_id', '=', acc.id)])
        return sum(l.debit - l.credit for l in vl)

    def _sb_due_depreciation(self, upto):
        """The birds' cost worn off by `upto` (straight line from the day they were placed to the planned end), less what
        is posted already."""
        self.ensure_one()
        if self.kind != 'layer' or not self.lay_until:
            return 0.0
        cost = self._sb_bird_cost()
        if cost <= 0:
            return 0.0
        end = min(upto, self.date_closed or upto, self.lay_until)
        days_all = max((self.lay_until - self.date_placed).days, 1)
        days = max(min((end - self.date_placed).days + 1, days_all), 0)
        # closed early (sold before the planned end): everything left wears off on the closing day
        share = cost if (self.date_closed and self.date_closed <= upto) else cost * days / days_all
        return self.company_id.currency_id.round(share - self._sb_depreciated())

    def action_depreciate(self, upto=None):
        """Post the birds' cost worn off up to `upto` (yesterday): Dr flock depreciation (on the flock) / Cr worn off to date."""
        upto = upto or fields.Date.context_today(self) - timedelta(days=1)
        posted = self.env['sb.voucher']
        for f in self.filtered(lambda x: x.kind == 'layer'):
            c = f.company_id
            amount = f._sb_due_depreciation(upto)
            if abs(amount) < 0.01:
                continue
            if not (c.sb_farm_dep_account_id and c.sb_farm_bio_dep_account_id):
                raise UserError(self.env._('Set the layer flock accounts in Farm > Setup first.'))
            v = self.env['sb.voucher'].sudo().with_company(c).create({
                'voucher_type': 'journal', 'date': upto, 'company_id': c.id,
                'memo': self.env._('%(f)s: the birds\' cost worn off to %(d)s', f=f.name, d=upto),
                'line_ids': [Command.create({'account_id': c.sb_farm_dep_account_id.id, 'name': f.name, 'job_id': f.job_id.id,
                                             'debit': max(amount, 0.0), 'credit': max(-amount, 0.0)}),
                             Command.create({'account_id': c.sb_farm_bio_dep_account_id.id, 'name': f.name, 'job_id': f.job_id.id,
                                             'debit': max(-amount, 0.0), 'credit': max(amount, 0.0)})]})
            v.action_post()
            posted |= v
        return posted

    @api.model
    def _cron_depreciate(self):
        """Each day: flocks whose month ended yesterday get the month's share (Ethiopian months where the books run on them)."""
        from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of
        y = fields.Date.context_today(self) - timedelta(days=1)
        ends = {p[3] for p in fiscal_periods(fiscal_year_of(y))}
        if y not in ends:
            return
        for c in self.env['res.company'].search([]):
            flocks = self.sudo().with_company(c).search([('company_id', '=', c.id), ('kind', '=', 'layer'),
                                                         ('state', 'in', ('active', 'closed')), ('date_placed', '<=', y)])
            try:
                with self.env.cr.savepoint():
                    flocks.action_depreciate(y)
            except (UserError, ValidationError):
                continue


class SbFarmRecord(models.Model):
    """One day of a flock: birds lost, feed given, eggs collected, weights, manure. Saving it posts the store movements:
    the feed issued to the flock (GIV), the eggs and manure received (FPRR)."""
    _name = 'sb.farm.record'
    _description = 'Daily farm record'
    _order = 'date desc, id desc'

    flock_id = fields.Many2one('sb.farm.flock', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='flock_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    dead = fields.Integer('Died')
    culled = fields.Integer('Culled')
    birds_out = fields.Integer('Sold / moved out')
    feed_qty = fields.Float('Feed given (kg)', digits=(16, 2))
    water = fields.Float('Water (litres)')
    egg_ids = fields.One2many('sb.farm.record.egg', 'record_id')
    eggs = fields.Integer('Eggs', compute='_compute_eggs', store=True)
    eggs_broken = fields.Integer('Broken / cracked')
    weight = fields.Float('Average weight (g)', help='Broilers: the weight of a sample of birds, averaged.')
    manure = fields.Float('Manure collected')
    note = fields.Char()
    recorded_by = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    issue_id = fields.Many2one('sb.stock.voucher', 'Feed issue', readonly=True, copy=False)
    receipt_id = fields.Many2one('sb.stock.voucher', 'Eggs / manure received', readonly=True, copy=False)
    withdrawal = fields.Boolean('In a withdrawal period', help='A medicine was given whose eggs or meat may not be sold yet.')
    store_skipped = fields.Boolean('Store not moved', help='The company makes its store documents in its main system for now: '
                                                         'the day is recorded here, the store is moved there.')

    _one = models.Constraint('unique(flock_id, date)', 'One record per flock and day.')

    @api.depends('egg_ids.qty')
    def _compute_eggs(self):
        for r in self:
            r.eggs = int(sum(r.egg_ids.mapped('qty')))

    def _sb_post_store(self):
        """Feed issued to the flock and eggs / manure received into the farm store, for this record (again when it changed)."""
        Stock = self.env['sb.stock.voucher'].sudo()
        for r in self:
            f, c = r.flock_id, r.company_id
            wh = f.farm_id.warehouse_id or self.env['stock.warehouse'].sudo().search([('company_id', '=', c.id)], limit=1)
            for old in (r.issue_id | r.receipt_id).filtered(lambda v: v.state == 'posted'):
                old.with_context(sb_void_reason=self.env._('daily record of %s changed', r.date)).action_void()
            r.issue_id = r.receipt_id = False
            r.store_skipped = _store_locked(self.env, c)
            if r.store_skipped:
                continue
            if r.feed_qty > 0:
                if not f.feed_product_id:
                    raise UserError(self.env._('Choose the feed of %s (Farm > the flock) to issue it from the store.', f.name))
                account = c.sb_farm_cost_account_id
                if not account:
                    raise UserError(self.env._('Set the farm accounts in Farm > Setup first.'))
                giv = Stock.with_company(c).create({
                    'voucher_type': 'giv', 'date': r.date, 'company_id': c.id, 'warehouse_id': wh.id, 'account_id': account.id,
                    'analytic_account_id': f.analytic_id.id, 'memo': self.env._('%(f)s: feed of %(d)s', f=f.name, d=r.date),
                    'line_ids': [Command.create({'product_id': f.feed_product_id.id, 'quantity': r.feed_qty, 'account_id': account.id,
                                                 'job_id': f.job_id.id})]})
                giv.action_post()
                r.issue_id = giv
            products = self.env['sb.farm.setup']._products(c)
            lines = [(e.product_id, e.qty) for e in r.egg_ids if e.qty > 0 and e.product_id]
            if r.manure > 0 and products.get('manure'):
                lines.append((products['manure'], r.manure))
            if lines:
                vals = {'voucher_type': 'fprr', 'date': r.date, 'company_id': c.id, 'warehouse_id': wh.id,
                        'account_id': (c.sb_farm_cost_account_id or c.account_production_wip_account_id).id,
                        'analytic_account_id': f.analytic_id.id,
                        'memo': self.env._('%(f)s: collected on %(d)s', f=f.name, d=r.date),
                        'line_ids': [Command.create({'product_id': p.id, 'quantity': q, 'unit_cost': 0.0, 'job_id': f.job_id.id})
                                     for p, q in lines]}
                if 'sb_wip_costed' in Stock._fields:
                    vals['sb_wip_costed'] = False  # collected at no cost: the flock's costs are its own (cost per egg)
                rec = Stock.with_company(c).create(vals)
                rec.action_post()
                r.receipt_id = rec


class SbFarmRecordEgg(models.Model):
    _name = 'sb.farm.record.egg'
    _description = 'Eggs collected by grade'

    record_id = fields.Many2one('sb.farm.record', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', 'Grade', required=True)
    qty = fields.Integer('Eggs')


class SbFarmHealthTemplate(models.Model):
    _name = 'sb.farm.health.template'
    _description = 'Health programme (vaccines, deworming, vitamins)'
    _order = 'kind, age_day, id'

    company_id = fields.Many2one('res.company')
    kind = fields.Selection(KINDS, required=True, default='layer')
    name = fields.Char('What', required=True)
    age_day = fields.Integer('At age (days)', required=True)
    repeat_days = fields.Integer('Then every (days)', help='E.g. a Newcastle booster every 90 days while laying.')
    method = fields.Char('How', help='Drinking water, eye drop, injection ...')
    product_id = fields.Many2one('product.product', 'Product (from the store)')
    dose = fields.Float('Quantity per flock', help='Issued from the store when it is given (in the product unit).')
    withdrawal_days = fields.Integer('Withdrawal (days)', help='Days after it is given that eggs / meat may not be sold.')


class SbFarmHealth(models.Model):
    _name = 'sb.farm.health'
    _description = 'Health calendar of a flock'
    _order = 'date_due, id'

    flock_id = fields.Many2one('sb.farm.flock', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='flock_id.company_id', store=True)
    template_id = fields.Many2one('sb.farm.health.template', ondelete='set null')
    name = fields.Char(required=True)
    method = fields.Char()
    product_id = fields.Many2one('product.product')
    dose = fields.Float()
    date_due = fields.Date('Due', required=True, index=True)
    date_done = fields.Date('Given on')
    state = fields.Selection([('due', 'Due'), ('done', 'Given'), ('skipped', 'Skipped')], default='due', required=True, index=True)
    withdrawal_days = fields.Integer()
    given_by = fields.Many2one('res.users')
    issue_id = fields.Many2one('sb.stock.voucher', readonly=True, copy=False)
    note = fields.Char()

    def action_done(self, day=None, qty=None):
        """Given: the product is issued from the farm store to the flock."""
        Stock = self.env['sb.stock.voucher'].sudo()
        for h in self.filtered(lambda x: x.state == 'due'):
            f, c = h.flock_id, h.company_id
            day = day or fields.Date.context_today(self)
            qty = qty if qty is not None else h.dose
            if h.product_id and h.product_id.is_storable and qty and not _store_locked(self.env, c):
                wh = f.farm_id.warehouse_id or self.env['stock.warehouse'].sudo().search([('company_id', '=', c.id)], limit=1)
                account = c.sb_farm_cost_account_id
                if not account:
                    raise UserError(self.env._('Set the farm accounts in Farm > Setup first.'))
                giv = Stock.with_company(c).create({
                    'voucher_type': 'giv', 'date': day, 'company_id': c.id, 'warehouse_id': wh.id, 'account_id': account.id,
                    'analytic_account_id': f.analytic_id.id, 'memo': '%s: %s' % (f.name, h.name),
                    'line_ids': [Command.create({'product_id': h.product_id.id, 'quantity': qty, 'account_id': account.id,
                                                 'job_id': f.job_id.id})]})
                giv.action_post()
                h.issue_id = giv
            h.write({'state': 'done', 'date_done': day, 'given_by': self.env.uid, 'dose': qty or h.dose})
        return True

    def withdrawal_until(self):
        self.ensure_one()
        return self.date_done + timedelta(days=self.withdrawal_days) if self.state == 'done' and self.withdrawal_days else False
