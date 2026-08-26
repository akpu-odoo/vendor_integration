from odoo import fields, models


class VendorIntegrationRecord(models.Model):
    _name = 'vendor.integration.record'
    _description = 'Vendor Integration External Record'
    _rec_name = 'external_id'

    vendor_api_id = fields.Many2one('vendor.api', required=True, ondelete='cascade', index=True)
    res_model_id = fields.Many2one('ir.model', index=True)
    external_id = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)

    _vendor_external_id_unique = models.Constraint(
        'unique(vendor_api_id, res_model_id, external_id)',
        'An external record can only be linked once for an API and model.',
    )
