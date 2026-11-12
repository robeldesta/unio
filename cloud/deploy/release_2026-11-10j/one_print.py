"""AfyaSoft, 10 Nov 2026 (Robel / Kaleab): one invoice print. The sales invoice prints from the sale (Print > Invoice), with
the bank to pay to; Odoo's own invoice prints on the journal entry (a "Proforma Invoice" without the EFRIS numbers and the
bank) are taken off the Print menu. The Stanbic account of Ghion Investment and General Trading Limited is AfyaSoft's
account to be paid on. Safe to repeat."""
R = env['ir.actions.report'].sudo()
for xid in ('account.account_invoices', 'account.account_invoices_without_payment'):
    r = env.ref(xid, raise_if_not_found=False)
    if r and r.binding_model_id:
        r.unlink_action()
        print('> off the Print menu: %s' % r.name)
sale = env.ref('sonador_books.action_report_sale_document')
sale.name = 'Invoice'
print('> Print > %s on sales' % sale.name)
B = env['res.partner.bank'].sudo()
ghion_bank = B.search([('acc_number', '=', '9030025359134')], limit=1)
afya = env['res.company'].sudo().search([('name', '=', 'AfyaSoft')], limit=1)
assert ghion_bank and afya
if not B.search_count([('partner_id', '=', afya.partner_id.id), ('acc_number', '=', '9030025359134')]):
    B.create({'partner_id': afya.partner_id.id, 'acc_number': '9030025359134', 'bank_id': ghion_bank.bank_id.id,
              'acc_holder_name': 'Ghion Investment and General Trading Limited', 'currency_id': ghion_bank.currency_id.id})
for b in B.search([('partner_id', 'in', env['res.company'].sudo().search([]).partner_id.ids)]):
    if not b.acc_holder_name:
        b.acc_holder_name = 'Ghion Investment and General Trading Limited'
    print('> %s: %s %s, %s' % (b.partner_id.name, b.bank_id.name, b.acc_number, b.acc_holder_name))
env.cr.commit()
