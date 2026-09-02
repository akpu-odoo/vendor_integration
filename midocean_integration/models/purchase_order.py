from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    midocean_vendor_id = fields.Many2one(related='partner_id.external_vendor_id', readonly=True)
    midocean_order_number = fields.Char(copy=False, readonly=True)
    midocean_order_type = fields.Selection(
        [('normal', 'Normal'), ('sample', 'Sample')],
        default='normal',
    )
    midocean_repeat_order = fields.Boolean(
        string='Repeat Print Order',
        help='Use MiDocean repeat setup prices for printed order lines.',
    )
    midocean_response = fields.Json(copy=False, readonly=True)
    midocean_proof_line_id = fields.Char(copy=False)
    midocean_rejection_code = fields.Integer(default=3)
    midocean_rejection_comment = fields.Char()

    def _midocean_api_enabled(self):
        """Return the configured MiDocean API, if this is a MiDocean order."""
        self.ensure_one()
        return self.midocean_vendor_id.vendor_api_ids.filtered(lambda api: api.integration_type == 'midocean')[:1]

    def _midocean_request(self, method, path, payload=None, params=None):
        """Send an authenticated request through the vendor's auth connection."""
        self.ensure_one()
        if not self._midocean_api_enabled():
            raise UserError(self.env._('This purchase order does not use a MiDocean vendor.'))
        response = self.midocean_vendor_id.authentication_method_id.request(
            method, path, json=payload, params=params,
            headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        )
        if not response.success:
            message = '; '.join(error.message for error in response.errors)
            raise UserError(message or self.env._('MiDocean request failed.'))
        return response.data

    def _midocean_order_payload(self):
        """Build the MiDocean create-order payload from this purchase order."""
        self.ensure_one()
        address = self.dest_address_id or self.company_id.partner_id
        lines = self.order_line.filtered(lambda line: not line.display_type and line.product_id)
        if not lines:
            raise UserError(self.env._('Add at least one product line before sending the order.'))
        is_print_order = bool(lines.mapped('midocean_print_configuration_ids'))
        if not is_print_order and any(not line.product_id.default_code for line in lines):
            raise UserError(self.env._('Every MiDocean order line needs a product with an internal reference (SKU).'))
        if is_print_order:
            self._validate_midocean_print_order(lines)
        return {
            'order_header': {
                'preferred_shipping_date': (
                    fields.Date.to_string(self.date_planned.date())
                    if self.date_planned else False
                ),
                'check_price': 'false',
                'currency': self.currency_id.name,
                'contact_email': self.user_id.email or '',
                'shipping_address': {
                    'contact_name': address.name or '',
                    'company_name': address.commercial_company_name or '',
                    'street1': address.street or '',
                    'street2': address.street2 or '',
                    'postal_code': address.zip or '',
                    'city': address.city or '',
                    'region': address.state_id.code or '',
                    'country': address.country_id.code or '',
                    'email': address.email or self.user_id.email or '',
                    'phone': address.phone or '',
                },
                'po_number': self.name,
                'timestamp': fields.Datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'contact_name': self.user_id.name,
                'order_type': (
                    'PRINT' if is_print_order
                    else 'SAMPLE' if self.midocean_order_type == 'sample'
                    else 'NORMAL'
                ),
            },
            'order_lines': [
                line._midocean_print_order_line_payload()
                if is_print_order else line._midocean_regular_order_line_payload()
                for line in lines
            ],
        }

    def _validate_midocean_print_order(self, lines):
        """Ensure one request never mixes MiDocean print and regular lines."""
        if self.midocean_order_type == 'sample':
            raise UserError(self.env._('Sample orders cannot contain printing.'))
        if any(not line.midocean_print_configuration_ids for line in lines):
            raise UserError(self.env._(
                'A MiDocean print order cannot contain unprinted order lines.',
            ))
        if any(not line.midocean_printing_required for line in lines):
            raise UserError(self.env._('Every line in a print order must use a printable MiDocean product.'))

    def _midocean_number(self, data):
        """Find the returned MiDocean order number in a nested response."""
        if isinstance(data, dict):
            for key in ('order_number', 'orderNumber'):
                if data.get(key):
                    return str(data[key])
            for value in data.values():
                number = self._midocean_number(value)
                if number:
                    return number
        if isinstance(data, list):
            for value in data:
                number = self._midocean_number(value)
                if number:
                    return number
        return False

    def action_midocean_create_order(self):
        """Create the remote MiDocean order once and store its response."""
        for order in self:
            if order.midocean_order_number:
                continue
            data = order._midocean_request('post', '/gateway/order/2.1/create', order._midocean_order_payload())
            order.write({'midocean_response': data, 'midocean_order_number': order._midocean_number(data)})
        return True

    def action_midocean_refresh_order(self):
        """Fetch and store the latest remote order detail."""
        for order in self:
            if not order.midocean_order_number:
                raise UserError(self.env._('Send the purchase order to MiDocean first.'))
            order.write({'midocean_response': order._midocean_request(
                'get', '/gateway/order/2.1/detail', params={'order_number': order.midocean_order_number})})
        return True

    def action_midocean_approve_proof(self):
        """Approve the configured MiDocean proof line."""
        for order in self:
            order._midocean_proof('approve', {
                'order_number': order.midocean_order_number,
                'order_line_id': order.midocean_proof_line_id,
            })
        return True

    def action_midocean_reject_proof(self):
        """Reject the configured MiDocean proof line."""
        for order in self:
            order._midocean_proof('reject', {
                'order_number': order.midocean_order_number,
                'order_line_id': order.midocean_proof_line_id,
                'rejection_code': order.midocean_rejection_code,
                'rejection_comment': order.midocean_rejection_comment or '',
                'additional_files': [],
                'new_artworks': [],
            })
        return True

    def _midocean_proof(self, action, payload):
        """Send one proof decision after validating its required identifiers."""
        self.ensure_one()
        if not payload['order_number'] or not payload['order_line_id']:
            raise UserError(self.env._('MiDocean order number and proof line ID are required.'))
        self._midocean_request('post', '/gateway/proof/1.0/%s' % action, payload)

    def button_confirm(self):
        """Confirm in Odoo, then create an order for MiDocean suppliers."""
        result = super().button_confirm()
        self.filtered(
            lambda order: order._midocean_api_enabled() and not order.midocean_order_number
        ).action_midocean_create_order()
        return result


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    midocean_printing_required = fields.Boolean(compute='_compute_midocean_print_data')
    midocean_print_product_id = fields.Many2one(
        'midocean.print.product', compute='_compute_midocean_print_data',
    )
    midocean_print_configuration_ids = fields.One2many(
        'midocean.purchase.line.print', 'purchase_line_id', string='Print Configurations',
    )
    midocean_printing_cost = fields.Monetary(
        string='Printing Cost', compute='_compute_midocean_printing_cost',
        currency_field='currency_id',
    )

    @api.depends(
        'product_qty', 'midocean_print_configuration_ids.setup_cost',
        'midocean_print_configuration_ids.printing_cost',
    )
    def _compute_midocean_printing_cost(self):
        printable_lines = self.filtered('midocean_print_configuration_ids')
        vendor_ids = printable_lines.order_id.midocean_vendor_id.ids
        manipulation_codes = printable_lines.midocean_print_product_id.mapped(
            'print_manipulation_code',
        )
        manipulations = self.env['midocean.print.manipulation'].search([
            ('pricelist_id.vendor_id', 'in', vendor_ids),
            ('code', 'in', manipulation_codes),
        ])
        manipulations_by_vendor_and_code = {
            (manipulation.pricelist_id.vendor_id.id, manipulation.code): manipulation
            for manipulation in manipulations
        }
        for line in self:
            configurations = line.midocean_print_configuration_ids
            if not configurations:
                line.midocean_printing_cost = 0.0
                continue
            manipulation = manipulations_by_vendor_and_code.get((
                line.order_id.midocean_vendor_id.id,
                line.midocean_print_product_id.print_manipulation_code,
            ))
            handling_cost = manipulation.price * line.product_qty if manipulation else 0.0
            line.midocean_printing_cost = (
                sum(configurations.mapped('setup_cost'))
                + sum(configurations.mapped('printing_cost'))
                + handling_cost
            )

    @api.depends('product_id', 'product_id.product_tmpl_id.midocean_printable', 'order_id.midocean_vendor_id')
    def _compute_midocean_print_data(self):
        print_product_model = self.env['midocean.print.product']
        candidates = self.filtered(
            lambda line: line.order_id.midocean_vendor_id and line.product_id.product_tmpl_id.midocean_master_code
        )
        vendor_ids = candidates.order_id.midocean_vendor_id.ids
        master_codes = candidates.product_id.product_tmpl_id.mapped('midocean_master_code')
        print_products = print_product_model.search([
            ('vendor_id', 'in', vendor_ids), ('master_code', 'in', master_codes),
        ])
        products_by_vendor_and_code = {
            (product.vendor_id.id, product.master_code): product for product in print_products
        }
        for line in self:
            template = line.product_id.product_tmpl_id
            vendor = line.order_id.midocean_vendor_id
            print_product = products_by_vendor_and_code.get(
                (vendor.id, template.midocean_master_code), print_product_model.browse(),
            )
            line.midocean_printing_required = bool(vendor and template.midocean_printable)
            line.midocean_print_product_id = print_product

    @api.onchange('product_id', 'order_id.partner_id')
    def _onchange_midocean_print_product(self):
        for line in self:
            if not line.midocean_printing_required:
                line.midocean_printing_cost = 0.0

    def action_midocean_open_print_configurations(self):
        """Open the modal used to configure every print position on this line."""
        self.ensure_one()
        if not self.midocean_printing_required:
            raise UserError(self.env._('Only printable MiDocean products can be configured for printing.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Print Configuration'),
            'res_model': 'midocean.purchase.line.print',
            'view_mode': 'list,form',
            'target': 'new',
            'domain': [('purchase_line_id', '=', self.id)],
            'context': {'default_purchase_line_id': self.id},
        }

    def _midocean_regular_order_line_payload(self):
        """Build one NORMAL or SAMPLE MiDocean order line."""
        self.ensure_one()
        values = {
            'order_line_id': str(self.id),
            'sku': self.product_id.default_code,
            'quantity': str(self.product_qty),
            'expected_price': str(self.price_unit),
        }
        if self.product_id.midocean_variant_id:
            values['variant_id'] = self.product_id.midocean_variant_id
        return values

    def _midocean_print_order_line_payload(self):
        """Build one PRINT MiDocean order line from stored print configuration."""
        self.ensure_one()
        template = self.product_id.product_tmpl_id
        if not template.midocean_master_code or not self.product_id.midocean_color_code:
            raise UserError(self.env._(
                'Printed MiDocean lines require a master code and variant colour code.',
            ))
        print_item = {
            'item_color_number': self.product_id.midocean_color_code,
            'quantity': str(self.product_qty),
        }
        size = self._midocean_textile_size()
        is_textile = 'textile' in (template.midocean_type_of_products or '').lower()
        if is_textile and not size:
            raise UserError(self.env._('Printed textile products require a Size attribute value.'))
        if size:
            print_item['item_size'] = size
        return {
            'order_line_id': str(self.id),
            'master_code': template.midocean_master_code,
            'quantity': str(self.product_qty),
            'expected_price': '0',
            'printing_positions': [
                {
                    'id': configuration.position_id.position_id,
                    'print_size_height': str(configuration.print_size_height),
                    'print_size_width': str(configuration.print_size_width),
                    'printing_technique_id': configuration.technique_id.code,
                    'number_of_print_colors': str(configuration.colour_count),
                    'print_artwork_url': configuration.artwork_url,
                    'print_mockup_url': configuration.mockup_url or '',
                    'print_instruction': configuration.instruction or '',
                    'print_colors': [{'color': colour.colour} for colour in configuration.colour_ids],
                }
                for configuration in self.midocean_print_configuration_ids
            ],
            'print_items': [print_item],
        }

    def _midocean_textile_size(self):
        """Return the configured native Size attribute value for textile items."""
        self.ensure_one()
        size_value = self.product_id.product_template_attribute_value_ids.filtered(
            lambda value: value.attribute_id.name == 'Size',
        )[:1]
        return size_value.product_attribute_value_id.name if size_value else False
