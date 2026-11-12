{
    'name': 'Support',
    'version': '19.0.0.1.0',
    'summary': 'Report a problem with the system from any screen (the screen and record come with it); IT or the admin '
               'answers, fixes and the person who reported it confirms. Response and fix times by priority.',
    'author': 'Sonador Systems',
    'category': 'Services',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'web'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/sequence.xml',
        'views/views.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_support/static/src/support/*']},
    'installable': True,
    'application': True,
}
