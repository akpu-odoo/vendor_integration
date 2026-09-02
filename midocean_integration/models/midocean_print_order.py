"""Printable MiDocean purchase-order line configuration and pricing."""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


WHITE_TEXTILE_COLOURS = {'AS', 'WW', 'WD', 'WH', 'NB', 'NW', 'RH'}


class MidoceanPurchaseLinePrint(models.Model):
    """One print position requested for one MiDocean purchase-order line."""

    _name = 'midocean.purchase.line.print'
    _description = 'MiDocean Purchase Line Print'
    _order = 'sequence, id'

    purchase_line_id = fields.Many2one(
        'purchase.order.line', required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    print_product_id = fields.Many2one(
        related='purchase_line_id.midocean_print_product_id', readonly=True,
    )
    position_id = fields.Many2one(
        'midocean.print.position', required=True, ondelete='restrict',
    )
    technique_id = fields.Many2one(
        'midocean.print.technique', required=True, ondelete='restrict',
    )
    available_technique_ids = fields.Many2many(
        'midocean.print.technique', compute='_compute_available_techniques',
    )
    print_size_height = fields.Float(required=True)
    print_size_width = fields.Float(required=True)
    colour_count = fields.Integer(required=True, default=1)
    artwork_url = fields.Char(required=True)
    mockup_url = fields.Char()
    instruction = fields.Char()
    colour_ids = fields.One2many(
        'midocean.purchase.line.print.colour', 'print_id',
    )
    setup_cost = fields.Monetary(compute='_compute_costs', currency_field='currency_id')
    printing_cost = fields.Monetary(compute='_compute_costs', currency_field='currency_id')
    currency_id = fields.Many2one(related='purchase_line_id.currency_id')

    @api.depends('position_id')
    def _compute_available_techniques(self):
        for print_configuration in self:
            print_configuration.available_technique_ids = (
                print_configuration.position_id.technique_ids.technique_id
            )

    @api.depends(
        'purchase_line_id.product_qty', 'purchase_line_id.product_id',
        'technique_id', 'position_id', 'print_size_height', 'print_size_width',
        'colour_count', 'purchase_line_id.order_id.midocean_repeat_order',
    )
    def _compute_costs(self):
        for print_configuration in self:
            costs = print_configuration._calculate_costs()
            print_configuration.setup_cost = costs['setup']
            print_configuration.printing_cost = costs['printing']

    @api.onchange('position_id')
    def _onchange_position_id(self):
        for print_configuration in self:
            position = print_configuration.position_id
            if not position:
                continue
            print_configuration.print_size_height = position.max_print_size_height
            print_configuration.print_size_width = position.max_print_size_width
            if print_configuration.technique_id not in position.technique_ids.technique_id:
                print_configuration.technique_id = False

    @api.constrains(
        'position_id', 'technique_id', 'print_size_height', 'print_size_width',
        'colour_count', 'instruction', 'colour_ids',
    )
    def _check_print_configuration(self):
        for print_configuration in self:
            position = print_configuration.position_id
            relation = position.technique_ids.filtered(
                lambda item: item.technique_id == print_configuration.technique_id,
            )
            if not relation:
                raise ValidationError(self.env._('The selected technique is not available at this print position.'))
            if (
                print_configuration.print_size_height > position.max_print_size_height
                or print_configuration.print_size_width > position.max_print_size_width
            ):
                raise ValidationError(self.env._('The requested print size exceeds the allowed position size.'))
            if relation.max_colours and print_configuration.colour_count > relation.max_colours:
                raise ValidationError(self.env._('The selected technique does not allow this many colours.'))
            if print_configuration.colour_count <= 0:
                raise ValidationError(self.env._('At least one print colour is required.'))
            if len(print_configuration.instruction or '') > 300:
                raise ValidationError(self.env._('Print instructions cannot exceed 300 characters.'))
            if len(print_configuration.colour_ids) != print_configuration.colour_count:
                raise ValidationError(self.env._('Add exactly one Pantone colour for each requested print colour.'))

    def _calculate_costs(self):
        """Calculate setup and print cost from the imported MiDocean pricelist."""
        self.ensure_one()
        quantity = self.purchase_line_id.product_qty
        technique = self.technique_id
        variable_cost = self._matching_variable_cost()
        scale = self._matching_scale(variable_cost, quantity)
        print_colours = self._printing_colour_count()
        unit_print_price = scale.price if scale else 0.0
        next_colour_price = scale.next_price if scale else 0.0
        pricing_type = (technique.pricing_type or '').lower()
        if 'colour' in pricing_type or 'color' in pricing_type:
            printing = unit_print_price * quantity
            if print_colours > 1:
                additional_price = (
                    next_colour_price
                    if technique.next_colour_cost_indicator and next_colour_price
                    else unit_print_price
                )
                printing += additional_price * (print_colours - 1) * quantity
            setup_multiplier = self.colour_count
        else:
            printing = unit_print_price * quantity
            setup_multiplier = 1
        setup_unit_price = (
            technique.setup_repeat
            if self.purchase_line_id.order_id.midocean_repeat_order
            else technique.setup
        )
        return {'setup': setup_unit_price * setup_multiplier, 'printing': printing}

    def _matching_variable_cost(self):
        """Select the cost area; a shared boundary belongs to the higher area."""
        self.ensure_one()
        area = self.print_size_height * self.print_size_width / 100.0
        costs = self.technique_id.variable_cost_ids.sorted(
            key=lambda cost: (cost.area_from, cost.area_to), reverse=True,
        )
        return next((cost for cost in costs if cost.area_from <= area and (
            not cost.area_to or area <= cost.area_to
        )), costs[:1])

    @staticmethod
    def _matching_scale(variable_cost, quantity):
        """Use the highest quantity threshold that does not exceed the order."""
        if not variable_cost:
            return False
        scales = variable_cost.scale_ids.filtered(
            lambda scale: scale.minimum_quantity <= quantity,
        ).sorted(key=lambda scale: scale.minimum_quantity, reverse=True)
        return scales[:1]

    def _printing_colour_count(self):
        """Apply MiDocean's extra white layer for non-white ST textiles."""
        self.ensure_one()
        product = self.purchase_line_id.product_id
        is_textile = 'textile' in (product.product_tmpl_id.midocean_type_of_products or '').lower()
        if (
            is_textile
            and self.technique_id.code.startswith('ST')
            and product.midocean_color_code not in WHITE_TEXTILE_COLOURS
        ):
            return self.colour_count + 1
        return self.colour_count


class MidoceanPurchaseLinePrintColour(models.Model):
    _name = 'midocean.purchase.line.print.colour'
    _description = 'MiDocean Purchase Line Print Colour'
    _order = 'id'

    print_id = fields.Many2one(
        'midocean.purchase.line.print', required=True, ondelete='cascade',
    )
    colour = fields.Char(required=True)

    @api.constrains('colour')
    def _check_colour_length(self):
        for colour in self:
            if len(colour.colour) > 12:
                raise ValidationError(self.env._('Pantone colours cannot exceed 12 characters.'))
