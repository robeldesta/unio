{
    'name': 'Audit Room',
    'version': '19.0.0.1.0',
    'summary': 'For internal auditors who drop by a few times a month: start a visit, see what changed in the books '
               'since the last one, count the cash, raise findings with an owner and a due date, re-check earlier '
               'findings, end the visit with a report. Auditors only read the books.',
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'mail'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/sequence.xml',
        'views/views.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_audit/static/src/room/*']},
    'installable': True,
    'application': True,
}
