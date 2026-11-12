{
    'name': 'Sales Approvals',
    'version': '19.0.0.1.0',
    'summary': 'Optional finance check and commercial approval of sales orders before they are confirmed for delivery',
    'description': """
When the company switches it on (Company > Sales approvals), confirming a sales order sends it for approval
instead: finance checks it (prices, credit, payment), then the commercial manager approves it, and only then is it
confirmed and goes to delivery. Either can send it back with the reason. Orders waiting are in the approvers'
inbox in Me.
""",
    'author': 'Sonador Systems',
    'category': 'Sales',
    'license': 'LGPL-3',
    'depends': ['sonador_sales', 'sonador_me'],
    'data': ['security/groups.xml', 'views/sale_views.xml'],
    'installable': True,
}
