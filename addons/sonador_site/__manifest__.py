{
    'name': 'Website',
    'version': '19.0.0.1.0',
    'summary': 'Your website and online shop: pages from ready-made blocks, products, prices and stock straight from '
               'your system, orders that land as sales orders, delivery or pickup, SEO, mobile first',
    'author': 'Sonador Systems',
    'category': 'Website',
    'license': 'LGPL-3',
    'depends': ['sonador_sales'],
    'data': [
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/views.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_site/static/src/site/*']},
    'installable': True,
    'application': True,
}
