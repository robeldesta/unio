{
    'name': 'Production',
    'version': '19.0.0.1.0',
    'summary': 'The factory floor: production board by machine and shift, orders run from the floor (output, waste, downtime '
               'per shift), material check and shortages, quality checklists, lot traceability, yield, OEE, and costing '
               'through the Build voucher',
    'author': 'Sonador Systems',
    'category': 'Manufacturing',
    'license': 'LGPL-3',
    'depends': ['sonador_mrp', 'sonador_people'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_factory/static/src/fac/*']},
    'installable': True,
    'application': True,
}
