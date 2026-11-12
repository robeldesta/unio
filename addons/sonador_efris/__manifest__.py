{
    'name': 'EFRIS (Uganda e-invoicing)',
    'version': '19.0.0.1.0',
    'summary': "URA's Electronic Fiscal Receipting and Invoicing Solution: invoices and credit notes are fiscalised with URA "
               "(fiscal document number, verification code, QR code on the printed invoice), with a sending queue and retries",
    'author': 'Sonador Systems',
    'category': 'Accounting/Localizations',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_sales'],
    'data': ['security/ir.model.access.csv', 'data/cron.xml', 'views/views.xml', 'views/invoice.xml'],
    'external_dependencies': {'python': ['cryptography']},
    'installable': True,
}
