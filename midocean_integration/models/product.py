from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    vendor_stock_ids = fields.One2many('vendor.product.stock', 'product_id', string='Vendor Stock')
    vendor_asset_ids = fields.One2many('vendor.product.asset', 'product_id', string='Vendor Assets')
    midocean_vendor_id = fields.Many2one(related='product_tmpl_id.midocean_vendor_id', store=True, index=True)
    midocean_variant_id = fields.Char(index=True)
    midocean_color_code = fields.Char()
    midocean_color_description = fields.Char()
    midocean_color_group = fields.Char()
    midocean_pms_color = fields.Char()
    midocean_release_date = fields.Date()
    midocean_plc_status = fields.Char()
    midocean_plc_status_description = fields.Char()

    @api.model
    def _search(self, domain, *args, **kwargs):
        vendor_id = self.env.context.get('midocean_vendor_filter_id')
        if vendor_id:
            domain = [('midocean_vendor_id', '=', vendor_id)] + list(domain)
        return super()._search(domain, *args, **kwargs)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vendor_asset_ids = fields.One2many('vendor.product.asset', 'product_tmpl_id', string='Vendor Assets')
    midocean_vendor_id = fields.Many2one('external.vendor', index=True, string='MiDocean Vendor')
    midocean_master_id = fields.Char(index=True)
    midocean_master_code = fields.Char(index=True)
    midocean_type_of_products = fields.Char()
    midocean_commodity_code = fields.Char()
    midocean_country_of_origin = fields.Char()
    midocean_brand = fields.Char()
    midocean_category_code = fields.Char()
    midocean_product_class = fields.Char()
    midocean_dimensions = fields.Char()
    midocean_material = fields.Char()
    midocean_packaging_after_printing = fields.Char()
    midocean_printable = fields.Boolean()
    midocean_print_positions = fields.Integer()
    midocean_length = fields.Float()
    midocean_width = fields.Float()
    midocean_height = fields.Float()
    midocean_diameter = fields.Float()
    midocean_volume = fields.Float()
    midocean_gross_weight = fields.Float()
    midocean_net_weight = fields.Float()
    midocean_inner_carton_quantity = fields.Integer()
    midocean_outer_carton_quantity = fields.Integer()
    midocean_carton_length = fields.Float()
    midocean_carton_width = fields.Float()
    midocean_carton_height = fields.Float()
    midocean_carton_volume = fields.Float()
    midocean_carton_gross_weight = fields.Float()
    midocean_source_timestamp = fields.Datetime()


class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    vendor_api_id = fields.Many2one('vendor.api', ondelete='cascade', index=True)
    _vendor_product_unique = models.Constraint('unique(vendor_api_id, product_id)', 'A vendor API can only have one supplier price per product.')
