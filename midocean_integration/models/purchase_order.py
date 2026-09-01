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
        response = self.midocean_vendor_id.authentcation_method_id.request(
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
        if not lines or any(not line.product_id.default_code for line in lines):
            raise UserError(self.env._('Every MiDocean order line needs a product with an internal reference (SKU).'))
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
                'timestamp': fields.Datetime.to_string(fields.Datetime.now()),
                'contact_name': self.user_id.name,
                'order_type': 'SAMPLE' if self.midocean_order_type == 'sample' else 'NORMAL',
            },
            'order_lines': [
                {
                    'order_line_id': str(line.id),
                    'sku': line.product_id.default_code,
                    'quantity': str(line.product_qty),
                    'expected_price': str(line.price_unit),
                }
                for line in lines
            ],
        }

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
    midocean_available_print_technique_ids = fields.Many2many(
        'midocean.print.technique', compute='_compute_midocean_print_data',
    )
    midocean_position_technique_ids = fields.Many2many(
        'midocean.print.technique', compute='_compute_midocean_position_techniques',
    )
    midocean_print_position_id = fields.Many2one('midocean.print.position', string='Print Position')
    midocean_print_technique_id = fields.Many2one('midocean.print.technique', string='Print Technique')
    midocean_printing_cost = fields.Monetary(
        string='Printing Cost', currency_field='currency_id',
        help='Reserved for the MiDocean print-price calculation in phase two.',
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
            line.midocean_available_print_technique_ids = print_product.position_ids.technique_ids.technique_id

    @api.depends('midocean_print_position_id', 'midocean_available_print_technique_ids')
    def _compute_midocean_position_techniques(self):
        for line in self:
            line.midocean_position_technique_ids = (
                line.midocean_print_position_id.technique_ids.technique_id
                or line.midocean_available_print_technique_ids
            )

    @api.onchange('product_id', 'order_id.partner_id')
    def _onchange_midocean_print_product(self):
        for line in self:
            if not line.midocean_printing_required:
                line.midocean_print_position_id = False
                line.midocean_print_technique_id = False
                line.midocean_printing_cost = 0.0
            elif line.midocean_print_position_id not in line.midocean_print_product_id.position_ids:
                line.midocean_print_position_id = False
            elif line.midocean_print_technique_id not in line.midocean_position_technique_ids:
                line.midocean_print_technique_id = False

    @api.onchange('midocean_print_position_id')
    def _onchange_midocean_print_position(self):
        for line in self:
            if line.midocean_print_technique_id not in line.midocean_position_technique_ids:
                line.midocean_print_technique_id = False
