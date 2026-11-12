{
    'name': 'Inventory',
    'version': '19.0.0.1.0',
    'summary': 'Peachtree-style inventory on the Odoo stock engine: GRN, GIV, transfers, counts, FIFO/AVCO, OWL reports',
    'description': """
Inventory
=========
Inventory documents (opening stock, goods received, goods issued, transfers, stock counts) and stock-moving
sales and purchase vouchers, all on standard Odoo stock moves and FIFO / AVCO costing, with the accounting
entries dated on the document date. Manufacturing, projects and construction build on the same moves.
""",
    'author': 'Sonador Systems',
    'category': 'Inventory',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'stock_account'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'views/stock_voucher_views.xml',
        'views/product_views.xml',
        'views/category_views.xml',
        'views/voucher_views.xml',
        'views/stock_warning_views.xml',
        'views/menus.xml',
        'views/gate_pass_views.xml',
        'report/documents.xml',
        'views/store_menus.xml',
        'views/series_views.xml',
        'data/sync_costs.xml',
        'data/defaults.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sonador_inventory/static/src/js/*',
            'sonador_inventory/static/src/home/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
