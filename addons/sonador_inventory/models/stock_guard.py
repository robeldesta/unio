"""Posted inventory documents stay as posted.

A posted or void GRN, GIV, transfer, count, opening stock, FPRR or build changes only through its own flows: Post,
Correct (reset to draft) and Void, and the engine that re-costs later issues. Writing its status, date, lines, quantities,
costs or accounts directly (over RPC, an import, a script) is refused, as for vouchers (sonador_books move_guard); the
flags the flows keep (its entry, the figures as posted while in correction, brought over, taken by an FPRR) are set by
the flows alone. Notes and the chatter stay open.

The stock moves behind them are guarded the same way: a done stock move or move line (quantity, cost, value, date,
places, item, the document it belongs to) and the on-hand quantities (stock.quant) change only through the stock flows:
posting, Correct and Void, the costing engine's re-cost, and Odoo's own validation of transfers, scraps and inventory
counts. Changing one directly would change the stock and its value with no journal entry, in any period, closed or not.
"""
from odoo import api, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.move_guard import _defaults, _will_change, controlled, in_flow, is_accountant

# set by the flows only
STOCK_FLAGS = ('state', 'name', 'move_id', 'void_move_id', 'sb_correction_before', 'sb_migrated', 'sb_fprr_id', 'sb_ob_period')
# what can still be changed directly on a posted or void document: nothing the books or the stock records show
STOCK_FREE = ('memo',)
# a done stock move / move line: what makes its quantity, value, date, place or document (sb_* fields too)
DONE_MOVE_FIELDS = ('product_id', 'product_uom_qty', 'product_qty', 'quantity', 'product_uom', 'location_id', 'location_dest_id',
                    'location_final_id', 'date', 'state', 'company_id', 'price_unit', 'value', 'value_manual', 'is_in', 'is_out',
                    'is_dropship', 'move_line_ids', 'picked', 'lot_ids', 'restrict_partner_id', 'picking_id', 'scrapped',
                    'production_id', 'raw_material_production_id', 'origin_returned_move_id')
DONE_LINE_FIELDS = ('product_id', 'quantity', 'quantity_product_uom', 'product_uom_id', 'location_id', 'location_dest_id', 'lot_id',
                    'lot_name', 'package_id', 'result_package_id', 'owner_id', 'date', 'move_id', 'picking_id', 'company_id',
                    'state', 'picked', 'quant_id')
QUANT_FIELDS = ('quantity', 'product_id', 'location_id', 'lot_id', 'package_id', 'owner_id', 'company_id', 'in_date')


def _check_correction_rights(env, vouchers):
    """A document reset to draft by Correct is out of the books until it is posted again: only an Accountant (who alone may
    correct) changes it, posts it again or voids it. A store user cannot finish another's correction."""
    correcting = vouchers.filtered(lambda v: v.state == 'draft' and v.sb_correction_before)
    if correcting and not env.su and not is_accountant(env.user):
        raise UserError(env._('%s is being corrected (reset to draft by an accountant): only users with the Accountant role '
                              '(or higher) can change it, post it again or void it.', ', '.join(correcting.mapped('name'))))


def _stock_refused(env, what):
    raise UserError(env._('%s: stock that was moved changes only through its document (Correct or Void it, or enter a '
                          'stock count or adjustment).', what))


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    @api.model
    def _sb_free_fields(self):
        return set(STOCK_FREE) | {f for f in self._fields if f.startswith(('message_', 'activity_'))}

    def _sb_check_locked(self, fields_changed):
        locked = self.filtered(lambda v: v.state in ('posted', 'void'))
        blocked = set(fields_changed) - self._sb_free_fields()
        if not locked.filtered(lambda v: v.state == 'void'):
            blocked.discard('sb_void_reason')  # written on a posted document, before voiding it; kept as it was once void
        if locked and blocked:
            labels = sorted({self._fields[f].string for f in blocked if f in self._fields})
            raise UserError(self.env._('%(v)s is %(state)s and cannot be changed directly (%(fields)s). Use Correct or Void.',
                                       v=', '.join(locked.mapped('name')),
                                       state=self.env._('void') if all(v.state == 'void' for v in locked) else self.env._('posted'),
                                       fields=', '.join(labels)))

    @api.model_create_multi
    def create(self, vals_list):
        if not in_flow(self.env):
            flags = [f for f in STOCK_FLAGS if f in self._fields]
            for vals in vals_list:
                set_ = {f: v for f, v in _defaults(self.env, vals, flags).items() if v and v not in ('draft', '/')}
                if set_:
                    raise UserError(self.env._('A new inventory voucher starts as a draft: %s is set by posting, correcting or '
                                               'voiding it.', ', '.join(sorted(self._fields[f].string for f in set_))))
        return super().create(vals_list)

    def write(self, vals):
        if not in_flow(self.env):
            changing = [f for f in vals if any(_will_change(v, vals, f) for v in self)] if self else list(vals)
            flags = [f for f in changing if f in STOCK_FLAGS]
            if flags:
                raise UserError(self.env._('%s is changed only by posting, correcting or voiding the voucher.',
                                           ', '.join(sorted(self._fields[f].string for f in flags))))
            self._sb_check_locked(changing)
            if set(changing) - self._sb_free_fields():
                _check_correction_rights(self.env, self)
        return super().write(vals)

    def action_post(self):
        _check_correction_rights(self.env, self)
        return super(SbStockVoucher, controlled(self)).action_post()

    def action_correct(self):
        return super(SbStockVoucher, controlled(self)).action_correct()

    def action_void(self):
        return super(SbStockVoucher, controlled(self)).action_void()


class SbStockVoucherLine(models.Model):
    """The lines of a posted or void inventory voucher change with the voucher only."""
    _inherit = 'sb.stock.voucher.line'

    def _sb_check_voucher_open(self, vouchers):
        if in_flow(self.env):
            return
        locked = vouchers.filtered(lambda v: v.state in ('posted', 'void'))
        if locked:
            raise UserError(self.env._('%s is posted: its lines change only through Correct (or Void).',
                                       ', '.join(locked.mapped('name'))))
        _check_correction_rights(self.env, vouchers)

    @api.model_create_multi
    def create(self, vals_list):
        self._sb_check_voucher_open(self.env['sb.stock.voucher'].browse({v['voucher_id'] for v in vals_list if v.get('voucher_id')}))
        return super().create(vals_list)

    def write(self, vals):
        if vals:
            self._sb_check_voucher_open(self.voucher_id | self.env['sb.stock.voucher'].browse(vals.get('voucher_id') or []))
        return super().write(vals)

    def unlink(self):
        self._sb_check_voucher_open(self.voucher_id)
        return super().unlink()


class StockMove(models.Model):
    """Done stock moves change through the stock flows only (see the module docstring)."""
    _inherit = 'stock.move'

    @api.model
    def _sb_done_fields(self):
        return set(DONE_MOVE_FIELDS) | {f for f in self._fields if f.startswith('sb_')}

    @api.model_create_multi
    def create(self, vals_list):
        if not in_flow(self.env):
            for vals in vals_list:
                done = _defaults(self.env, vals, ['state']).get('state') == 'done' or \
                    self.env['stock.picking'].browse(vals.get('picking_id') or []).state == 'done'
                if done:
                    _stock_refused(self.env, self.env._('A stock move cannot be created as done'))
        return super().create(vals_list)

    def write(self, vals):
        if not in_flow(self.env):
            if vals.get('state') == 'done':
                _stock_refused(self.env, self.env._('A stock move is done by validating its document'))
            done = self.filtered(lambda m: m.state == 'done')
            blocked = self._sb_done_fields() & set(vals)
            if done and blocked and any(_will_change(m, vals, f) for m in done for f in blocked):
                _stock_refused(self.env, self.env._('%(moves)s is done (%(fields)s)',
                                                    moves=', '.join(sorted({m.reference or m.origin or str(m.id) for m in done}))[:200],
                                                    fields=', '.join(sorted({self._fields[f].string for f in blocked}))))
        return super().write(vals)

    def unlink(self):
        if not in_flow(self.env) and self.filtered(lambda m: m.state == 'done'):
            _stock_refused(self.env, self.env._('A done stock move cannot be deleted'))
        return super().unlink()

    def _action_done(self, cancel_backorder=False):
        # validating moves (a transfer, a scrap, an inventory count, a document posting) is the stock flow itself
        return super(StockMove, controlled(self))._action_done(cancel_backorder=cancel_backorder)


class SbStock(models.AbstractModel):
    """The stock engine (moving goods, valuing an issue, undoing a document's moves) is one of the stock flows: the moves
    it is handed are worked on under the flow too."""
    _inherit = 'sb.stock'

    @api.model
    def _move(self, product, qty, src, dst, day, origin, company, unit_cost=None, link=None, reverses=None):
        flow = controlled(self)
        return super(SbStock, flow)._move(product, qty, src, dst, day, origin, company, unit_cost=unit_cost, link=link,
                                          reverses=reverses.with_env(flow.env) if reverses else reverses)

    @api.model
    def _sb_cost_out(self, move, day):
        flow = controlled(self)
        return super(SbStock, flow)._sb_cost_out(move.with_env(flow.env), day)

    @api.model
    def _undo_moves(self, moves, day, origin, link, company, recost=True):
        flow = controlled(self)
        return super(SbStock, flow)._undo_moves(moves.with_env(flow.env), day, origin, link, company, recost=recost)


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _sb_done(self):
        return self.filtered(lambda l: l.state == 'done' or l.move_id.state == 'done' or l.picking_id.state == 'done')

    @api.model_create_multi
    def create(self, vals_list):
        if not in_flow(self.env):
            moves = self.env['stock.move'].browse({v['move_id'] for v in vals_list if v.get('move_id')})
            pickings = self.env['stock.picking'].browse({v['picking_id'] for v in vals_list if v.get('picking_id')})
            if moves.filtered(lambda m: m.state == 'done') or pickings.filtered(lambda p: p.state == 'done'):
                _stock_refused(self.env, self.env._('A line cannot be added to a done stock move'))
        return super().create(vals_list)

    def write(self, vals):
        if not in_flow(self.env):
            done = self._sb_done()
            blocked = set(DONE_LINE_FIELDS) & set(vals)
            if done and blocked and any(_will_change(l, vals, f) for l in done for f in blocked):
                _stock_refused(self.env, self.env._('%(moves)s is done (%(fields)s)',
                                                    moves=', '.join(sorted({l.move_id.reference or l.move_id.origin or l.reference or str(l.move_id.id) for l in done}))[:200],
                                                    fields=', '.join(sorted({self._fields[f].string for f in blocked}))))
            if vals.get('move_id') and self.env['stock.move'].browse(vals['move_id']).state == 'done':
                _stock_refused(self.env, self.env._('A line cannot be moved to a done stock move'))
        return super().write(vals)

    def unlink(self):
        if not in_flow(self.env) and self._sb_done():
            _stock_refused(self.env, self.env._('A done stock move line cannot be deleted'))
        return super().unlink()


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        return super(StockPicking, controlled(self))._action_done()


class StockQuant(models.Model):
    """On-hand quantities follow the stock moves: a count (Odoo's inventory mode sets the counted quantity, applying it
    makes the moves) or a document, never a direct change."""
    _inherit = 'stock.quant'

    @api.model_create_multi
    def create(self, vals_list):
        if not in_flow(self.env) and any(v.get('quantity') for v in vals_list):
            _stock_refused(self.env, self.env._('On-hand quantities cannot be set directly'))
        return super().create(vals_list)

    def write(self, vals):
        if not in_flow(self.env) and set(QUANT_FIELDS) & set(vals) and \
                any(_will_change(q, vals, f) for q in self for f in set(QUANT_FIELDS) & set(vals)):
            _stock_refused(self.env, self.env._('On-hand quantities cannot be changed directly'))
        return super().write(vals)

    @api.model
    def _update_available_quantity(self, product_id, location_id, quantity=False, reserved_quantity=False, lot_id=None,
                                   package_id=None, owner_id=None, in_date=None):
        # called by the stock flows only (a done move, a count applied): it is what moves the on-hand quantity
        return super(StockQuant, controlled(self))._update_available_quantity(
            product_id, location_id, quantity=quantity, reserved_quantity=reserved_quantity, lot_id=lot_id, package_id=package_id,
            owner_id=owner_id, in_date=in_date)


class ProductValue(models.Model):
    """Odoo's valuation adjustments of a stock move change its value with no journal entry: a done move's value changes
    through its document or the costing engine only."""
    _inherit = 'product.value'

    def _sb_check_moves(self, moves):
        if not in_flow(self.env) and moves.filtered(lambda m: m.state == 'done'):
            _stock_refused(self.env, self.env._('The value of a done stock move cannot be adjusted directly'))

    @api.model_create_multi
    def create(self, vals_list):
        self._sb_check_moves(self.env['stock.move'].browse({v['move_id'] for v in vals_list if v.get('move_id')}))
        return super().create(vals_list)

    def write(self, vals):
        self._sb_check_moves(self.move_id | self.env['stock.move'].browse(vals.get('move_id') or []))
        return super().write(vals)

    def unlink(self):
        self._sb_check_moves(self.move_id)
        return super().unlink()
