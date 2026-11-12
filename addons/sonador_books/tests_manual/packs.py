# Report pack tests. Rolled back at the end.
from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.addons.sonador_books.models.ethiopian_calendar import to_ethiopian, fiscal_periods, fiscal_year_of
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


Pack = env['sb.report.pack']
packs = Pack.search([])
check(len(packs) >= 3, 'ready-made packs exist: %s' % ', '.join(packs.mapped('name')))
for p in packs:
    pdf, fn, mime = p.render('pdf')
    xls, fn2, mime2 = p.render('xlsx')
    pages = pdf.count(b'/Type /Page') - pdf.count(b'/Type /Pages')
    check(pdf[:4] == b'%PDF' and xls[:2] == b'PK' and pages >= len(p.line_ids) + 1,
          '%s: PDF %d pages (%d reports + contents), Excel %d KB' % (p.name, pages, len(p.line_ids), len(xls) // 1024))
today = fields.Date.from_string('2026-10-17')   # Tikimt 7, 2019
m = packs.filtered(lambda x: x.period == 'last_month')[:1]
a, b = m._range(today)
check(fields.Date.to_string(a) == '2026-09-11' and fields.Date.to_string(b) == '2026-10-10', 'last month = Meskerem 2019 (%s - %s)' % (a, b))
fy = packs.filtered(lambda x: x.period == 'last_fy')[:1]
a, b = fy._range(today)
check(fields.Date.to_string(a) == '2025-07-08' and fields.Date.to_string(b) == '2026-07-07', 'last fiscal year = FY 2018 E.C.')
m.period = 'last_quarter'
a, b = m._range(today)
check(fields.Date.to_string(a) == '2026-07-08' and fields.Date.to_string(b) == '2026-10-10', 'last quarter = Q1 (Hamle - Meskerem)')
m.period = 'last_month'
# email
partner = env['res.partner'].create({'name': 'Board Member', 'email': 'board@example.com'})
m.partner_ids = [Command.set([partner.id])]
m.action_send()
msg = m.message_ids.filtered(lambda x: x.attachment_ids)[:1]
check(msg and partner in msg.partner_ids and msg.attachment_ids, 'emailed with the pack attached (%s)' % ', '.join(msg.attachment_ids.mapped('name')))
# telegram without a token
m.telegram_chat = '-100123'
env.company.sudo().sb_telegram_token = False
try:
    m._send_telegram([('x.pdf', b'%PDF', 'application/pdf')], 'test'); check(False, 'telegram needs a token')
except UserError:
    check(True, 'telegram refuses clearly without a bot token')
m.telegram_chat = False
# schedule
m.write({'schedule': 'monthly', 'send_day': to_ethiopian(fields.Date.context_today(env['sb.report.pack']))[2], 'last_sent': False})
Pack._cron_send()
check(m.last_sent, 'monthly schedule sends on the chosen Ethiopian day')
cat = env['sb.report'].get_catalog()
check(any(r.get('pack_id') == m.id for r in cat['reports']), 'packs listed under Report Groups in the Report Center')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
