{
    'name': 'Projects',
    'version': '19.0.0.1.0',
    'summary': 'Projects and job costing: phases and tasks, budgets by cost code, bills of quantities, timesheets, '
               'actual costs from the books, milestone / time and materials / interim payment certificate billing with '
               'retention and advance recovery, subcontractors, job profitability and over/under billing',
    'author': 'Sonador Systems',
    'category': 'Services/Project',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_inventory', 'sonador_people'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/views.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_project/static/src/prj/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
