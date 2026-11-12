"""Batch names as before: "Batch 1", "Batch 2" per delivery order (was DO/05732/B1 or DO/05732/Batch 1);
the invoiced time of a delivery order from its first invoice made here; states from the batches."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute(r"""UPDATE sb_delivery_batch SET name = 'Batch ' || substring(name from '/B(\d+)$') WHERE name ~ '/B\d+$'""")
    cr.execute(r"""UPDATE sb_delivery_batch SET name = substring(name from '(Batch \d+)$') WHERE name ~ '/Batch \d+$'""")
    cr.execute("""UPDATE sb_delivery_order d SET invoiced_time = v.t FROM (
                      SELECT sb_delivery_id, min(create_date) t FROM sb_voucher
                       WHERE state = 'posted' AND sb_delivery_id IS NOT NULL AND NOT coalesce(sb_migrated, false)
                       GROUP BY sb_delivery_id) v
                   WHERE v.sb_delivery_id = d.id AND d.invoiced_time IS NULL""")
    env = api.Environment(cr, SUPERUSER_ID, {})
    B = env['sb.delivery.batch'].search([])
    env.add_to_compute(B._fields['reference'], B)
    env.flush_all()
    env['sb.delivery.order'].search([('state', '=', 'draft')])._sb_sync_state()
