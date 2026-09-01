from odoo import fields, models


class MidoceanPrintTechnique(models.Model):
    _name = 'midocean.print.technique'
    _description = 'MiDocean Print Technique'
    _rec_name = 'name'

    vendor_id = fields.Many2one('external.vendor', required=True, ondelete='cascade', index=True)
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, string='Name (NL)')
    pricelist_id = fields.Many2one('midocean.print.pricelist', ondelete='set null', index=True)
    pricing_description = fields.Char()
    pricing_type = fields.Char()
    setup = fields.Float()
    setup_repeat = fields.Float()
    next_colour_cost_indicator = fields.Boolean()
    variable_cost_ids = fields.One2many('midocean.print.variable.cost', 'technique_id')

    _vendor_technique_unique = models.Constraint(
        'unique(vendor_id, code)', 'A print technique code must be unique per vendor.',
    )


class MidoceanPrintProduct(models.Model):
    _name = 'midocean.print.product'
    _description = 'MiDocean Product Print Data'
    _rec_name = 'master_code'

    vendor_id = fields.Many2one('external.vendor', required=True, index=True)
    product_tmpl_id = fields.Many2one('product.template', index=True)
    master_code = fields.Char(required=True, index=True)
    master_id = fields.Char(index=True)
    item_color_numbers = fields.Json()
    print_manipulation_code = fields.Char()
    print_template_url = fields.Char()
    position_ids = fields.One2many('midocean.print.position', 'print_product_id')

    _vendor_product_print_unique = models.Constraint(
        'unique(vendor_id, master_code)', 'Print data must be unique per vendor and master code.',
    )


class MidoceanPrintPosition(models.Model):
    _name = 'midocean.print.position'
    _description = 'MiDocean Print Position'
    _rec_name = 'position_id'

    print_product_id = fields.Many2one('midocean.print.product', required=True, ondelete='cascade', index=True)
    position_id = fields.Char(required=True)
    print_size_unit = fields.Char()
    max_print_size_height = fields.Float()
    max_print_size_width = fields.Float()
    rotation = fields.Float()
    position_type = fields.Char()
    category = fields.Char()
    points = fields.Json()
    technique_ids = fields.One2many('midocean.print.position.technique', 'position_id')
    image_ids = fields.One2many('midocean.print.position.image', 'position_id')

    _product_position_unique = models.Constraint(
        'unique(print_product_id, position_id)', 'A print position must be unique per print-data product.',
    )


class MidoceanPrintPositionTechnique(models.Model):
    _name = 'midocean.print.position.technique'
    _description = 'MiDocean Print Position Technique'
    _rec_name = 'technique_id'

    position_id = fields.Many2one('midocean.print.position', required=True, ondelete='cascade', index=True)
    technique_id = fields.Many2one('midocean.print.technique', required=True, ondelete='cascade', index=True)
    is_default = fields.Boolean()
    max_colours = fields.Integer()

    _position_technique_unique = models.Constraint(
        'unique(position_id, technique_id)', 'A technique can only occur once on a print position.',
    )


class MidoceanPrintPositionImage(models.Model):
    _name = 'midocean.print.position.image'
    _description = 'MiDocean Print Position Image'

    position_id = fields.Many2one('midocean.print.position', required=True, ondelete='cascade', index=True)
    variant_color = fields.Char(index=True)
    blank_url = fields.Char(string='Blank Image URL')
    with_area_url = fields.Char(string='Image with Print Area URL')

    _position_image_unique = models.Constraint(
        'unique(position_id, variant_color)', 'A print position can only have one image per variant colour.',
    )
