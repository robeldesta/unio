{
    'name': 'Budgets',
    'version': '19.0.0.1.0',
    'summary': 'Yearly budget per account and month (from last year with a growth rate), approval, budget against actual '
               '(account variance, income statement against budget)',
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books'],
    'data': ['security/ir.model.access.csv', 'views/menus.xml'],
    'assets': {'web.assets_backend': ['sonador_budget/static/src/bg/*']},
    'installable': True,
}
