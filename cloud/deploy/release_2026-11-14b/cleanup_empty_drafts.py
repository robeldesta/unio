# Release 12 Nov 2026 (a): remove the empty drafts Odoo made by saving untouched new forms when the browser tab was
# hidden ("/" vouchers under "Documents not posted"). Removed only when ALL of these hold:
#   draft · never numbered ("/" or "New") · never posted (no entry, no correction) · no lines · no deposit lines ·
#   no supplier / customer / department / reference typed · older than one day.
# Anything with a single thing typed in stays. Prints what it removed. Odoo shell, as the system.
# DRY_RUN=1 lists without removing.
import os
from datetime import timedelta

from odoo import fields

dry = os.environ.get('DRY_RUN') == '1'
cutoff = fields.Datetime.now() - timedelta(days=1)
total = 0


def report(model, recs):
    global total
    total += len(recs)
    if recs:
        print('> %s: %d empty draft(s)%s: ids %s' % (model, len(recs), ' (dry run, kept)' if dry else ' removed',
                                                    ', '.join(str(i) for i in recs.ids[:30])))


if 'sb.stock.voucher' in env:
    SV = env['sb.stock.voucher'].sudo().with_context(active_test=False)
    cand = SV.search([('state', '=', 'draft'), ('name', 'in', ('/', False)), ('create_date', '<', cutoff)])
    empty = cand.filtered(lambda v: not v.line_ids and not v.move_id and not v.partner_id and not (v.department or '').strip()
                          and not (v.reference or '').strip() and not (v.memo or '').strip()
                          and not getattr(v, 'sb_correction_before', False) and not getattr(v, 'sb_mfg_order_id', False))
    report('stock vouchers (GRN / GIV / transfers / counts)', empty)
    if not dry:
        empty.unlink()

if 'sb.voucher' in env:
    V = env['sb.voucher'].sudo().with_context(active_test=False)
    cand = V.search([('state', '=', 'draft'), ('name', 'in', ('/', 'New', False)), ('create_date', '<', cutoff)])
    empty = cand.filtered(lambda v: not v.line_ids and not v.cash_line_ids and not v.move_id and not v.partner_id
                          and not (v.reference or '').strip() and not (v.memo or '').strip())
    report('vouchers (invoices / receipts / payments / journals)', empty)
    if not dry:
        empty.unlink()

if not dry:
    env.cr.commit()
print('> empty drafts: %d %s' % (total, 'found (dry run)' if dry else 'removed'))
