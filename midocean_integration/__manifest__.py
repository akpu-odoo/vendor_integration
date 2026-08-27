{
    'name': 'MiDocean Integration',
    'version': '19.0.1.0.0',
    'summary': 'MiDocean catalogue, stock and supplier-price processing',
    'license': 'LGPL-3',
    'depends': ['vendor_integration'],
    'data': [
        'security/ir.model.access.csv',
        'data/midocean_demo.xml',
        'views/product_views.xml',
        'views/purchase_order_views.xml',
    ],
    'demo': ['data/midocean_demo.xml'],
}
