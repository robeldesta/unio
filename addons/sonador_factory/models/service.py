"""Calls of the Production screens: the board (machines × days), a production order run from the floor, material
shortages with purchase requests, analytics (output, yield, waste, downtime, OEE, cost per unit) and setup."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian


def _s(d):
    return fields.Date.to_string(d) if d else False


def shift_minutes(shift):
    if not shift:
        return 480
    h = shift.hour_to - shift.hour_from
    return int(round((h if h > 0 else h + 24) * 60))


class SbFactory(models.AbstractModel):
    _name = 'sb.factory'
    _description = 'Production floor'

    # ------------------------------------------------------------------ access
    def _is_manager(self):
        return self.env.su or self.env.user.has_group('sonador_factory.group_manager')

    def _require(self, manager=False):
        if manager and not self._is_manager():
            raise AccessError(self.env._('Only a production manager can do this.'))
        if not (self.env.su or self.env.user.has_group('sonador_factory.group_operator')):
            raise AccessError(self.env._('Production is for the factory team.'))

    def _O(self):
        return self.env['sb.mfg.order'].sudo()

    def _wh(self):
        return self.env['stock.warehouse'].sudo().search([('company_id', '=', self.env.company.id)], limit=1)

    def _onhand(self, products):
        wh = self._wh()
        out = {}
        for p in products:
            out[p.id] = p.with_context(warehouse_id=wh.id).qty_available if wh else p.qty_available
        return out

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        self._require()
        c = self.env.company
        products = self.env['mrp.bom'].sudo().search([('company_id', 'in', (c.id, False))]).mapped('product_tmpl_id.product_variant_ids')
        return {'is_manager': self._is_manager(), 'company': c.name, 'today': _s(fields.Date.context_today(self)),
                'today_ec': format_ethiopian(fields.Date.context_today(self)),
                'lines': [{'id': l.id, 'name': l.name, 'code': l.code or '', 'speed': l.rated_speed, 'unit': l.speed_unit or '',
                           'labour': l.labour_rate, 'overhead': l.overhead_rate, 'crew': l.crew}
                          for l in self.env['sb.mfg.line'].sudo().search([('company_id', '=', c.id)])],
                'shifts': [{'id': s.id, 'name': s.name, 'from': s.hour_from, 'to': s.hour_to, 'minutes': shift_minutes(s)}
                           for s in self.env['sb.mfg.shift'].sudo().search([('company_id', '=', c.id)])],
                'reasons': [{'id': r.id, 'name': r.name, 'kind': r.kind, 'planned': r.planned}
                            for r in self.env['sb.mfg.reason'].sudo().search([('company_id', '=', c.id)])],
                'products': [{'id': p.id, 'name': p.display_name, 'uom': p.uom_id.name, 'weight': p.sb_unit_weight} for p in products],
                'people': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search(
                    [('company_id', '=', c.id), ('active', '=', True)], order='name')]}

    # ------------------------------------------------------------------ board
    def _card(self, o, onhand=None):
        short = []
        if o.state in ('draft', 'planned') and onhand is not None:
            for m in o.material_ids:
                need = max(m.qty_required - m.qty_consumed, 0)
                if need > onhand.get(m.product_id.id, 0) + 1e-6:
                    short.append(m.product_id.display_name)
        return {'id': o.id, 'name': o.name, 'product': o.product_id.display_name, 'product_id': o.product_id.id, 'qty': o.qty_planned,
                'uom': o.uom_name, 'good': o.qty_good, 'state': o.state, 'line_id': o.line_id.id or False, 'date': _s(o.date_planned),
                'shift': o.shift_id.name or '', 'shift_id': o.shift_id.id or False, 'priority': o.priority,
                'customer': o.partner_id.name or '', 'short': short, 'lot': o.lot_name or '',
                'pct': round(100 * o.qty_good / o.qty_planned) if o.qty_planned else 0,
                'late': bool(o.date_planned and o.state in ('draft', 'planned') and o.date_planned < fields.Date.context_today(self))}

    @api.model
    def board(self, start=False, days=7):
        self._require()
        today = fields.Date.context_today(self)
        d0 = fields.Date.to_date(start) if start else today - timedelta(days=1)
        d1 = d0 + timedelta(days=int(days) - 1)
        O = self._O()
        orders = O.search([('company_id', '=', self.env.company.id), ('state', '!=', 'cancel'), '|',
                           '&', ('date_planned', '>=', d0), ('date_planned', '<=', d1),
                           '|', ('state', 'in', ('running', 'paused')), '&', ('state', 'in', ('draft', 'planned')),
                           '|', ('date_planned', '=', False), ('line_id', '=', False)])
        onhand = self._onhand(orders.material_ids.product_id)
        days_l = []
        d = d0
        while d <= d1:
            days_l.append({'date': _s(d), 'name': d.strftime('%a'), 'ec': format_ethiopian(d), 'today': d == today})
            d += timedelta(days=1)
        late = O.search_count([('company_id', '=', self.env.company.id), ('state', 'in', ('draft', 'planned')), ('date_planned', '<', today)])
        return {'start': _s(d0), 'end': _s(d1), 'label': '%s – %s' % (format_ethiopian(d0), format_ethiopian(d1)),
                'prev': _s(d0 - timedelta(days=7)), 'next': _s(d0 + timedelta(days=7)), 'days': days_l,
                'orders': [self._card(o, onhand) for o in orders], 'late': late}

    @api.model
    def move(self, order_id, line_id, day, shift_id=False):
        self._require(manager=True)
        o = self._O().browse(int(order_id))
        if o.state in ('done', 'cancel'):
            raise UserError(self.env._('%s is finished.', o.name))
        o.write({'line_id': int(line_id) if line_id else False, 'date_planned': day or False,
                 'shift_id': int(shift_id) if shift_id else o.shift_id.id or False})
        return self._card(o, self._onhand(o.material_ids.product_id))

    # ------------------------------------------------------------------ order
    def _materials_vals(self, o):
        bom = o.bom_id
        if not bom:
            return []
        factor = (o.qty_planned or 0.0) / (bom.product_qty or 1.0)
        out = []
        for i, bl in enumerate(bom.bom_line_ids):
            p = bl.product_id
            out.append((0, 0, {'sequence': i, 'product_id': p.id, 'qty_required': bl.product_uom_id._compute_quantity(bl.product_qty * factor, p.uom_id)}))
        return out

    @api.model
    def save_order(self, order_id, vals):
        self._require(manager=True)
        O = self._O()
        w = {}
        for k in ('lot_name', 'note'):
            if k in vals:
                w[k] = (vals[k] or '').strip() or False
        for k in ('product_id', 'line_id', 'shift_id', 'partner_id'):
            if k in vals:
                w[k] = int(vals[k]) if vals[k] else False
        if 'qty_planned' in vals:
            w['qty_planned'] = float(vals['qty_planned'] or 0)
        if 'date_planned' in vals:
            w['date_planned'] = vals['date_planned'] or False
        if 'priority' in vals:
            w['priority'] = bool(vals['priority'])
        if order_id:
            o = O.browse(int(order_id))
            if o.state in ('done', 'cancel'):
                raise UserError(self.env._('%s is finished.', o.name))
            o.write(w)
        else:
            if not w.get('product_id') or not w.get('qty_planned'):
                raise ValidationError(self.env._('Choose the product and the quantity.'))
            o = O.create(dict(w, company_id=self.env.company.id, state='planned' if w.get('line_id') and w.get('date_planned') else 'draft'))
        if {'product_id', 'qty_planned'} & set(w) or not o.material_ids:
            o.bom_id = self.env['mrp.bom']._sb_for(o.product_id, o.date_planned or fields.Date.context_today(self))
            if o.state in ('draft', 'planned') and not o.entry_ids:
                o.material_ids = [(5, 0, 0)] + self._materials_vals(o)
        if not o.check_ids:
            T = self.env['sb.mfg.check.template'].sudo().search([('company_id', '=', o.company_id.id), '|',
                                                                  ('product_ids', '=', False), ('product_ids', 'in', o.product_id.ids)])
            o.check_ids = [(0, 0, {'template_id': t.id, 'sequence': t.sequence, 'name': t.name, 'kind': t.kind, 'min_value': t.min_value,
                                   'max_value': t.max_value, 'unit': t.unit or False, 'blocking': t.blocking}) for t in T]
        if o.state == 'draft' and o.line_id and o.date_planned:
            o.state = 'planned'
        return self.order(o.id)

    def _summary(self, o):
        good, waste = o.qty_good, o.qty_waste
        losses = o.entry_ids.loss_ids
        waste_kg = sum(losses.filtered(lambda l: l.kind == 'waste' and l.in_kg).mapped('qty'))
        unplanned = sum(losses.filtered(lambda l: l.kind == 'downtime' and not l.planned).mapped('minutes'))
        planned = sum(losses.filtered(lambda l: l.kind == 'downtime' and l.planned).mapped('minutes'))
        run = o.run_minutes
        avail = run / (run + unplanned) if (run + unplanned) else None
        speed = o.line_id.rated_speed
        perf = ((good + waste) / (run / 60.0 * speed)) if (run and speed) else None
        qual = good / (good + waste) if (good + waste) else None
        oee = avail * perf * qual if None not in (avail, perf, qual) else None
        w = o.product_id.sb_unit_weight
        kg_in = sum(m.qty_consumed for m in o.material_ids if (m.product_id.uom_id.name or '').lower() in ('kg', 'kgs', 'kilogram', 'kilograms'))
        kg_out = good * w if w else None
        hours = run / 60.0
        labour = hours * (o.line_id.labour_rate or 0.0)
        overhead = hours * (o.line_id.overhead_rate or 0.0)
        share = (good / o.qty_planned) if o.qty_planned else 0
        material = sum((m.qty_consumed or m.qty_required * share) * m.product_id.standard_price for m in o.material_ids)
        return {'good': good, 'waste': waste, 'waste_kg': waste_kg, 'run': run, 'down': unplanned, 'planned_down': planned,
                'availability': round(100 * avail, 1) if avail is not None else None,
                'performance': round(100 * min(perf, 1.5), 1) if perf is not None else None,
                'quality': round(100 * qual, 1) if qual is not None else None,
                'oee': round(100 * min(oee, 1.0), 1) if oee is not None else None,
                'kg_in': round(kg_in, 2), 'kg_out': round(kg_out, 2) if kg_out is not None else None,
                'yield': round(100 * kg_out / kg_in, 1) if kg_in and kg_out else None,
                'labour': round(labour, 2), 'overhead': round(overhead, 2), 'material_est': round(material, 2),
                'unit_cost_est': round((material + labour + overhead) / good, 4) if good else None}

    def _posted_cost(self, v):
        """{'value', 'unit_cost'} of the voucher an order was finished on: an FPRR (now), or a Build (before)."""
        if v.voucher_type == 'fprr':
            lines = v.line_ids.filtered('product_id')
            value, qty = sum(lines.mapped('value')), sum(lines.mapped('quantity'))
            return {'value': value, 'unit_cost': round(value / qty, 4) if qty else 0.0}
        return {'value': v.build_value, 'unit_cost': v.build_unit_cost}

    @api.model
    def order(self, order_id):
        self._require()
        o = self._O().browse(int(order_id))
        onhand = self._onhand(o.material_ids.product_id)
        return dict(self._card(o, onhand), line=o.line_id.name or '', bom=o.bom_id.display_name or '', note=o.note or '',
                    partner_id=o.partner_id.id or False, started=o.started_at and fields.Datetime.to_string(o.started_at) or '',
                    voucher=o.voucher_id and dict({'id': o.voucher_id.id, 'name': o.voucher_id.name}, **self._posted_cost(o.voucher_id)) or False,
                    materials=[{'id': m.id, 'product': m.product_id.display_name, 'uom': m.uom_name, 'required': m.qty_required,
                                'consumed': m.qty_consumed, 'lots': m.lots or '', 'onhand': onhand.get(m.product_id.id, 0),
                                'expected': round(m.qty_required * (o.qty_good / o.qty_planned), 4) if o.qty_planned else 0}
                               for m in o.material_ids],
                    entries=[{'id': e.id, 'date': _s(e.date), 'ec': format_ethiopian(e.date), 'shift': e.shift_id.name or '',
                              'who': e.employee_id.name or e.by_id.name or '', 'run': e.minutes_run, 'good': e.good_qty, 'note': e.note or '',
                              'losses': [{'kind': l.kind, 'reason': l.reason_id.name, 'minutes': l.minutes, 'qty': l.qty, 'kg': l.in_kg}
                                         for l in e.loss_ids]} for e in o.entry_ids],
                    checks=[{'id': c.id, 'name': c.name, 'kind': c.kind, 'min': c.min_value, 'max': c.max_value, 'unit': c.unit or '',
                             'value': c.value, 'result': c.result, 'blocking': c.blocking, 'by': c.by_id.name or '', 'note': c.note or ''}
                            for c in o.check_ids],
                    summary=self._summary(o), can_manage=self._is_manager())

    @api.model
    def set_state(self, order_id, action, force=False):
        self._require()
        o = self._O().browse(int(order_id))
        if action == 'start':
            if o.state not in ('draft', 'planned', 'paused'):
                raise UserError(self.env._('%s cannot start from "%s".', o.name, o.state))
            if not o.line_id:
                raise ValidationError(self.env._('Put the order on a machine first.'))
            card = self._card(o, self._onhand(o.material_ids.product_id)) if o.state != 'paused' else {'short': []}
            if card['short'] and not force:
                raise UserError(self.env._('Not enough %s in stock for the whole order. Start anyway from the order if you have it on the floor.',
                                           ', '.join(card['short'])))
            o.write({'state': 'running', 'started_at': o.started_at or fields.Datetime.now()})
        elif action == 'pause':
            o.state = 'paused'
        elif action == 'cancel':
            self._require(manager=True)
            if o.entry_ids:
                raise UserError(self.env._('Output is already recorded: finish the order instead.'))
            o.state = 'cancel'
        return self.order(o.id)

    @api.model
    def record(self, order_id, vals):
        """One shift's record: good output, waste and downtime."""
        self._require()
        o = self._O().browse(int(order_id))
        if o.state not in ('running', 'paused'):
            raise UserError(self.env._('Start the order before recording output.'))
        shift = self.env['sb.mfg.shift'].sudo().browse(int(vals['shift_id'])) if vals.get('shift_id') else self.env['sb.mfg.shift']
        losses = []
        down = 0
        for l in vals.get('losses') or []:
            if not l.get('reason_id'):
                continue
            r = self.env['sb.mfg.reason'].sudo().browse(int(l['reason_id']))
            if r.kind == 'downtime':
                m = int(float(l.get('minutes') or 0))
                if m > 0:
                    losses.append((0, 0, {'kind': 'downtime', 'reason_id': r.id, 'minutes': m}))
                    down += m
            else:
                q = float(l.get('qty') or 0)
                if q > 0:
                    losses.append((0, 0, {'kind': 'waste', 'reason_id': r.id, 'qty': q, 'in_kg': bool(l.get('in_kg'))}))
        good = float(vals.get('good_qty') or 0)
        if good < 0:
            raise ValidationError(self.env._('Output cannot be negative.'))
        run = int(float(vals['minutes_run'])) if vals.get('minutes_run') not in (None, '') else max(shift_minutes(shift) - down, 0)
        if run + down > 24 * 60:
            raise ValidationError(self.env._('Running and stopped time add up to more than a day.'))
        speed = o.line_id.rated_speed
        if speed and run and good > speed * run / 60.0 * 1.3:
            raise ValidationError(self.env._('%(g)s in %(h)s hours is faster than the machine can go (%(s)s an hour). Check the figures.',
                                             g='{:,.0f}'.format(good), h=round(run / 60.0, 1), s='{:,.0f}'.format(speed)))
        if not good and not losses:
            raise ValidationError(self.env._('Record the output, the waste or the stops.'))
        self.env['sb.mfg.entry'].sudo().create({'order_id': o.id, 'date': vals.get('date') or fields.Date.context_today(self),
                                                'shift_id': shift.id or False, 'employee_id': int(vals['employee_id']) if vals.get('employee_id') else False,
                                                'minutes_run': run, 'good_qty': good, 'note': (vals.get('note') or '').strip() or False,
                                                'loss_ids': losses, 'by_id': self.env.uid})
        return self.order(o.id)

    @api.model
    def remove_entry(self, entry_id):
        self._require()
        e = self.env['sb.mfg.entry'].sudo().browse(int(entry_id))
        if e.order_id.state == 'done':
            raise UserError(self.env._('The order is finished and posted.'))
        if e.by_id != self.env.user and not self._is_manager():
            raise AccessError(self.env._('Only who recorded it or a manager can remove it.'))
        o = e.order_id
        e.unlink()
        return self.order(o.id)

    @api.model
    def save_materials(self, order_id, rows):
        self._require()
        o = self._O().browse(int(order_id))
        if o.state == 'done':
            raise UserError(self.env._('The order is finished and posted.'))
        M = self.env['sb.mfg.material'].sudo()
        for r in rows:
            m = M.browse(int(r['id']))
            if m.order_id != o:
                continue
            q = float(r.get('consumed') or 0)
            if q < 0:
                raise ValidationError(self.env._('Quantities cannot be negative.'))
            m.write({'qty_consumed': q, 'lots': (r.get('lots') or '').strip() or False})
        return self.order(o.id)

    @api.model
    def save_check(self, check_id, vals):
        self._require()
        c = self.env['sb.mfg.check'].sudo().browse(int(check_id))
        if c.order_id.state == 'done':
            raise UserError(self.env._('The order is finished and posted.'))
        if c.kind == 'number':
            if vals.get('value') in (None, ''):
                raise ValidationError(self.env._('Enter the measured value.'))
            v = float(vals['value'])
            ok = (not c.min_value or v >= c.min_value) and (not c.max_value or v <= c.max_value)
            c.write({'value': v, 'result': 'pass' if ok else 'fail'})
        else:
            c.result = 'pass' if vals.get('result') == 'pass' else 'fail'
        c.write({'by_id': self.env.uid, 'at': fields.Datetime.now(), 'note': (vals.get('note') or '').strip() or False})
        return self.order(c.order_id.id)

    @api.model
    def finish(self, order_id, use_expected=False):
        """Close the order and post the Finished Product Receipt: actual materials, labour and overhead from the running hours."""
        self._require(manager=True)
        o = self._O().browse(int(order_id))
        if o.state not in ('running', 'paused'):
            raise UserError(self.env._('Only a started order can be finished.'))
        if o.qty_good <= 0:
            raise ValidationError(self.env._('No good output is recorded.'))
        failed = o.check_ids.filtered(lambda c: c.blocking and c.result != 'pass')
        if failed:
            raise ValidationError(self.env._('Quality checks not passed: %s.', ', '.join(failed.mapped('name'))))
        mats = o.material_ids
        if use_expected:
            for m in mats.filtered(lambda m: not m.qty_consumed):
                m.qty_consumed = m.qty_required * (o.qty_good / o.qty_planned) if o.qty_planned else 0
        if mats and not any(m.qty_consumed for m in mats):
            raise ValidationError(self.env._('Enter what material was used (or use the recipe quantities for the output).'))
        # The materials came out of the store on the order's GIVs (Dr WIP / Cr inventory). Finishing receives the good
        # output on an FPRR: Dr finished goods / Cr WIP (this order's GIVs) and the labour and overhead absorbed.
        # Nothing is taken from stock here: what was used is recorded above for the usage report only.
        wip = o.company_id.account_production_wip_account_id
        if not wip:
            raise UserError(self.env._('Set the work in progress account of the company (Settings) first.'))
        if not o._sb_waiting_givs():
            raise UserError(self.env._(
                'No materials have been issued to %s yet. The store issues them on a GIV for this production order '
                '(Inventory › Issue, choose the production order), then finish it here.', o.name))
        hours = o.run_minutes / 60.0
        wh = self._wh()
        V = self.env['sb.stock.voucher'].sudo()
        v = V.create({'voucher_type': 'fprr', 'company_id': o.company_id.id, 'warehouse_id': wh.id, 'date': fields.Date.context_today(self),
                      'sb_mfg_order_id': o.id, 'sb_wip_costed': True, 'account_id': wip.id,
                      'reference': o.name, 'memo': ('%s %s' % (o.name, ('lot ' + o.lot_name) if o.lot_name else '')).strip(),
                      'sb_labour_total': round(hours * (o.line_id.labour_rate or 0.0), 2),
                      'sb_overhead_total': round(hours * (o.line_id.overhead_rate or 0.0), 2),
                      'line_ids': [(0, 0, {'product_id': o.product_id.id, 'name': o.product_id.display_name, 'quantity': o.qty_good,
                                           'unit_cost': o.product_id.standard_price or 0.0})]})
        v.action_post()
        o.write({'state': 'done', 'finished_at': fields.Datetime.now(), 'voucher_id': v.id})
        return self.order(o.id)

    # ------------------------------------------------------------------ materials
    @api.model
    def shortages(self):
        self._require()
        orders = self._O().search([('company_id', '=', self.env.company.id), ('state', 'in', ('draft', 'planned', 'running', 'paused'))],
                                  order='date_planned')
        need = defaultdict(float)
        users = defaultdict(list)
        for o in orders:
            issued = o._sb_issued()  # materials leave stock on the order's GIVs: those are no longer needed from stock
            for m in o.material_ids:
                rest = max(m.qty_required - issued.get(m.product_id, 0.0), 0)
                if rest:
                    need[m.product_id] += rest
                    users[m.product_id].append('%s (%s)' % (o.name, format_ethiopian(o.date_planned) if o.date_planned else 'not planned'))
        onhand = self._onhand(list(need))
        rows = []
        for p, q in sorted(need.items(), key=lambda x: x[0].display_name):
            have = onhand.get(p.id, 0)
            seller = p.seller_ids[:1]
            rows.append({'product_id': p.id, 'product': p.display_name, 'uom': p.uom_id.name, 'need': round(q, 3), 'onhand': round(have, 3),
                         'short': round(max(q - have, 0), 3), 'orders': users[p], 'vendor': seller.partner_id.name or '',
                         'lead': seller.delay or 0})
        return {'rows': rows, 'can_buy': 'purchase.order' in self.env}

    @api.model
    def request_purchase(self, product_ids):
        """Draft purchase orders (one per usual vendor) for what is short."""
        self._require(manager=True)
        if 'purchase.order' not in self.env:
            raise UserError(self.env._('Purchasing is not installed.'))
        data = {r['product_id']: r for r in self.shortages()['rows']}
        by_vendor = defaultdict(list)
        missing = []
        for pid in product_ids:
            r = data.get(int(pid))
            if not r or r['short'] <= 0:
                continue
            p = self.env['product.product'].browse(int(pid))
            vendor = p.seller_ids[:1].partner_id
            if not vendor:
                missing.append(p.display_name)
                continue
            by_vendor[vendor].append((p, r['short']))
        if missing:
            raise UserError(self.env._('No usual vendor on: %s. Set it on the item (Purchase tab).', ', '.join(missing)))
        names = []
        for vendor, items in by_vendor.items():
            po = self.env['purchase.order'].create({'partner_id': vendor.id, 'origin': self.env._('Production shortages'),
                                                    'order_line': [(0, 0, {'product_id': p.id, 'product_qty': q}) for p, q in items]})
            names.append(po.name)
        return names

    # ------------------------------------------------------------------ analytics
    @api.model
    def analytics(self, date_from, date_to, line_id=False):
        self._require()
        df, dt = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        dom = [('company_id', '=', self.env.company.id), ('date', '>=', df), ('date', '<=', dt)]
        if line_id:
            dom.append(('line_id', '=', int(line_id)))
        E = self.env['sb.mfg.entry'].sudo().search(dom)
        lines = []
        for line in E.line_id | self.env['sb.mfg.line'].sudo().browse(int(line_id) if line_id else []):
            es = E.filtered(lambda e: e.line_id == line)
            losses = es.loss_ids
            run = sum(es.mapped('minutes_run'))
            down = sum(losses.filtered(lambda l: l.kind == 'downtime' and not l.planned).mapped('minutes'))
            good = sum(es.mapped('good_qty'))
            waste = sum(losses.filtered(lambda l: l.kind == 'waste' and not l.in_kg).mapped('qty'))
            avail = run / (run + down) if run + down else 0
            perf = min((good + waste) / (run / 60.0 * line.rated_speed), 1.5) if run and line.rated_speed else 0
            qual = good / (good + waste) if good + waste else 0
            lines.append({'id': line.id, 'name': line.name, 'run_h': round(run / 60.0, 1), 'down_h': round(down / 60.0, 1), 'good': good,
                          'waste': waste, 'waste_kg': sum(losses.filtered(lambda l: l.kind == 'waste' and l.in_kg).mapped('qty')),
                          'availability': round(100 * avail, 1), 'performance': round(100 * perf, 1), 'quality': round(100 * qual, 1),
                          'oee': round(100 * min(avail * perf * qual, 1.0), 1)})
        downtime = Counter()
        waste = Counter()
        for l in E.loss_ids:
            if l.kind == 'downtime':
                downtime[l.reason_id.name] += l.minutes
            else:
                waste[l.reason_id.name + (' (kg)' if l.in_kg else '')] += l.qty
        per_day = defaultdict(float)
        per_product = defaultdict(float)
        for e in E:
            per_day[e.date] += e.good_qty
            per_product[e.product_id.display_name] += e.good_qty
        days = []
        d = df
        while d <= dt:
            days.append({'date': _s(d), 'label': format_ethiopian(d).rsplit(',', 1)[0], 'good': per_day.get(d, 0)})
            d += timedelta(days=1)
        vdom = [('company_id', '=', self.env.company.id), ('voucher_type', 'in', ('build', 'fprr')), ('state', '=', 'posted'),
                ('date', '>=', df), ('date', '<=', dt)]
        costs = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
        for v in self.env['sb.stock.voucher'].sudo().search(vdom):
            if v.voucher_type == 'fprr':
                # from work in progress: materials = the GIVs it took, labour and overhead as entered
                lines = v.line_ids.filtered('product_id')
                total = sum(lines.mapped('value'))
                labour, overhead = v.sb_labour_total or 0.0, v.sb_overhead_total or 0.0
                for l in lines:
                    share = (l.value / total) if total else 0.0
                    c = costs[l.product_id.display_name]
                    c[0] += l.quantity
                    c[1] += (total - labour - overhead) * share
                    c[2] += labour * share
                    c[3] += overhead * share
                    c[4] += l.value
                continue
            c = costs[v.build_product_id.display_name]
            c[0] += v.build_qty
            c[1] += v.material_value
            c[2] += v.labour_value
            c[3] += v.overhead_value
            c[4] += v.build_value
        orders = self._O().search([('company_id', '=', self.env.company.id), ('state', '=', 'done'),
                                   ('finished_at', '>=', datetime.combine(df, time(0))), ('finished_at', '<=', datetime.combine(dt, time(23, 59)))])
        yields = []
        for o in orders:
            s = self._summary(o)
            if s['yield'] is not None:
                yields.append({'order': o.name, 'product': o.product_id.display_name, 'kg_in': s['kg_in'], 'kg_out': s['kg_out'], 'yield': s['yield']})
        return {'lines': lines, 'downtime': [{'reason': k, 'minutes': v} for k, v in downtime.most_common()],
                'waste': [{'reason': k, 'qty': round(v, 2)} for k, v in waste.most_common()], 'days': days,
                'products': [{'name': k, 'good': v} for k, v in sorted(per_product.items(), key=lambda x: -x[1])],
                'costs': [{'product': k, 'qty': c[0], 'material': round(c[1], 2), 'labour': round(c[2], 2), 'overhead': round(c[3], 2),
                           'total': round(c[4], 2), 'unit': round(c[4] / c[0], 4) if c[0] else 0} for k, c in costs.items()],
                'yields': yields, 'range': '%s – %s' % (format_ethiopian(df), format_ethiopian(dt))}

    # ------------------------------------------------------------------ setup
    @api.model
    def setup(self):
        self._require(manager=True)
        c = self.env.company
        out = self.ctx()
        out['checks'] = [{'id': t.id, 'name': t.name, 'kind': t.kind, 'min': t.min_value, 'max': t.max_value, 'unit': t.unit or '',
                          'blocking': t.blocking, 'products': t.product_ids.ids}
                         for t in self.env['sb.mfg.check.template'].sudo().search([('company_id', '=', c.id)])]
        return out

    @api.model
    def save_setup(self, kind, vals):
        self._require(manager=True)
        model = {'line': 'sb.mfg.line', 'shift': 'sb.mfg.shift', 'reason': 'sb.mfg.reason', 'check': 'sb.mfg.check.template'}[kind]
        M = self.env[model].sudo()
        fmap = {'line': {'name': str, 'code': str, 'rated_speed': float, 'speed_unit': str, 'labour_rate': float, 'overhead_rate': float, 'crew': int},
                'shift': {'name': str, 'hour_from': float, 'hour_to': float},
                'reason': {'name': str, 'kind': str, 'planned': bool},
                'check': {'name': str, 'kind': str, 'min_value': float, 'max_value': float, 'unit': str, 'blocking': bool}}[kind]
        w = {}
        for f, t in fmap.items():
            if f in vals:
                v = vals[f]
                w[f] = t(v) if v not in (None, '') else (False if t is str else 0)
        if kind == 'check' and 'products' in vals:
            w['product_ids'] = [(6, 0, [int(x) for x in vals['products']])]
        if not w.get('name') and not vals.get('id'):
            raise ValidationError(self.env._('Give it a name.'))
        if vals.get('id'):
            M.browse(int(vals['id'])).write(w)
        else:
            M.create(dict(w, company_id=self.env.company.id))
        return self.setup()

    @api.model
    def archive_setup(self, kind, rec_id):
        self._require(manager=True)
        model = {'line': 'sb.mfg.line', 'shift': 'sb.mfg.shift', 'reason': 'sb.mfg.reason', 'check': 'sb.mfg.check.template'}[kind]
        self.env[model].sudo().browse(int(rec_id)).active = False
        return self.setup()

    @api.model
    def set_weight(self, product_id, weight):
        self._require(manager=True)
        self.env['product.product'].sudo().browse(int(product_id)).product_tmpl_id.sb_unit_weight = float(weight or 0)
        return True
