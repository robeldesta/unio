"""House and position allowances move from recurring pay items onto the job (People), where cash indemnity now lives too.

The job's "taxable allowances" used to be the total of all taxed allowances; it becomes "other taxable allowances", so
it is reduced by what moves out."""
from odoo import SUPERUSER_ID, api, fields

FIELD = {'house': 'sb_house_allowance', 'position': 'sb_position_allowance'}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'sb_no_payroll_sync': True, 'active_test': False})
    today = fields.Date.context_today(env.user)
    items = env['sb.payroll.benefit'].search([('kind', 'in', list(FIELD)), '|', ('date_to', '=', False), ('date_to', '>=', today)])
    moved = env['hr.employee']
    for b in items:
        e = b.employee_id
        versions = e.version_ids.filtered(lambda v: not b.date_from or v.date_version >= b.date_from or v == e.current_version_id)
        for v in versions:
            v.write({FIELD[b.kind]: (v[FIELD[b.kind]] or 0.0) + b.amount,
                     'sb_taxable_allowance': max((v.sb_taxable_allowance or 0.0) - b.amount, 0.0)})
        e.message_post(body='%s %s moved from recurring pay items to the job.' % (
            dict(b._fields['kind']._description_selection(env)).get(b.kind), '{:,.2f}'.format(b.amount)))
        moved |= e
    items.unlink()
    moved.with_context(sb_no_payroll_sync=False)._sb_sync_payroll()
