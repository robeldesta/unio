{
    'name': 'Leave Requests',
    'version': '19.0.0.1.0',
    'summary': 'Leave under the Ethiopian Labour Proclamation 1156/2019: annual leave by service, sick leave pay tiers, '
               'maternity, paternity, special and unpaid leave, Ethiopian public holidays, approvals, team calendar',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_people'],
    'data': [
        'security/ir.model.access.csv',
        'data/leave_types.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_timeoff/static/src/leave/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
