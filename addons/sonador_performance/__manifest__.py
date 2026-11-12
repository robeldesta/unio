{
    'name': 'Performance',
    'version': '19.0.0.2.0',
    'summary': 'Performance for People: goals and OKRs (key results fed from KPIs and plans), a KPI library with '
               'computed measures, templates per position, performance levels and scorecards, review cycles, '
               'check-ins, one-to-ones, praise and feedback, 9-box',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_people', 'sonador_sales', 'sonador_me'],
    'data': [
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/template_views.xml',
        'views/kpi_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sonador_performance/static/src/perf/*',
        ],
    },
    'installable': True,
    'application': False,
}
