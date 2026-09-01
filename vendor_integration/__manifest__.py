{
    "name": "Multiple Vendor Integration",
    "version": "19.0.1.0.1",
    "summary": "Configure and synchronize external vendor APIs",
    "description": """
        Configure external vendors, API endpoints, field mappings, and scheduled
        synchronization into any Odoo model.
    """,
    "category": "Custom",
    "author": "Odoo PS",
    "website": "https://www.odoo.com",
    "license": "LGPL-3",
    "depends": ["base_auth", "purchase"],
    "data": [
        "security/ir.model.access.csv",
        "views/external_vendor_views.xml",
        "views/vendor_api_views.xml",
        "views/field_mapping_views.xml",
        "views/res_partner_views.xml",
        "views/vendor_integration_menus.xml",
        "data/vendor_integration_cron.xml",
    ],
}
