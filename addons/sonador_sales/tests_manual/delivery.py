# Delivery orders and batches as in Eco Green's Odoo 18 (names, states, times, invoice per batch, print count). Rolled back.
#   cd ~/ecogreen-odoo19 && sync/local_shell.sh < ~/sonador-odoo/addons/sonador_sales/tests_manual/delivery.py
import random
from datetime import datetime

from odoo import Command
from odoo.exceptions import UserError, ValidationError

ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def raises(fn, exc=(UserError, ValidationError), text=None):
    try:
        with env.cr.savepoint():
            fn()
    except exc as e:
        return text is None or text in str(e)
    return False


c = env.company
P = env['product.product']
aqua = P.search([('default_code', '=', '1504-3001')], limit=1)
one = P.search([('default_code', '=', '1504-1001')], limit=1)
wh = env['stock.warehouse'].search([('company_id', '=', c.id)], limit=1)
cust = env['res.partner'].create({'name': 'Test Delivery Farm', 'customer_rank': 1})
fs = random.randint(10 ** 7, 9 * 10 ** 7)


def invoice(so, qty=None):
    v = env['sb.voucher'].browse(so.action_sb_invoice()['res_id'])
    if qty:
        v.line_ids.quantity = qty
    global fs
    fs += 1
    if 'sb_pad_number' in v._fields:
        v.sb_pad_number = 'FS%08d' % fs
    v.action_post()
    env.invalidate_all()
    return v


def order(qty):
    so = env['sale.order'].create({'partner_id': cust.id, 'warehouse_id': wh.id, 'order_line': [
        Command.create({'product_id': aqua.id, 'product_uom_qty': qty, 'price_unit': 50, 'tax_ids': [Command.clear()]})]})
    so.with_context(sb_so_approved=True).action_confirm()  # the approval steps are sonador_sale_approval's test
    return so


B = env['sb.delivery.batch']
labels = lambda m, f: [l for k, l in env[m]._fields[f]._description_selection(env)]  # noqa: E731
check(labels('sb.delivery.order', 'state') == ['Draft', 'Ready', 'In Transit', 'Delivered', 'Done', 'Cancelled'],
      'delivery order states as Odoo 18: Draft, Ready, In Transit, Delivered, Done, Cancelled')
check(labels('sb.delivery.batch', 'state') == ['Draft', 'Done', 'Cancelled'], 'batch states as Odoo 18: Draft, Done, Cancelled')
check(labels('sb.delivery.batch', 'delivery_method') == ['Company Delivery', 'Own Customer Pickup', 'Rented Vehicle Delivery'],
      'delivery methods as Odoo 18')
check([B._fields[f].string for f in ('invoiced_time', 'loaded_time', 'gate_time', 'unloading_time', 'return_time', 'print_count')] ==
      ['Invoiced Time', 'Loaded Time', 'Gate Exit Time', 'Unloading Time', 'Return Time', 'Print Count'], 'time and print labels as Odoo 18')

# ------------------------------------------------------------------ not strict (the product default)
c.sb_delivery_strict = False
so = order(100)
check(not so.sb_delivery_ids, 'not strict: no delivery order on confirmation')
inv = invoice(so)
do = inv.sb_delivery_id
check(do.state == 'draft' and do.invoiced_time and not do.batch_ids, 'invoice posted: %s Draft, invoiced time set, no batch yet' % do.name)
b1 = B.browse(do.action_new_batch()['res_id'])
b1.line_ids.quantity = 60
check(b1.name == 'Batch 1' and b1.display_name == '%s — Batch 1' % do.name, 'Add Batch: "Batch 1", shown as "%s"' % b1.display_name)
b1.write({'vehicle': 'AA-03-A40409', 'loaded_time': datetime(2026, 10, 30, 8), 'gate_time': datetime(2026, 10, 30, 8)})
check(raises(b1.action_validate, text='Gate Exit Time must be after Loaded Time'), 'gate exit at the same minute as loading is refused')
b1.gate_time = datetime(2026, 10, 30, 9)
b1.action_validate()
mv = b1.move_ids[:1]
check(b1.state == 'done' and do.state == 'ready' and mv.origin == '%s / Batch 1' % do.name,
      'Batch 1 validated: Done; delivery order Ready; stock move "%s"' % mv.origin)
b2 = B.browse(do.action_new_batch()['res_id'])
check(b2.name == 'Batch 2' and b2.batch_sequence_display == 2, 'second batch "Batch 2" (Batch No 2)')
b2.vehicle = 'X'
b2.action_validate()
check(do.state == 'done' and not do.sb_to_deliver, 'not strict: everything delivered, delivery order Done by itself')
act = b1.action_print()
env['ir.actions.report']._render_qweb_pdf(act['report_name'], res_ids=b1.ids)  # what the browser asks for after Print
check(b1.print_count >= 1, 'print counted (Print Count %s) with %s' % (b1.print_count, act['report_name']))

# ------------------------------------------------------------------ strict (Eco Green)
c.sb_delivery_strict = True
so = order(1000)
do = so.sb_delivery_ids
check(len(do) == 1 and do.state == 'draft' and not do.batch_ids, 'strict: delivery order %s opened on approval, Draft, no batch' % do.name)
inv1 = invoice(so, 600)
check(inv1.sb_delivery_id == do and len(do.batch_ids) == 1, 'first invoice goes to the same delivery order')
s1 = do.batch_ids
check(s1.name == 'Batch 1' and s1.invoice_id == inv1 and s1.invoiced_time and s1.total_qty == 600,
      'invoice %s opened its own Batch 1 (600, invoice and invoiced time on the batch)' % inv1.name)
inv2 = invoice(so, 400)
s2 = do.batch_ids - s1
check(s2.name == 'Batch 2' and s2.invoice_id == inv2 and s2.total_qty == 400, 'second invoice: Batch 2 (400) with its invoice')
check(raises(s1.action_validate, text='Delivery Vehicle is required on batch'), 'strict: company delivery needs the Delivery Vehicle')
s1.vehicle = 'AA-03-A40409'
check(raises(s1.action_validate, text='Loaded Time is required on batch'), 'strict: Loaded Time required')
t0 = s1.invoiced_time.replace(microsecond=0)
from datetime import timedelta  # noqa: E402
s1.write({'loaded_time': t0 + timedelta(hours=1), 'gate_time': t0 + timedelta(hours=2)})
check(raises(s1.action_validate, text='Unloading Time is required on batch'), 'strict: Unloading Time required (company delivery)')
s1.unloading_time = t0 + timedelta(hours=1, minutes=30)
check(raises(s1.action_validate, text='Unloading Time must be after Gate Exit Time'), 'strict: Unloading before Gate Exit refused')
s1.unloading_time = t0 + timedelta(hours=5)
check(s1.can_print_batch if env.user.has_group('base.group_system') else not s1.can_print_batch,
      'draft batch: only an administrator may print it')
s1.action_validate()
check(s1.state == 'done' and do.state == 'ready', 'Validate Batch: Batch 1 Done, delivery order Ready')
s2.write({'delivery_method': 'pickup', 'loaded_time': t0 + timedelta(hours=1), 'gate_time': t0 + timedelta(hours=2)})
s2.action_validate()
check(s2.state == 'done' and do.state == 'ready' and not do.sb_to_deliver,
      'own customer pickup: Loaded + Gate Exit are enough; all delivered, still Ready (closed by hand)')
check(raises(do.action_cancel, text='validated batch'), 'a delivery order with a validated batch cannot be cancelled')
do.action_mark_done()
check(do.state == 'done' and do.validation_status == 'validated', 'Mark as Done: Done, validation status Validated')
do.action_draft()
check(do.state == 'ready', 'Reset to Draft: back to what its batches say (Ready)')
so3 = order(10)
check(raises(so3.sb_delivery_ids.action_mark_done, text='Validate at least one delivery batch'), 'Mark as Done needs a validated batch')
inv3 = invoice(so3)
inv3.with_context(sb_void_reason='test').action_void()
check(not so3.sb_delivery_ids.batch_ids, 'invoice voided: its draft batch goes too')
x = so3.sb_delivery_ids
x.action_cancel()
check(x.state == 'cancel', 'delivery order without posted invoice or validated batch: Cancelled')
check(B.name_search('%s — Batch' % do.name) and B.search([('reference', '=', '%s — Batch 2' % do.name)]) == s2,
      'batches found by "DO — Batch n"')
print('RESULT %s (%d/%d)' % ('ALL PASS' if all(ok) else 'FAILURES', sum(ok), len(ok)))
env.cr.rollback()
