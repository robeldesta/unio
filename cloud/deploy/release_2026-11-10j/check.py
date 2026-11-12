v = env['sb.voucher'].search([('voucher_type', '=', 'sale'), ('state', '=', 'posted')], order='id desc', limit=1)
pdf, _ = env['ir.actions.report']._render_qweb_pdf('sonador_books.action_report_sale_document', v.ids)
print('> %s prints (%s KB)' % (v.name, len(pdf) // 1024))
