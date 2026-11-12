{
    'name': 'Cloud Subscriptions',
    'version': '19.0.0.1.0',
    'summary': 'Runs the cloud service: customer companies (one database each), plans and per-user pricing, instant sign-up '
               'from country templates, trials, nightly user counts and monthly subscription invoices',
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_crm'],
    'data': ['security/ir.model.access.csv', 'data/data.xml', 'data/help_articles.xml', 'views/views.xml'],
    'assets': {'web.assets_backend': ['sonador_saas/static/src/hq/*']},
    'installable': True,
    'application': True,
}
