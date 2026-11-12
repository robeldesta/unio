{
    'name': 'Manufacturing',
    'version': '19.0.0.1.0',
    'summary': 'Peachtree-style Build voucher on Odoo MRP: recipes (BOMs) with versions, labour and overhead absorption, production reports',
    'author': 'Sonador Systems',
    'category': 'Manufacturing',
    'license': 'LGPL-3',
    'depends': ['sonador_inventory', 'mrp'],
    'data': [
        'security/ir.model.access.csv',
        'views/mrp_views.xml',
        'views/store_views.xml',
    ],
    'assets': {
        'web.assets_backend': ['sonador_mrp/static/src/js/*'],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
}
