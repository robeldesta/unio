{
    'name': 'CRM',
    'version': '19.0.0.1.0',
    'summary': 'HubSpot-style CRM on your customers and sales: contacts and companies, deals board, timeline of calls, '
               'meetings, field visits and Telegram, tasks, reorders due, dashboard and targets, in the Ethiopian calendar',
    'author': 'Sonador Systems',
    'category': 'Sales/CRM',
    'license': 'LGPL-3',
    'depends': ['sonador_sales', 'sonador_people'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_crm/static/src/crm/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
