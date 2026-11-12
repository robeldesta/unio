{
    'name': 'Fixed Assets',
    'version': '19.0.0.1.0',
    'summary': 'Fixed-asset register: assets from bills, capital projects or opening balances, monthly depreciation posted to the '
               'ledger, disposals with gain or loss, asset register, depreciation schedule and PPE movement (IAS 16)',
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books'],
    'data': ['security/ir.model.access.csv', 'data/data.xml', 'data/cron.xml', 'views/menus.xml'],
    'assets': {'web.assets_backend': ['sonador_assets/static/src/fa/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
}
