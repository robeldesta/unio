{
    'name': 'Onboarding',
    'version': '19.0.0.1.0',
    'summary': 'Onboarding and offboarding checklists by role, policy acknowledgements, exit clearance and interview, '
               'certificate of service (Labour Proclamation 1156/2019 art. 12(8))',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_payroll'],
    'data': [
        'security/ir.model.access.csv',
        'data/templates.xml',
        'report/certificate.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_onboarding/static/src/ob/*']},
    'installable': True,
    'application': False,
}
