{
    'name': 'Sales',
    'version': '19.0.0.2.0',
    'summary': 'Quotations and sales orders on Odoo Sales; invoices and cash sales move the stock; batched delivery orders',
    'author': 'Sonador Systems',
    'category': 'Sales',
    'license': 'LGPL-3',
    'depends': ['sonador_inventory', 'sale_management', 'sale_stock'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'views/delivery_views.xml',
        'views/pricing_views.xml',  # before sale_views: its layout moves sb_channel, which this file adds
        'views/sale_views.xml',
        'report/delivery_note.xml',
        'views/sales_app.xml',
        'views/series_views.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_sales/static/src/js/*']},
    'installable': True,
}
