{
    'name': 'Leaving and clearance',
    'version': '19.0.0.1.0',
    'summary': 'Resignation or termination with clearance: what the person holds (vehicle, laptop, phone, SIM, fixed '
               'assets) and owes (staff debtor, loans, customer balance) is cleared by each department head, then HR, then '
               'the general manager; the person is paid their days and leaves after the last payroll.',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_people', 'sonador_payroll', 'mail'],
    'data': ['security/ir.model.access.csv', 'views/views.xml'],
    'assets': {'web.assets_backend': ['sonador_exit/static/src/exit/*']},
    'installable': True,
}
