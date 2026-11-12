{
    'name': 'Fleet',
    'version': '19.0.0.1.0',
    'summary': 'Vehicles with Ethiopian plates, Bolo / insurance / permit expiries, fuel requests and fills with a fuel cheat '
               'checker (km per litre, odometer, tank size, duplicate and telebirr receipts, GPS), trips with gate pass and '
               'per diem, maintenance schedules and work orders, cost per vehicle and per km',
    'author': 'Sonador Systems',
    'category': 'Human Resources/Fleet',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_inventory', 'sonador_people'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/views.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_fleet/static/src/flt/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
