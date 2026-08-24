{
    "name": "Base Auth",
    "version": "19.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "Generic outbound HTTP authentication",
    "description": """
    6479384 | Generic outbound HTTP authentication
    ==================================================================
    Allow users to connect to external APIs using self-configured
    authentication methods.
    - API Key
    - Bearer Token
    - Basic Authentication
    - OAuth 2.0
    """,
    "author": "Odoo",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [
        "security/ir.model.access.csv",
        "views/base_auth_views.xml",
    ],
    "installable": True,
}
