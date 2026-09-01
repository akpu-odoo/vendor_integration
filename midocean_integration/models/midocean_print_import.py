"""MiDocean adapters for API responses that contain nested, multiple root lists."""

from datetime import timedelta

from odoo import fields, models


class VendorApi(models.Model):
    _inherit = 'vendor.api'

    def _response_records(self, payload):
        """Expose a useful record count when testing a nested print endpoint."""
        if self.integration_type == 'midocean' and self.api_purpose == 'print_data':
            return payload.get('products', [])
        if self.integration_type == 'midocean' and self.api_purpose == 'print_pricelist':
            return payload.get('print_techniques', [])
        return super()._response_records(payload)

    def _sync(self):
        if self.integration_type == 'midocean' and self.api_purpose in ('print_data', 'print_pricelist'):
            return self._sync_midocean_print_payload()
        return super()._sync()

    @staticmethod
    def _number(value):
        """Parse MiDocean's decimal-comma and thousands-separated numbers."""
        if value in (None, ''):
            return 0.0
        value = str(value).strip()
        if ',' in value:
            value = value.replace('.', '').replace(',', '.')
        elif value.count('.') > 1 or ('.' in value and len(value.rsplit('.', 1)[1]) == 3):
            value = value.replace('.', '')
        return float(value)

    def _sync_midocean_print_payload(self):
        """Fetch and persist a complete print response in one transaction."""
        self.ensure_one()
        payload = self._get_json()
        saved = (
            self._import_midocean_print_data(payload)
            if self.api_purpose == 'print_data'
            else self._import_midocean_print_pricelist(payload)
        )
        now = fields.Datetime.now()
        self.write({
            'last_sync_on': now,
            'next_sync_on': now + timedelta(hours=self.sync_interval_hours),
            'sync_cursor': 0,
            'sync_payload': False,
            'last_sync_message': self.env._('%s print record(s) saved.', saved),
        })
        return self.env['midocean.print.product'].browse(), payload

    def _import_midocean_print_data(self, payload):
        """Bulk-upsert techniques and rebuild positions for changed print products."""
        vendor_id = self.external_vendor_id.id
        product_sources = {
            source['master_code']: source
            for source in payload.get('products', [])
            if source.get('master_code')
        }
        techniques = self._upsert_midocean_print_techniques(
            payload.get('printing_technique_descriptions', []), product_sources.values(), vendor_id,
        )
        master_codes = list(product_sources)
        product_model = self.env['midocean.print.product']
        existing = product_model.search([
            ('vendor_id', '=', vendor_id), ('master_code', 'in', master_codes),
        ])
        products_by_code = {product.master_code: product for product in existing}
        templates = self.env['product.template'].search([
            ('midocean_vendor_id', '=', vendor_id), ('midocean_master_code', 'in', master_codes),
        ])
        templates_by_code = {template.midocean_master_code: template for template in templates}
        create_values = []
        for code, source in product_sources.items():
            values = self._midocean_print_product_values(source, vendor_id, templates_by_code.get(code))
            if code in products_by_code:
                products_by_code[code].write(values)
            else:
                create_values.append(values)
        created = product_model.create(create_values)
        products_by_code.update({product.master_code: product for product in created})

        # The API provides a complete position set per product.  Removing those
        # child rows once and inserting their replacement in bulk avoids stale
        # positions while keeping the number of SQL operations low.
        existing.position_ids.unlink()
        self._create_midocean_print_positions(product_sources, products_by_code, techniques)
        return len(product_sources) + len(techniques)

    def _upsert_midocean_print_techniques(self, descriptions, product_sources, vendor_id):
        names = {}
        for source in descriptions:
            code = source.get('id')
            if code:
                translations = source.get('name') or []
                names[code] = next(
                    (item.get('nl') for item in translations if item.get('nl')),
                    next((item.get('en') for item in translations if item.get('en')), code),
                )
        for product in product_sources:
            for position in product.get('printing_positions') or []:
                for technique in position.get('printing_techniques') or []:
                    if technique.get('id'):
                        names.setdefault(technique['id'], technique['id'])
        technique_model = self.env['midocean.print.technique']
        existing = technique_model.search([
            ('vendor_id', '=', vendor_id), ('code', 'in', list(names)),
        ])
        techniques = {technique.code: technique for technique in existing}
        for code, name in names.items():
            if code in techniques:
                if techniques[code].name != name:
                    techniques[code].write({'name': name})
            else:
                techniques[code] = technique_model.create({
                    'vendor_id': vendor_id, 'code': code, 'name': name,
                })
        return techniques

    def _midocean_print_product_values(self, source, vendor_id, template):
        return {
            'vendor_id': vendor_id,
            'product_tmpl_id': template.id if template else False,
            'master_code': source['master_code'],
            'master_id': source.get('master_id'),
            'item_color_numbers': source.get('item_color_numbers') or [],
            'print_manipulation_code': source.get('print_manipulation'),
            'print_template_url': source.get('print_template'),
        }

    def _create_midocean_print_positions(self, product_sources, products_by_code, techniques):
        position_values, position_sources = [], []
        for master_code, product_source in product_sources.items():
            for source in product_source.get('printing_positions') or []:
                if not source.get('position_id'):
                    continue
                position_values.append({
                    'print_product_id': products_by_code[master_code].id,
                    'position_id': source['position_id'],
                    'print_size_unit': source.get('print_size_unit'),
                    'max_print_size_height': self._number(source.get('max_print_size_height')),
                    'max_print_size_width': self._number(source.get('max_print_size_width')),
                    'rotation': self._number(source.get('rotation')),
                    'position_type': source.get('print_position_type'),
                    'category': source.get('category'),
                    'points': source.get('points') or [],
                })
                position_sources.append(source)
        positions = self.env['midocean.print.position'].create(position_values) if position_values else self.env['midocean.print.position']
        technique_values, image_values = [], []
        for position, source in zip(positions, position_sources):
            for technique_source in source.get('printing_techniques') or []:
                technique = techniques.get(technique_source.get('id'))
                if technique:
                    technique_values.append({
                        'position_id': position.id,
                        'technique_id': technique.id,
                        'is_default': bool(technique_source.get('default')),
                        'max_colours': int(technique_source.get('max_colours') or 0),
                    })
            image_values.extend({
                'position_id': position.id,
                'variant_color': image.get('variant_color'),
                'blank_url': image.get('print_position_image_blank'),
                'with_area_url': image.get('print_position_image_with_area'),
            } for image in source.get('images') or [])
        if technique_values:
            self.env['midocean.print.position.technique'].create(technique_values)
        if image_values:
            self.env['midocean.print.position.image'].create(image_values)

    def _import_midocean_print_pricelist(self, payload):
        """Enrich shared techniques with the vendor's current pricing data."""
        pricelist = self._midocean_print_pricelist(payload)
        pricelist.manipulation_ids.unlink()
        self.env['midocean.print.manipulation'].create([{
            'pricelist_id': pricelist.id,
            'code': source['code'],
            'description': source.get('description'),
            'price': self._number(source.get('price')),
        } for source in payload.get('print_manipulations') or [] if source.get('code')])
        self._sync_midocean_print_technique_prices(pricelist, payload.get('print_techniques') or [])
        return 1 + len(payload.get('print_manipulations') or []) + len(payload.get('print_techniques') or [])

    def _midocean_print_pricelist(self, payload):
        currency = self.env['res.currency'].search([('name', '=', payload.get('currency'))], limit=1)
        values = {
            'vendor_id': self.external_vendor_id.id,
            'currency_id': currency.id,
            'valid_from': payload.get('pricelist_valid_from'),
            'valid_until': payload.get('pricelist_valid_until'),
        }
        model = self.env['midocean.print.pricelist']
        pricelist = model.search([('vendor_id', '=', self.external_vendor_id.id)], limit=1)
        if pricelist:
            pricelist.write(values)
        else:
            pricelist = model.create(values)
        return pricelist

    def _sync_midocean_print_technique_prices(self, pricelist, sources):
        """Upsert technique prices and bulk-replace their nested cost scales."""
        sources_by_code = {source['id']: source for source in sources if source.get('id')}
        previous_techniques = pricelist.technique_ids
        previous_techniques.variable_cost_ids.unlink()
        stale_techniques = previous_techniques.filtered(lambda technique: technique.code not in sources_by_code)
        if stale_techniques:
            stale_techniques.write({
                'pricelist_id': False,
                'pricing_description': False,
                'pricing_type': False,
                'setup': 0.0,
                'setup_repeat': 0.0,
                'next_colour_cost_indicator': False,
            })
        technique_model = self.env['midocean.print.technique']
        existing = technique_model.search([
            ('vendor_id', '=', self.external_vendor_id.id), ('code', 'in', list(sources_by_code)),
        ])
        techniques_by_code = {technique.code: technique for technique in existing}
        missing_codes = set(sources_by_code) - set(techniques_by_code)
        if missing_codes:
            created = technique_model.create([{
                'vendor_id': self.external_vendor_id.id,
                'code': code,
                'name': sources_by_code[code].get('description') or code,
            } for code in missing_codes])
            techniques_by_code.update({technique.code: technique for technique in created})
        for code, source in sources_by_code.items():
            techniques_by_code[code].write({
                'pricelist_id': pricelist.id,
                'pricing_description': source.get('description'),
                'pricing_type': source.get('pricing_type'),
                'setup': self._number(source.get('setup')),
                'setup_repeat': self._number(source.get('setup_repeat')),
                'next_colour_cost_indicator': str(source.get('next_colour_cost_indicator')).lower() == 'true',
            })
        variable_values, variable_sources = [], []
        for code, source in sources_by_code.items():
            technique = techniques_by_code[code]
            for variable_source in source.get('var_costs') or []:
                variable_values.append({
                    'technique_id': technique.id,
                    'range_id': variable_source.get('range_id'),
                    'area_from': self._number(variable_source.get('area_from')),
                    'area_to': self._number(variable_source.get('area_to')),
                })
                variable_sources.append(variable_source)
        variables = self.env['midocean.print.variable.cost'].create(variable_values) if variable_values else self.env['midocean.print.variable.cost']
        scale_values = [{
            'variable_cost_id': variable.id,
            'minimum_quantity': self._number(scale.get('minimum_quantity')),
            'price': self._number(scale.get('price')),
            'next_price': self._number(scale.get('next_price')),
        } for variable, source in zip(variables, variable_sources) for scale in source.get('scales') or []]
        if scale_values:
            self.env['midocean.print.price.scale'].create(scale_values)
