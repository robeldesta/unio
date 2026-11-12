{
    'name': 'Farm',
    'version': '19.0.0.1.0',
    'summary': 'One farm app for every kind of farm, poultry first (layers and broilers): farms, houses and flocks; the '
               'daily record on a phone (deaths, feed, eggs by grade, weights, manure) with yesterday beside each field; '
               'laying %, feed per bird, weight and FCR against the breed; the health calendar with withdrawal days; '
               'alerts; and every flock a cost centre of the books: bought through purchasing, fed from the store, birds '
               'worn off monthly, sold through sales, cost per egg or kg and profit per flock',
    'author': 'Sonador Systems',
    'category': 'Agriculture',
    'license': 'LGPL-3',
    'depends': ['sonador_project', 'sonador_mrp', 'sonador_purchase', 'sonador_people'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/views.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_farm/static/src/farm/*']},
    'installable': True,
    'application': True,
}
