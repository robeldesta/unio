{
    'name': 'Payroll',
    'version': '19.0.0.3.1',
    'summary': 'Ethiopian payroll on People: cost-centre accounts, overtime at 1.5/1.75/2/2.5 (art. 68), leave pay from '
               'Time Off, loans and advances, recurring benefits, commission, payslips, bank transfer files, final '
               'settlement (notice, severance art. 39-41, leave pay-out art. 77(4))',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_people', 'sonador_timeoff'],
    'data': [
        'security/ir.model.access.csv',
        'views/menus.xml',
        'report/bank_letter.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_payroll/static/src/pay/*']},
    'installable': True,
    'application': False,
}
