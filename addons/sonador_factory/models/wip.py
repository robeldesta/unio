"""Production costs through work in progress (owner's rules, 12 Nov 2026).

The store issues materials for a production order on a GIV:   Dr Work in Progress / Cr Inventory, at average cost.
The floor's "Finish and post" receives the finished goods on an FPRR:
    Dr Finished Goods / Cr WIP (that order's GIVs) · Cr labour absorbed · Cr overhead absorbed.
Materials leave stock once, on the GIV. Each order's FPRR takes its own GIVs only, so orders running at the same time
never mix their costs. A company with Production gets this set up (WIP account, cost from WIP, raw materials issued to
WIP) at install and at every update, without changing what a company already chose."""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError

OPEN_ORDER = [('state', 'not in', ('done', 'cancel'))]


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _sb_setup_production(self):
        Account = self.env['account.account'].sudo()
        for company in self.sudo().search([]):
            A = Account.with_company(company)
            wip = company.account_production_wip_account_id or A._sb_find('241100', company)[:1]
            vals = {}
            if wip and not company.account_production_wip_account_id:
                vals['account_production_wip_account_id'] = wip.id
            if company.sb_fprr_cost_source != 'wip':
                vals['sb_fprr_cost_source'] = 'wip'
            if vals:
                company.write(vals)
            rm = self.env['product.category'].sudo().with_company(company).search([('name', 'ilike', 'Raw Materials')], limit=1)
            if wip and rm and not rm.sb_issue_account_id:
                rm.sb_issue_account_id = wip
        # waste reasons: the first defaults were a converting plant's (wind, seal, broke). Generic names, only where the
        # company still has the old default name (a reason it renamed itself is kept)
        for xmlid, (old, new) in {'waste_0': ('Rejects (bad wind, cut or seal)', 'Rejects'),
                                  'waste_1': ('Trim and edge (kg)', 'Offcuts and trim (kg)'),
                                  'waste_3': ('Damaged in packing', 'Damaged in handling'),
                                  'waste_4': ('Broke / reusable (kg)', 'Reusable scrap (kg)')}.items():
            r = self.env.ref('sonador_factory.' + xmlid, raise_if_not_found=False)
            if r and r.name == old:
                r.sudo().name = new
        seq = self.env.ref('sonador_factory.seq_order', raise_if_not_found=False)
        if seq and (seq.prefix or '').startswith('PO/'):
            seq.sudo().prefix = 'MO/' + seq.prefix[3:]   # purchase orders are PO/; orders already numbered keep theirs
        return True


class SbMfgOrder(models.Model):
    _inherit = 'sb.mfg.order'

    giv_ids = fields.One2many('sb.stock.voucher', 'sb_mfg_order_id', string='Materials issued',
                              domain=[('voucher_type', '=', 'giv')])

    @api.depends('name', 'qty_planned', 'product_id')
    def _compute_display_name(self):
        """'MO/2026/0004 · 5 × Steel door 90x210': a store clerk picking an order sees what it makes."""
        for o in self:
            qty = ('%g' % o.qty_planned) if o.qty_planned else ''
            o.display_name = '%s · %s × %s' % (o.name, qty, o.product_id.name) if o.product_id and qty else (o.name or '')

    def _sb_needs(self):
        """{product: quantity} the recipe needs for the planned quantity."""
        self.ensure_one()
        if self.material_ids:
            return {m.product_id: m.qty_required for m in self.material_ids if m.qty_required}
        bom = self.bom_id
        if not bom or not bom.product_qty:
            return {}
        factor = self.qty_planned / bom.product_qty
        out = {}
        for bl in bom.bom_line_ids:
            out[bl.product_id] = out.get(bl.product_id, 0.0) + bl.product_qty * factor
        return out

    def _sb_issued(self):
        """{product: quantity} already issued on posted GIVs for this order."""
        out = {}
        for g in self.giv_ids.filtered(lambda g: g.state == 'posted'):
            for l in g.line_ids.filtered('product_id'):
                out[l.product_id] = out.get(l.product_id, 0.0) + l.quantity
        return out

    def _sb_waiting_givs(self):
        return self.giv_ids.filtered(lambda g: g.state == 'posted' and not g.sb_fprr_id)


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_mfg_order_id = fields.Many2one(
        'sb.mfg.order', 'Production order', index='btree_not_null', copy=False, domain=OPEN_ORDER,
        help='GIV: the materials are issued for this order and go to its work in progress. '
             'FPRR: the finished goods of this order, costed from its GIVs.')

    def _sb_wip_account(self):
        return (self.company_id or self.env.company).account_production_wip_account_id

    @api.onchange('sb_mfg_order_id')
    def _onchange_sb_mfg_order(self):
        """Issue for an order: what its recipe still needs, charged to work in progress."""
        order = self.sb_mfg_order_id
        if not order or self.voucher_type != 'giv' or self.state not in ('draft', False):
            return
        wip = self._sb_wip_account()
        if wip:
            self.account_id = wip
        if not self.department:
            self.department = order.line_id.name or self.env._('Production')
        if not self.reference:
            self.reference = order.name
        issued = order._sb_issued()
        cmds = [Command.clear()]
        for product, need in order._sb_needs().items():
            left = need - issued.get(product, 0.0)
            if left > 0:
                cmds.append(Command.create({'product_id': product.id, 'name': product.display_name, 'quantity': left,
                                            'unit_cost': product.standard_price, 'account_id': wip.id if wip else False,
                                            'value': round(left * product.standard_price, 2)}))   # estimate until posted
        self.line_ids = cmds
        if len(cmds) == 1:
            return {'warning': {'type': 'notification', 'title': self.env._('Nothing left to issue'),
                                'message': self.env._('Everything %s needs has been issued. Add a line for anything extra.', order.name)}}

    def _sb_force_wip(self):
        """A GIV for a production order charges work in progress, whatever an item's class says."""
        for v in self.filtered(lambda v: v.voucher_type == 'giv' and v.sb_mfg_order_id and v.state == 'draft'):
            wip = v._sb_wip_account()
            if not wip:
                raise UserError(self.env._('Set the work in progress account of the company (Settings) before issuing for '
                                           'a production order.'))
            if v.account_id != wip:
                v.account_id = wip
            v.line_ids.filtered(lambda l: l.product_id and l.account_id != wip).write({'account_id': wip.id})

    def action_post(self):
        self._sb_force_wip()
        return super().action_post()

    def _sb_waiting_givs(self):
        """An order's FPRR takes that order's GIVs only; an FPRR with no order never takes an order's GIVs."""
        givs = super()._sb_waiting_givs()
        if self.voucher_type == 'fprr' and self.sb_mfg_order_id:
            return givs.filtered(lambda g: g.sb_mfg_order_id == self.sb_mfg_order_id)
        return givs.filtered(lambda g: not g.sb_mfg_order_id)


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    @api.onchange('product_id')
    def _onchange_product(self):
        res = super()._onchange_product()
        v = self.voucher_id
        if self.product_id and self.voucher_type == 'giv' and v.sb_mfg_order_id and v._sb_wip_account():
            self.account_id = v._sb_wip_account()
        return res
