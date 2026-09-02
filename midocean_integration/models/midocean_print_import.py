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

    def _sync_custom_payload(self):
        """Handle MiDocean's nested print endpoints through the base hook."""
        if (
            self.integration_type == 'midocean'
            and self.api_purpose in ('print_data', 'print_pricelist')
        ):
            return self._sync_midocean_print_payload()
        return super()._sync_custom_payload()

    @staticmethod
    def _parse_number(value):
        """Parse MiDocean's decimal-comma and thousands-separated numbers."""
        if value in (None, ''):
            return 0.0
        value = str(value).strip()
        if ',' in value:
            value = value.replace('.', '').replace(',', '.')
        elif value.count('.') > 1 or ('.' in value and len(value.rsplit('.', 1)[1]) == 3):
            value = value.replace('.', '')
        return float(value)

    @staticmethod
    def _parse_boolean(value):
        """Accept MiDocean's boolean and ``X`` indicator representations."""
        return str(value).strip().lower() in {'1', 'true', 'yes', 'x'}

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
            values = self._midocean_print_product_values(
                source, vendor_id, templates_by_code.get(code),
            )
            if code in products_by_code:
                products_by_code[code].write(values)
            else:
                create_values.append(values)
        created = product_model.create(create_values) if create_values else product_model
        products_by_code.update({product.master_code: product for product in created})

        self._sync_midocean_print_positions(
            product_sources, products_by_code, techniques,
        )
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

    def _sync_midocean_print_positions(
        self, product_sources, products_by_code, techniques,
    ):
        """Upsert positions and their children without changing existing IDs."""
        print_product_ids = [product.id for product in products_by_code.values()]
        position_model = self.env['midocean.print.position'].with_context(
            active_test=False,
        )
        existing_positions = position_model.search([
            ('print_product_id', 'in', print_product_ids),
        ])
        positions_by_key = {
            (position.print_product_id.id, position.position_id): position
            for position in existing_positions
        }
        position_values_by_key = {}
        for master_code, product_source in product_sources.items():
            for source in product_source.get('printing_positions') or []:
                if not source.get('position_id'):
                    continue
                print_product = products_by_code[master_code]
                key = (print_product.id, source['position_id'])
                position_values_by_key[key] = {
                    'print_product_id': products_by_code[master_code].id,
                    'position_id': source['position_id'],
                    'active': True,
                    'print_size_unit': source.get('print_size_unit'),
                    'max_print_size_height': self._parse_number(
                        source.get('max_print_size_height'),
                    ),
                    'max_print_size_width': self._parse_number(
                        source.get('max_print_size_width'),
                    ),
                    'rotation': self._parse_number(source.get('rotation')),
                    'position_type': source.get('print_position_type'),
                    'category': source.get('category'),
                    'points': source.get('points') or [],
                }
        # The endpoint supplies the full position set for each returned product.
        # Deactivation preserves history and document references for rows that
        # are no longer supplied by MiDocean.
        stale_positions = existing_positions.filtered(
            lambda position: (
                position.print_product_id.id, position.position_id,
            ) not in position_values_by_key,
        )
        if stale_positions:
            stale_positions.write({'active': False})
        new_position_values = []
        for key, values in position_values_by_key.items():
            position = positions_by_key.get(key)
            if position:
                position.write(values)
            else:
                new_position_values.append(values)
        if new_position_values:
            created_positions = position_model.create(new_position_values)
            positions_by_key.update({
                (position.print_product_id.id, position.position_id): position
                for position in created_positions
            })
        self._sync_midocean_print_position_children(
            product_sources, products_by_code, positions_by_key, techniques,
        )

    def _sync_midocean_print_position_children(
        self, product_sources, products_by_code, positions_by_key, techniques,
    ):
        """Upsert allowed techniques and preview images for each print position."""
        positions = self.env['midocean.print.position'].browse([
            positions_by_key[(products_by_code[master_code].id, source['position_id'])].id
            for master_code, product_source in product_sources.items()
            for source in product_source.get('printing_positions') or []
            if source.get('position_id')
        ])
        technique_relation_model = self.env['midocean.print.position.technique']
        image_model = self.env['midocean.print.position.image']
        existing_techniques = technique_relation_model.with_context(
            active_test=False,
        ).search([('position_id', 'in', positions.ids)])
        existing_images = image_model.with_context(active_test=False).search([
            ('position_id', 'in', positions.ids),
        ])
        relations_by_key = {
            (relation.position_id.id, relation.technique_id.id): relation
            for relation in existing_techniques
        }
        images_by_key = {
            (image.position_id.id, image.variant_color): image
            for image in existing_images
        }
        new_relations, new_images = [], []
        source_relation_keys, source_image_keys = set(), set()
        for master_code, product_source in product_sources.items():
            print_product = products_by_code[master_code]
            for source in product_source.get('printing_positions') or []:
                if not source.get('position_id'):
                    continue
                position = positions_by_key[(print_product.id, source['position_id'])]
                for technique_source in source.get('printing_techniques') or []:
                    technique = techniques.get(technique_source.get('id'))
                    if not technique:
                        continue
                    source_relation_keys.add((position.id, technique.id))
                    values = {
                        'position_id': position.id,
                        'technique_id': technique.id,
                        'active': True,
                        'is_default': bool(technique_source.get('default')),
                        'max_colours': int(technique_source.get('max_colours') or 0),
                    }
                    relation = relations_by_key.get((position.id, technique.id))
                    if relation:
                        relation.write(values)
                    else:
                        new_relations.append(values)
                for image_source in source.get('images') or []:
                    key = (position.id, image_source.get('variant_color'))
                    source_image_keys.add(key)
                    values = {
                        'position_id': position.id,
                        'variant_color': image_source.get('variant_color'),
                        'active': True,
                        'blank_url': image_source.get('print_position_image_blank'),
                        'with_area_url': image_source.get('print_position_image_with_area'),
                    }
                    image = images_by_key.get(key)
                    if image:
                        image.write(values)
                    else:
                        new_images.append(values)
        stale_relations = existing_techniques.filtered(
            lambda relation: (
                relation.position_id.id, relation.technique_id.id,
            ) not in source_relation_keys,
        )
        stale_images = existing_images.filtered(
            lambda image: (image.position_id.id, image.variant_color)
            not in source_image_keys,
        )
        if stale_relations:
            stale_relations.write({'active': False})
        if stale_images:
            stale_images.write({'active': False})
        if new_relations:
            technique_relation_model.create(new_relations)
        if new_images:
            image_model.create(new_images)

    def _import_midocean_print_pricelist(self, payload):
        """Enrich shared techniques with the vendor's current pricing data."""
        pricelist = self._midocean_print_pricelist(payload)
        self._sync_midocean_print_manipulations(
            pricelist, payload.get('print_manipulations') or [],
        )
        self._sync_midocean_print_technique_prices(pricelist, payload.get('print_techniques') or [])
        return (
            1
            + len(payload.get('print_manipulations') or [])
            + len(payload.get('print_techniques') or [])
        )

    def _midocean_print_pricelist(self, payload):
        currency = self.env['res.currency'].search(
            [('name', '=', payload.get('currency'))], limit=1,
        )
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

    def _sync_midocean_print_manipulations(self, pricelist, sources):
        """Upsert manipulation charges and retain rows used by past documents."""
        sources_by_code = {source['code']: source for source in sources if source.get('code')}
        manipulation_model = self.env['midocean.print.manipulation'].with_context(
            active_test=False,
        )
        existing = manipulation_model.search([
            ('pricelist_id', '=', pricelist.id),
        ])
        manipulations_by_code = {
            manipulation.code: manipulation for manipulation in existing
        }
        new_values = []
        for code, source in sources_by_code.items():
            values = {
                'pricelist_id': pricelist.id,
                'code': code,
                'active': True,
                'description': source.get('description'),
                'price': self._parse_number(source.get('price')),
            }
            manipulation = manipulations_by_code.get(code)
            if manipulation:
                manipulation.write(values)
            else:
                new_values.append(values)
        stale_manipulations = existing.filtered(
            lambda manipulation: manipulation.code not in sources_by_code,
        )
        if stale_manipulations:
            stale_manipulations.write({'active': False})
        if new_values:
            manipulation_model.create(new_values)

    def _sync_midocean_print_technique_prices(self, pricelist, sources):
        """Upsert technique prices and bulk-replace their nested cost scales."""
        sources_by_code = {source['id']: source for source in sources if source.get('id')}
        previous_techniques = pricelist.with_context(active_test=False).technique_ids
        previous_variable_costs = previous_techniques.variable_cost_ids.with_context(
            active_test=False,
        )
        stale_techniques = previous_techniques.filtered(
            lambda technique: technique.code not in sources_by_code,
        )
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
                'setup': self._parse_number(source.get('setup')),
                'setup_repeat': self._parse_number(source.get('setup_repeat')),
                'next_colour_cost_indicator': self._parse_boolean(
                    source.get('next_colour_cost_indicator'),
                ),
            })
        variable_cost_model = self.env['midocean.print.variable.cost'].with_context(
            active_test=False,
        )
        existing_variable_costs = variable_cost_model.search([
            ('technique_id', 'in', [
                technique.id for technique in techniques_by_code.values()
            ]),
        ])
        variable_costs_by_key = {
            (cost.technique_id.id, cost.source_key): cost
            for cost in existing_variable_costs
        }
        new_variable_values, variable_sources = [], []
        for code, source in sources_by_code.items():
            technique = techniques_by_code[code]
            for variable_source in source.get('var_costs') or []:
                source_key = self._midocean_variable_cost_key(variable_source)
                values = {
                    'technique_id': technique.id,
                    'active': True,
                    'source_key': source_key,
                    'range_id': variable_source.get('range_id'),
                    'area_from': self._parse_number(variable_source.get('area_from')),
                    'area_to': self._parse_number(variable_source.get('area_to')),
                }
                variable_cost = variable_costs_by_key.get((technique.id, source_key))
                if variable_cost:
                    variable_cost.write(values)
                else:
                    new_variable_values.append(values)
                variable_sources.append((technique.id, source_key, variable_source))
        current_variable_keys = {
            (technique_id, source_key)
            for technique_id, source_key, _source in variable_sources
        }
        stale_variable_costs = previous_variable_costs.filtered(
            lambda cost: (cost.technique_id.id, cost.source_key)
            not in current_variable_keys,
        )
        if stale_variable_costs:
            stale_variable_costs.write({'active': False})
        if new_variable_values:
            created_costs = variable_cost_model.create(new_variable_values)
            variable_costs_by_key.update({
                (cost.technique_id.id, cost.source_key): cost for cost in created_costs
            })
        self._sync_midocean_print_price_scales(
            variable_sources, variable_costs_by_key, previous_variable_costs,
        )

    def _sync_midocean_print_price_scales(
        self, variable_sources, costs_by_key, previous_variable_costs,
    ):
        """Upsert quantity scales while retaining rows referenced elsewhere."""
        scale_model = self.env['midocean.print.price.scale'].with_context(
            active_test=False,
        )
        existing_scales = scale_model.search([
            ('variable_cost_id', 'in', previous_variable_costs.ids),
        ])
        scales_by_key = {
            (scale.variable_cost_id.id, scale.source_key): scale
            for scale in existing_scales
        }
        new_values = []
        current_scale_keys = set()
        for technique_id, cost_key, source in variable_sources:
            variable_cost = costs_by_key[(technique_id, cost_key)]
            for scale_source in source.get('scales') or []:
                source_key = self._midocean_price_scale_key(scale_source)
                current_scale_keys.add((variable_cost.id, source_key))
                values = {
                    'variable_cost_id': variable_cost.id,
                    'active': True,
                    'source_key': source_key,
                    'minimum_quantity': self._parse_number(
                        scale_source.get('minimum_quantity'),
                    ),
                    'price': self._parse_number(scale_source.get('price')),
                    'next_price': self._parse_number(scale_source.get('next_price')),
                }
                scale = scales_by_key.get((variable_cost.id, source_key))
                if scale:
                    scale.write(values)
                else:
                    new_values.append(values)
        stale_scales = existing_scales.filtered(
            lambda scale: (scale.variable_cost_id.id, scale.source_key)
            not in current_scale_keys,
        )
        if stale_scales:
            stale_scales.write({'active': False})
        if new_values:
            scale_model.create(new_values)

    def _midocean_variable_cost_key(self, source):
        """Build a stable API key for a variable-cost area."""
        return '|'.join([
            source.get('range_id') or '',
            str(self._parse_number(source.get('area_from'))),
            str(self._parse_number(source.get('area_to'))),
        ])

    def _midocean_price_scale_key(self, source):
        """Build a stable API key for a quantity tier within one cost area."""
        return str(self._parse_number(source.get('minimum_quantity')))
