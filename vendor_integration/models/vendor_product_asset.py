from odoo import fields, models


class VendorProductAsset(models.Model):
    """A remote asset associated with a vendor product or product template."""

    _name = 'vendor.product.asset'
    _description = 'Vendor Product Asset'
    _rec_name = 'name'

    name = fields.Char(required=True)
    vendor_api_id = fields.Many2one(
        'vendor.api', required=True, ondelete='cascade', index=True,
    )
    asset_key = fields.Char(required=True, index=True)
    product_tmpl_id = fields.Many2one('product.template', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', ondelete='cascade', index=True)
    url = fields.Char(required=True)
    highres_url = fields.Char(string='High Resolution URL')
    asset_type = fields.Char()
    subtype = fields.Char()

    _asset_key_unique = models.Constraint(
        'unique(vendor_api_id, asset_key)',
        'A vendor asset can only be stored once per API.',
    )
