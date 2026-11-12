"""What the Farm screens call: the farms with their flocks and alerts, the daily record (yesterday beside each field),
a flock (its figures against the breed, its health calendar, its money), selling, asking for feed, and setup."""
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

FEED_DAYS_WARN = 7       # feed in the store for fewer days than this: warn
DEATH_SPIKE = 3.0        # today's deaths over 3 x the average of the 7 days before: warn
DEATH_FLOOR = 0.005      # ... and over 0.5% of the birds


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbFarmService(models.AbstractModel):
    _name = 'sb.farm.service'
    _description = 'Farm'

    # ------------------------------------------------------------------ access
    def _is_manager(self):
        return self.env.su or self.env.user.has_group('sonador_farm.group_manager')

    def _require(self, manager=False):
        if manager and not self._is_manager():
            raise AccessError(self.env._('Only a farm manager can do this.'))
        if not (self.env.su or self.env.user.has_group('sonador_farm.group_worker')):
            raise AccessError(self.env._('Farm is for the farm team.'))

    def _F(self):
        return self.env['sb.farm.flock'].sudo()

    def _today(self):
        return fields.Date.context_today(self)

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        self._require()
        c = self.env.company
        if not (c.sb_farm_cost_account_id and c.sb_farm_egg_product_ids) and self._is_manager():
            # nothing to set up by hand: the accounts and items are made the first time the farm is opened
            self.env['sb.farm.setup'].sudo().make_defaults()
        prods = self.env['sb.farm.setup']._products(c)
        return {'is_manager': self._is_manager(), 'company': c.name, 'today': _s(self._today()), 'today_ec': format_ethiopian(self._today()),
                'ready': bool(c.sb_farm_cost_account_id and c.sb_farm_egg_product_ids),
                'egg_grades': [{'id': p.id, 'name': p.name} for p in prods['eggs']],
                'farms': [{'id': f.id, 'name': f.name, 'units': [{'id': u.id, 'name': u.name, 'capacity': u.capacity} for u in f.unit_ids]}
                          for f in self.env['sb.farm'].sudo().search([('company_id', '=', c.id)])],
                'breeds': [{'id': b.id, 'name': b.name, 'kind': b.kind} for b in self.env['sb.farm.breed'].sudo().search([])],
                'feeds': [{'id': p.id, 'name': p.display_name} for p in self.env['product.product'].sudo().search(
                    [('is_storable', '=', True), '|', ('categ_id.name', 'ilike', 'feed'), ('name', 'ilike', 'feed')], limit=50)],
                'last_feed': self._F().search([('company_id', '=', c.id), ('feed_product_id', '!=', False)], limit=1,
                                              order='id desc').feed_product_id.id or False,
                'my_flocks': [f.id for f in self._F().search([('company_id', '=', c.id), ('state', '=', 'active')])]}

    # ------------------------------------------------------------------ figures
    def _stock(self, product, farm):
        wh = farm.warehouse_id or self.env['stock.warehouse'].sudo().search([('company_id', '=', farm.company_id.id)], limit=1)
        return product.with_context(warehouse_id=wh.id).qty_available if product and wh else 0.0

    def _kpis(self, f):
        """The flock's figures: alive, mortality, eggs / laying %, feed per bird, weight, FCR, against the breed."""
        today = self._today()
        recs = f.record_ids.sorted('date')
        alive = f.alive()
        age = f.age_days()
        week = age // 7
        target = f.breed_id._target(week) if f.breed_id else False
        last7 = recs.filtered(lambda r: r.date > today - timedelta(days=7))
        d7 = max(len(last7), 1)
        out = {'alive': alive, 'age_days': age, 'age_weeks': round(age / 7.0, 1), 'placed': f.birds_placed,
               'dead_total': sum(recs.mapped('dead')), 'culled_total': sum(recs.mapped('culled')),
               'mortality_pct': round(100.0 * (sum(recs.mapped('dead')) + sum(recs.mapped('culled'))) / f.birds_placed, 2),
               'feed_total': round(sum(recs.mapped('feed_qty')), 1),
               'feed_g_bird': round(1000.0 * sum(last7.mapped('feed_qty')) / d7 / alive, 1) if alive else 0,
               'target_feed': target.feed if target else 0, 'today_recorded': bool(recs.filtered(lambda r: r.date == today)),
               'last_date': _s(recs[-1:].date) if recs else False}
        if f.kind == 'layer':
            eggs7 = sum(last7.mapped('eggs'))
            out.update(eggs_total=sum(recs.mapped('eggs')), eggs_today=sum(recs.filtered(lambda r: r.date == today).mapped('eggs')),
                       hen_day=round(100.0 * eggs7 / d7 / alive, 1) if alive else 0, target_hen_day=target.hen_day if target else 0,
                       broken_total=sum(recs.mapped('eggs_broken')))
        else:
            w = recs.filtered('weight')[-1:]
            live_kg = alive * (w.weight or 0) / 1000.0
            out_kg = sum(recs.mapped('birds_out')) * (w.weight or 0) / 1000.0
            out.update(weight=w.weight or 0, weight_date=_s(w.date), target_weight=target.weight if target else 0,
                       fcr=round(out['feed_total'] / (live_kg + out_kg), 2) if (live_kg + out_kg) else 0,
                       livability=round(100.0 * alive / f.birds_placed, 1))
        return out

    def _money(self, f):
        """The flock's money from the books: cost by kind (birds worn off / chicks, feed and medicine, labour, other),
        sales, profit, and cost per egg or per kg."""
        c = f.company_id
        act = self.env['sb.projects'].sudo()._actuals(f.job_id)[f.job_id.id] if f.job_id else {'cost': {}, 'revenue': 0.0, 'lines': []}
        cost = sum(act['cost'].values())
        feed = sum(l['cost'] for l in act['lines'] if l.get('model') == 'sb.stock.voucher')
        labour = sum(l['cost'] for l in act['lines'] if l.get('ref') == 'Timesheets')
        if f.kind == 'layer':
            birds = f._sb_depreciated()
        else:
            acc = c.sb_farm_birds_account_id
            birds = sum(l.price_subtotal for l in self.env['sb.voucher.line'].sudo().search(
                [('job_id', '=', f.job_id.id), ('voucher_id.state', '=', 'posted'), ('account_id', '=', acc.id),
                 ('voucher_id.voucher_type', 'in', ('bill', 'cash_purchase'))])) if acc else 0.0
        by = {'Birds': birds, 'Feed and medicine': feed, 'Labour': labour, 'Other costs': cost - birds - feed - labour}
        k = self._kpis(f)
        unit = 0.0
        if f.kind == 'layer' and k.get('eggs_total'):
            unit = cost / k['eggs_total']
        elif f.kind == 'broiler':
            kg = (k.get('alive', 0) + sum(f.record_ids.mapped('birds_out'))) * (k.get('weight') or 0) / 1000.0
            unit = cost / kg if kg else 0.0
        return {'cost': round(cost, 2), 'revenue': round(act['revenue'], 2), 'profit': round(act['revenue'] - cost, 2),
                'by': [{'name': n, 'amount': round(a, 2)} for n, a in by.items() if abs(a) >= 0.01],
                'bird_cost': round(f._sb_bird_cost(), 2), 'worn_off': round(f._sb_depreciated(), 2),
                'unit_cost': round(unit, 2), 'unit': 'egg' if f.kind == 'layer' else 'kg',
                'lines': sorted(act['lines'], key=lambda x: x.get('date') or '', reverse=True)[:60]}

    def _alerts(self, f, k=None):
        """What needs attention: deaths up, feed running out, medicine due or late, today not recorded, eggs not for sale."""
        k = k or self._kpis(f)
        today = self._today()
        out = []
        recs = f.record_ids.sorted('date')
        # deaths up on one of the last three days recorded, against the week before it
        for last in recs.filtered(lambda r: r.date >= today - timedelta(days=3)).sorted('date', reverse=True):
            before = recs.filtered(lambda r: last.date - timedelta(days=8) < r.date < last.date)
            avg = (sum(before.mapped('dead')) + sum(before.mapped('culled'))) / max(len(before), 1)
            lost = last.dead + last.culled
            if lost > DEATH_SPIKE * max(avg, 0.5) and lost > DEATH_FLOOR * max(k['alive'], 1):
                out.append({'level': 'bad', 'icon': 'fa-heartbeat',
                            'text': self.env._('%(n)s birds lost on %(d)s (usually %(a)s a day): check the house and call the vet',
                                               n=lost, d=format_ethiopian(last.date), a=round(avg, 1))})
                break
        if f.feed_product_id and k['alive']:
            daily = sum(recs.filtered(lambda r: r.date > today - timedelta(days=7)).mapped('feed_qty')) / 7.0
            stock = self._stock(f.feed_product_id, f.farm_id)
            if daily > 0 and stock / daily < FEED_DAYS_WARN:
                out.append({'level': 'warn', 'icon': 'fa-cubes', 'feed': True,
                            'text': self.env._('Feed for %(d)s more days (%(q)s kg of %(p)s in the store)',
                                               d=round(stock / daily, 1), q=round(stock), p=f.feed_product_id.name)})
        for h in f.health_ids.filtered(lambda x: x.state == 'due' and x.date_due <= today + timedelta(days=2)):
            late = h.date_due < today
            out.append({'level': 'bad' if late else 'info', 'icon': 'fa-medkit',
                        'text': ('%s: %s' % (self.env._('Late since %s', format_ethiopian(h.date_due)) if late else
                                             self.env._('Due %s', format_ethiopian(h.date_due)), h.name))})
        withdrawing = f.health_ids.filtered(lambda x: x.state == 'done' and x.withdrawal_days and x.withdrawal_until() and x.withdrawal_until() >= today)
        for h in withdrawing:
            out.append({'level': 'warn', 'icon': 'fa-ban', 'text': self.env._('%(what)s given: %(item)s not for sale until %(d)s',
                                                                               what=h.name, item=self.env._('eggs') if f.kind == 'layer' else self.env._('birds'),
                                                                               d=format_ethiopian(h.withdrawal_until()))})
        if f.state == 'active' and not k['today_recorded']:
            out.append({'level': 'info', 'icon': 'fa-pencil', 'text': self.env._("Today is not recorded yet")})
        return out

    def _card(self, f):
        k = self._kpis(f)
        return {'id': f.id, 'name': f.name, 'kind': f.kind, 'breed': f.breed_id.name or '', 'house': f.unit_id.name or '',
                'farm': f.farm_id.name, 'farm_id': f.farm_id.id, 'state': f.state, 'placed': _s(f.date_placed),
                'placed_ec': format_ethiopian(f.date_placed), 'k': k, 'alerts': self._alerts(f, k)}

    # ------------------------------------------------------------------ home
    @api.model
    def home(self):
        self._require()
        c = self.env.company
        flocks = self._F().search([('company_id', '=', c.id), ('state', '=', 'active')])
        farms = []
        for farm in self.env['sb.farm'].sudo().search([('company_id', '=', c.id)]):
            mine = flocks.filtered(lambda f: f.farm_id == farm)
            farms.append({'id': farm.id, 'name': farm.name, 'location': farm.location or '', 'manager': farm.manager_id.name or '',
                          'setup_job_id': farm.setup_job_id.id, 'flocks': [self._card(f) for f in mine]})
        closed = self._F().search([('company_id', '=', c.id), ('state', '=', 'closed')], limit=20)
        return {'farms': farms, 'closed': [{'id': f.id, 'name': f.name, 'kind': f.kind, 'closed': _s(f.date_closed),
                                            'closed_ec': format_ethiopian(f.date_closed)} for f in closed]}

    # ------------------------------------------------------------------ one flock
    @api.model
    def flock(self, flock_id):
        self._require()
        f = self._F().browse(int(flock_id)).exists()
        if not f:
            raise UserError(self.env._('This flock is gone.'))
        card = self._card(f)
        recs = f.record_ids.sorted('date')
        series = []
        for r in recs[-60:]:
            alive = f.alive(r.date)
            week = f.age_days(r.date) // 7
            t = f.breed_id._target(week) if f.breed_id else False
            series.append({'date': _s(r.date), 'label': format_ethiopian(r.date).rsplit(',', 1)[0], 'dead': r.dead + r.culled,
                           'hen_day': round(100.0 * r.eggs / alive, 1) if (alive and f.kind == 'layer') else 0,
                           'target_hen_day': t.hen_day if t else 0, 'weight': r.weight, 'target_weight': t.weight if t else 0,
                           'feed_g': round(1000.0 * r.feed_qty / alive, 1) if alive else 0})
        card.update({
            'unit_id': f.unit_id.id, 'breed_id': f.breed_id.id, 'feed_id': f.feed_product_id.id, 'feed': f.feed_product_id.display_name or '',
            'job_id': f.job_id.id, 'lay_until': _s(f.lay_until), 'lay_until_ec': format_ethiopian(f.lay_until) if f.lay_until else '',
            'age_placed': f.age_placed, 'supplier': f.supplier_id.name or '', 'note': f.note or '',
            'series': series, 'money': self._money(f) if self._is_manager() else None,
            'records': [{'id': r.id, 'date': _s(r.date), 'ec': format_ethiopian(r.date), 'dead': r.dead, 'culled': r.culled, 'out': r.birds_out,
                         'feed': r.feed_qty, 'eggs': r.eggs, 'broken': r.eggs_broken, 'weight': r.weight, 'manure': r.manure,
                         'note': r.note or '', 'by': r.recorded_by.name or '', 'issue': r.issue_id.name or '', 'receipt': r.receipt_id.name or ''}
                        for r in recs.sorted('date', reverse=True)[:90]],
            'health': [{'id': h.id, 'name': h.name, 'method': h.method or '', 'product': h.product_id.display_name or '', 'dose': h.dose,
                        'due': _s(h.date_due), 'due_ec': format_ethiopian(h.date_due), 'done_ec': format_ethiopian(h.date_done) if h.date_done else '',
                        'state': h.state, 'late': h.state == 'due' and h.date_due < self._today(), 'withdrawal': h.withdrawal_days,
                        'issue': h.issue_id.name or ''} for h in f.health_ids.sorted('date_due')]})
        return card

    # ------------------------------------------------------------------ daily record
    @api.model
    def record_form(self, flock_id, day=False):
        """The day's record of a flock (empty when new) with the day before beside each field."""
        self._require()
        f = self._F().browse(int(flock_id))
        day = fields.Date.to_date(day) if day else self._today()
        R = self.env['sb.farm.record'].sudo()
        r = R.search([('flock_id', '=', f.id), ('date', '=', day)], limit=1)
        prev = R.search([('flock_id', '=', f.id), ('date', '<', day)], order='date desc', limit=1)

        def vals(x):
            return {'dead': x.dead, 'culled': x.culled, 'out': x.birds_out, 'feed': x.feed_qty, 'water': x.water, 'weight': x.weight,
                    'broken': x.eggs_broken, 'manure': x.manure, 'note': x.note or '',
                    'eggs': {str(e.product_id.id): e.qty for e in x.egg_ids}} if x else None
        return {'flock': {'id': f.id, 'name': f.name, 'kind': f.kind, 'alive': f.alive(day - timedelta(days=1)),
                          'feed': f.feed_product_id.display_name or '', 'feed_stock': round(self._stock(f.feed_product_id, f.farm_id), 1)},
                'date': _s(day), 'date_ec': format_ethiopian(day), 'exists': bool(r), 'values': vals(r) or {}, 'before': vals(prev),
                'store_skipped': bool(r and r.store_skipped),
                'before_ec': format_ethiopian(prev.date) if prev else '', 'can_edit': not r or self._is_manager() or r.recorded_by == self.env.user}

    @api.model
    def save_record(self, flock_id, day, vals):
        """Save a day: the feed is issued and the eggs and manure received in the store at once (again when it changed)."""
        self._require()
        f = self._F().browse(int(flock_id))
        day = fields.Date.to_date(day)
        if day > self._today():
            raise UserError(self.env._('A day that has not come yet cannot be recorded.'))
        R = self.env['sb.farm.record'].sudo()
        r = R.search([('flock_id', '=', f.id), ('date', '=', day)], limit=1)
        if r and not (self._is_manager() or r.recorded_by == self.env.user):
            raise AccessError(self.env._('Only the farm manager changes a day someone else recorded.'))
        num = lambda k: float(vals.get(k) or 0)  # noqa: E731
        data = {'dead': int(num('dead')), 'culled': int(num('culled')), 'birds_out': int(num('out')), 'feed_qty': num('feed'),
                'water': num('water'), 'weight': num('weight'), 'eggs_broken': int(num('broken')), 'manure': num('manure'),
                'note': vals.get('note') or False}
        if any(data[k] < 0 for k in ('dead', 'culled', 'birds_out', 'feed_qty', 'eggs_broken', 'manure')):
            raise UserError(self.env._('Numbers cannot be below zero.'))
        lost = data['dead'] + data['culled'] + data['birds_out']
        if lost > f.alive(day - timedelta(days=1)) + (r.dead + r.culled + r.birds_out if r else 0):
            raise UserError(self.env._('More birds lost than there are in the flock.'))
        eggs = [(int(pid), int(q or 0)) for pid, q in (vals.get('eggs') or {}).items() if int(q or 0) > 0]
        data['egg_ids'] = [Command.clear()] + [Command.create({'product_id': p, 'qty': q}) for p, q in eggs]
        today = self._today()
        data['withdrawal'] = bool(f.health_ids.filtered(lambda h: h.state == 'done' and h.withdrawal_until() and h.withdrawal_until() >= day))
        if r:
            changed = (r.feed_qty != data['feed_qty'] or r.manure != data['manure']
                       or sorted((e.product_id.id, e.qty) for e in r.egg_ids) != sorted(eggs))
            r.write(data)
        else:
            r = R.create(dict(data, flock_id=f.id, date=day))
            changed = True
        if changed:
            r._sb_post_store()
        if r.date == today and f.state == 'planned':
            f.state = 'active'
        return {'record': self.record_form(f.id, _s(day)), 'card': self._card(f)}

    # ------------------------------------------------------------------ health
    @api.model
    def health_done(self, health_id, qty=None, day=False):
        self._require()
        h = self.env['sb.farm.health'].sudo().browse(int(health_id))
        h.action_done(fields.Date.to_date(day) if day else None, float(qty) if qty not in (None, '') else None)
        return self.flock(h.flock_id.id)

    @api.model
    def health_skip(self, health_id, note=''):
        self._require(manager=True)
        h = self.env['sb.farm.health'].sudo().browse(int(health_id))
        h.write({'state': 'skipped', 'note': note or False})
        return self.flock(h.flock_id.id)

    @api.model
    def health_add(self, flock_id, vals):
        self._require(manager=True)
        f = self._F().browse(int(flock_id))
        self.env['sb.farm.health'].sudo().create({'flock_id': f.id, 'name': vals['name'], 'date_due': vals['date'], 'method': vals.get('method') or False,
                                                   'product_id': int(vals['product_id']) if vals.get('product_id') else False,
                                                   'dose': float(vals.get('dose') or 0), 'withdrawal_days': int(vals.get('withdrawal') or 0)})
        return self.flock(f.id)

    # ------------------------------------------------------------------ flocks
    @api.model
    def flock_save(self, flock_id, vals):
        """A new flock (or its details changed)."""
        self._require(manager=True)
        data = {'name': vals['name'], 'farm_id': int(vals['farm_id']), 'unit_id': int(vals['unit_id']) if vals.get('unit_id') else False,
                'kind': vals.get('kind') or 'layer', 'breed_id': int(vals['breed_id']) if vals.get('breed_id') else False,
                'date_placed': vals.get('date_placed') or _s(self._today()), 'age_placed': int(vals.get('age_placed') or 1),
                'birds_placed': int(vals.get('birds_placed') or 0),
                'feed_product_id': int(vals['feed_id']) if vals.get('feed_id') else False, 'note': vals.get('note') or False,
                'lay_until': vals.get('lay_until') or False}
        if data['birds_placed'] <= 0:
            raise UserError(self.env._('How many birds?'))
        if flock_id:
            f = self._F().browse(int(flock_id))
            f.write(data)
        else:
            f = self._F().create(data)
        return f.id

    @api.model
    def flock_close(self, flock_id, day=False):
        self._require(manager=True)
        f = self._F().browse(int(flock_id))
        f.action_close(fields.Date.to_date(day) if day else None)
        if f.kind == 'layer':
            f.action_depreciate(f.date_closed)
        return self.flock(f.id)

    @api.model
    def depreciate(self, flock_id):
        self._require(manager=True)
        f = self._F().browse(int(flock_id))
        v = f.action_depreciate()
        if not v:
            raise UserError(self.env._('Nothing to post: the birds\' cost is worn off to yesterday already (or no bird cost is on the flock yet).'))
        return self.flock(f.id)

    # ------------------------------------------------------------------ money in and out
    @api.model
    def sell(self, flock_id, credit=False):
        """A sale of the flock's eggs or birds, ready to fill: a cash sale (or a credit sale), its lines on the flock."""
        self._require(manager=True)
        f = self._F().browse(int(flock_id))
        prods = self.env['sb.farm.setup']._products(f.company_id)
        items = prods['eggs'] if f.kind == 'layer' else prods['broiler']
        income = self.env['account.account'].sudo().search([('account_type', '=', 'income'), ('company_ids', 'in', f.company_id.id)], limit=1)
        v = self.env['sb.voucher'].create({
            'voucher_type': 'sale' if credit else 'cash_sale', 'memo': self.env._('Sale from %s', f.name),
            'line_ids': [Command.create({'product_id': p.id, 'name': p.display_name, 'quantity': 1, 'price_unit': p.lst_price,
                                         'job_id': f.job_id.id, 'account_id': (p.property_account_income_id or p.categ_id.property_account_income_categ_id or income).id})
                         for p in items]})
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': v.id, 'view_mode': 'form', 'target': 'current',
                'name': self.env._('Sale')}

    @api.model
    def request_feed(self, flock_id, qty):
        """A purchase request for feed (or anything) for the flock: purchasing turns it into an order, the bill lands on the flock."""
        self._require()
        f = self._F().browse(int(flock_id))
        if not f.feed_product_id:
            raise UserError(self.env._('Choose the feed of %s first.', f.name))
        line = {'product_id': f.feed_product_id.id, 'name': f.feed_product_id.display_name, 'job_id': f.job_id.id}
        if 'sb.purchase.request' in self.env:
            req = self.env['sb.purchase.request'].create({
                'description': self.env._('Feed for %s', f.name),
                'line_ids': [Command.create(dict(line, qty=float(qty)))]})
            return {'type': 'ir.actions.act_window', 'res_model': 'sb.purchase.request', 'res_id': req.id, 'view_mode': 'form', 'target': 'current'}
        vendor = self.env['product.supplierinfo'].sudo().search([('product_tmpl_id', '=', f.feed_product_id.product_tmpl_id.id)], limit=1).partner_id
        if not vendor:
            raise UserError(self.env._('Who sells %s? Add a vendor to the item, or make the order in Purchase.', f.feed_product_id.display_name))
        po = self.env['purchase.order'].create({'partner_id': vendor.id, 'origin': f.name,
                                                'order_line': [Command.create(dict(line, product_qty=float(qty)))]})
        return {'type': 'ir.actions.act_window', 'res_model': 'purchase.order', 'res_id': po.id, 'view_mode': 'form', 'target': 'current'}

    # ------------------------------------------------------------------ setup
    @api.model
    def setup(self):
        self._require(manager=True)
        c = self.env.company
        acc = lambda a: {'id': a.id, 'name': a.display_name} if a else False  # noqa: E731
        return {'farms': [{'id': f.id, 'name': f.name, 'location': f.location or '', 'manager_id': f.manager_id.id,
                           'warehouse_id': f.warehouse_id.id, 'worker_ids': f.worker_ids.ids, 'setup_job_id': f.setup_job_id.id,
                           'units': [{'id': u.id, 'name': u.name, 'kind': u.kind, 'capacity': u.capacity} for u in f.unit_ids]}
                          for f in self.env['sb.farm'].sudo().search([('company_id', '=', c.id)])],
                'warehouses': [{'id': w.id, 'name': w.name} for w in self.env['stock.warehouse'].sudo().search([('company_id', '=', c.id)])],
                'employees': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search([('company_id', '=', c.id)], order='name')],
                'accounts': {'bio': acc(c.sb_farm_bio_account_id), 'bio_dep': acc(c.sb_farm_bio_dep_account_id), 'dep': acc(c.sb_farm_dep_account_id),
                             'cost': acc(c.sb_farm_cost_account_id), 'chicks': acc(c.sb_farm_birds_account_id)},
                'products': {'eggs': [p.display_name for p in c.sb_farm_egg_product_ids], 'manure': c.sb_farm_manure_product_id.display_name or '',
                             'broiler': c.sb_farm_broiler_product_id.display_name or ''},
                'breeds': [{'id': b.id, 'name': b.name, 'kind': b.kind, 'until': b.lay_until_week, 'targets': len(b.target_ids)}
                           for b in self.env['sb.farm.breed'].sudo().search([])],
                'health': [{'id': t.id, 'kind': t.kind, 'name': t.name, 'age': t.age_day, 'repeat': t.repeat_days, 'method': t.method or '',
                            'withdrawal': t.withdrawal_days} for t in self.env['sb.farm.health.template'].sudo().search([])]}

    @api.model
    def make_defaults(self):
        self._require(manager=True)
        self.env['sb.farm.setup'].sudo().make_defaults()
        return self.setup()

    @api.model
    def quick_start(self, vals):
        """The first farm in one step: its name, where it is, and its houses (how many, birds each)."""
        self._require(manager=True)
        n = max(int(vals.get('houses') or 1), 1)
        cap = int(vals.get('capacity') or 0)
        wh = self.env['stock.warehouse'].sudo().search([('company_id', '=', self.env.company.id)], limit=1)
        self.farm_save(False, {'name': vals['name'], 'location': vals.get('location') or False, 'warehouse_id': wh.id,
                               'units': [{'name': self.env._('House %s', i + 1), 'kind': 'house', 'capacity': cap} for i in range(n)]})
        return self.ctx()

    @api.model
    def farm_save(self, farm_id, vals):
        self._require(manager=True)
        F = self.env['sb.farm'].sudo()
        data = {'name': vals['name'], 'location': vals.get('location') or False,
                'manager_id': int(vals['manager_id']) if vals.get('manager_id') else False,
                'warehouse_id': int(vals['warehouse_id']) if vals.get('warehouse_id') else False,
                'worker_ids': [Command.set([int(x) for x in vals.get('worker_ids') or []])]}
        farm = F.browse(int(farm_id)) if farm_id else F.create(data)
        if farm_id:
            farm.write(data)
        keep = []
        for u in vals.get('units') or []:
            if not (u.get('name') or '').strip():
                continue
            uv = {'name': u['name'], 'kind': u.get('kind') or 'house', 'capacity': int(u.get('capacity') or 0), 'farm_id': farm.id}
            unit = self.env['sb.farm.unit'].sudo().browse(int(u['id'])) if u.get('id') else self.env['sb.farm.unit'].sudo().create(uv)
            unit.write(uv)
            keep.append(unit.id)
        (farm.unit_ids - farm.unit_ids.browse(keep)).write({'active': False})
        if vals.get('make_setup_job') and not farm.setup_job_id:
            farm.setup_job_id = self.env['sb.job'].sudo().create({'name': self.env._('%s: setting up (houses, ventilation, water, lights)', farm.name),
                                                                    'kind': 'internal', 'state': 'active', 'company_id': farm.company_id.id,
                                                                    'manager_id': farm.manager_id.id or False, 'site': farm.location or farm.name})
        return self.setup()

    @api.model
    def template_save(self, template_id, vals):
        self._require(manager=True)
        T = self.env['sb.farm.health.template'].sudo()
        data = {'kind': vals.get('kind') or 'layer', 'name': vals['name'], 'age_day': int(vals.get('age') or 1),
                'repeat_days': int(vals.get('repeat') or 0), 'method': vals.get('method') or False,
                'withdrawal_days': int(vals.get('withdrawal') or 0), 'company_id': self.env.company.id}
        if template_id:
            T.browse(int(template_id)).write(data)
        else:
            T.create(data)
        return self.setup()

    @api.model
    def template_delete(self, template_id):
        self._require(manager=True)
        self.env['sb.farm.health.template'].sudo().browse(int(template_id)).unlink()
        return self.setup()
