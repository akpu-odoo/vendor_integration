from odoo import api, fields, models


class ExternalVendor(models.Model):
    _name = 'external.vendor'
    _description = 'External Vendor'

    name = fields.Char(required=True)
    partner_id = fields.Many2one('res.partner')
    # Keep the original technical name for compatibility with existing records.
    authentcation_method_id = fields.Many2one(
        'base.auth', string='Authentication Method', required=True,
    )
    base_url = fields.Char(required=True)
    vendor_api_ids = fields.One2many('vendor.api', 'external_vendor_id')
    currency_id = fields.Many2one(related='partner_id.property_purchase_currency_id', readonly=False)


    @api.model_create_multi
    def create(self, vals_list):
        """Create one supplier contact for every newly configured vendor."""
        vendors = super().create(vals_list)
        for vendor in vendors:
            vendor.partner_id = self.env['res.partner'].create({
                'name': vendor.name,
                'external_vendor_id': vendor.id,
            })
        return vendors
