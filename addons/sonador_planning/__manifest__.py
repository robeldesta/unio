{
    'name': 'Planning',
    'version': '19.0.0.4.0',
    'summary': 'Monthly work plans approved by the line manager, a week that builds itself, one-tap daily check-ins, '
               'a Friday report that writes itself, a plan checker that catches vague, copied or junk plans, '
               'Excel templates both ways, roles with must-do items, change requests on approved plans, '
               'and a planning gate for the people who must plan',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_performance', 'sonador_timeoff'],
    'data': [
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/menus.xml',
        'views/okr_views.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_planning/static/src/plan/*']},
    'external_dependencies': {'python': ['openpyxl']},
    'installable': True,
    'application': True,
}
