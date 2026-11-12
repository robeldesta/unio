{
    'name': 'People',
    'version': '19.0.0.2.0',
    'summary': 'People (HR) on Odoo employees: directory, org chart, Ethiopian employee records, job and pay history, '
               'documents, assets, self-service',
    'description': """
People
======
One People app on the standard Odoo employee (hr.employee / hr.version), Ethiopian by default:
Labour Proclamation 1156/2019 probation (60 working days) on a 48-hour week, TIN, POESSA pension number,
Fayda ID, Region / Sub-city / Woreda / Kebele address, documents with expiry alerts, issued assets,
emergency contacts, effective-dated job and pay history feeding payroll, and self-service for every employee.
""",
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'hr'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/employee_views.xml',
        'views/approval_policy_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sonador_people/static/src/people/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
