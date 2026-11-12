{
    'name': 'Agenda',
    'version': '19.0.0.1.0',
    'summary': 'What is due and when, for each person: tax deadlines, bills to pay, customers to collect, cheques, payroll, '
               'renewals, probation ends, leave to approve, findings to look at, plus a short to-do list. It reads the '
               'dates the other apps already hold; nothing is typed twice, and an item goes away when its cause does.',
    'author': 'Sonador Systems',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/rules.xml',
        'views/views.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_agenda/static/src/agenda/*']},
    'installable': True,
    'application': True,
}
