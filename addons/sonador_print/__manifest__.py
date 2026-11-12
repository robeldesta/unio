{
    'name': 'Printing and Document Design',
    'version': '19.0.0.1.0',
    'summary': 'One engine for every printable document (invoices, receipts, vouchers, orders, delivery notes, stock '
               'vouchers): templates, colours, paper (A4, A5, 80 mm receipts), what to show, Amharic, live preview',
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_sales', 'sonador_purchase', 'sonador_inventory', 'sonador_payroll'],
    'data': ['security/ir.model.access.csv', 'report/print.xml', 'report/old.xml', 'views/views.xml'],
    'assets': {'web.assets_backend': ['sonador_print/static/src/designer/*']},
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
}
