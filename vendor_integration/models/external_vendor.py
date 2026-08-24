from odoo import api, fields, models


class ExternalVendor(models.Model):
    _name = 'external.vendor'
    _description = "External Vendors that will provide all the data"

    name = fields.Char(required=True)
    partner_id = fields.Many2one('res.partner')
    # Keep the original technical name so existing vendor configurations remain valid.
    authentcation_method_id = fields.Many2one(
        'base.auth', string='Authentication Method', required=True,
    )
    base_url = fields.Char(required=True)
    vendor_api_ids = fields.One2many('vendor.api', 'external_vendor_id')


    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for record in res:
            record.partner_id = self.env['res.partner'].create({
                'name': record.name,
                'external_vendor_id': record.id
            })

        return res
