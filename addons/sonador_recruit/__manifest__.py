{
    'name': 'Recruitment',
    'version': '19.0.0.1.0',
    'summary': 'Recruitment for People: openings, candidate pipeline, interviews and scorecards, offers, hire into '
               'People (onboarding starts)',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_onboarding'],
    'data': [
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_recruit/static/src/rc/*']},
    'installable': True,
    'application': False,
}
