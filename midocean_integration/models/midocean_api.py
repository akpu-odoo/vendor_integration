from odoo import models
from odoo.exceptions import UserError


class VendorApi(models.Model):
    _inherit = 'vendor.api'

    def _sync(self):
        if self.integration_type == 'midocean' and self.api_purpose == 'supplier_price' and self.res_model_id.model == 'vendor.product.price':
            self.res_model_id = self.env['ir.model']._get('product.supplierinfo').id
        cursor = self.sync_cursor
        records, payload = super()._sync()
        if self.integration_type == 'midocean':
            sources = self._response_records(payload)
            sources = sources[min(cursor, len(sources)):][:len(records)]
            getattr(self, '_sync_midocean_%s' % self.api_purpose, lambda *_: None)(records, sources)
        return records, payload

    def _before_create_records(self, sources):
        if self.integration_type != 'midocean':
            return super()._before_create_records(sources)
        if self.api_purpose == 'catalogue' and self.res_model_id.model == 'product.template':
            return {'attributes': self._midocean_attributes(sources)}
        if self.api_purpose == 'supplier_price' and self.res_model_id.model == 'product.supplierinfo':
            products = self.env['product.product'].search([('default_code', 'in', [source.get('sku') for source in sources])])
            return {'products': {product.default_code: product for product in products}}
        return {}

    def _create_values_from_json(self, source, prepared):
        values = super()._create_values_from_json(source, prepared)
        attributes = prepared.get('attributes') if prepared else None
        if attributes:
            values.update({'uom_id': self.env.ref('uom.product_uom_unit').id,
                           'attribute_line_ids': self._attribute_line_commands(source.get('variants', []), attributes)})
        return values

    def _prepare_record_values(self, source, values, record, prepared):
        values = super()._prepare_record_values(source, values, record, prepared)
        if self.integration_type != 'midocean' or self.api_purpose != 'supplier_price' or self.res_model_id.model != 'product.supplierinfo':
            return values
        product = prepared['products'].get(source.get('sku'))
        if not product:
            return False
        if not self.external_vendor_id.partner_id:
            raise UserError(self.env._('The vendor needs a contact before supplier prices can be created.'))
        values.update({'vendor_api_id': self.id, 'partner_id': self.external_vendor_id.partner_id.id,
                       'product_id': product.id, 'product_tmpl_id': product.product_tmpl_id.id,
                       'product_code': source.get('sku')})
        return values

    def _sync_midocean_catalogue(self, templates, sources):
        if templates._name != 'product.template':
            return
        unit = self.env.ref('uom.product_uom_unit')
        templates.filtered(lambda template: template.uom_id != unit).write({'uom_id': unit.id})
        attributes, assets = self._midocean_attributes(sources), []
        for template, source in zip(templates, sources):
            variants = source.get('variants', [])
            self._add_variant_values(template, variants, attributes)
            attribute_ids = {attribute.id for attribute, _values in attributes.values()}
            products = {frozenset(ptav.product_attribute_value_id.id for ptav in product.product_template_attribute_value_ids if ptav.attribute_id.id in attribute_ids): product for product in template.product_variant_ids}
            for variant in variants:
                key = frozenset(attributes[field][1][variant[field]].id for field in attributes if variant.get(field))
                product = products.get(key)
                if product:
                    values = {'default_code': variant.get('sku'), 'barcode': variant.get('gtin') or False}
                    if any(product[field] != value for field, value in values.items()):
                        product.write(values)
                    assets.extend((product, variant.get('sku'), item) for item in variant.get('digital_assets', []))
            assets.extend((template, source.get('master_id') or source.get('master_code'), item) for item in source.get('digital_assets', []))
        self._sync_assets(assets)

    def _midocean_attributes(self, sources):
        result = {}
        for field, name in {'color_code': 'Color Code', 'size_textile': 'Size'}.items():
            names = {variant.get(field) for source in sources for variant in source.get('variants', [])} - {False, None}
            if not names:
                continue
            attribute = self.env['product.attribute'].search([('name', '=', name)], limit=1)
            if not attribute:
                attribute = self.env['product.attribute'].create({'name': name, 'display_type': 'select', 'create_variant': 'always'})
            if attribute.create_variant != 'always':
                raise UserError(self.env._('%s must create variants instantly for MiDocean imports.', name))
            values = self.env['product.attribute.value'].search([('attribute_id', '=', attribute.id), ('name', 'in', list(names))])
            by_name = {value.name: value for value in values}
            missing = names - by_name.keys()
            if missing:
                by_name.update({value.name: value for value in self.env['product.attribute.value'].create([{'attribute_id': attribute.id, 'name': value} for value in missing])})
            result[field] = (attribute, by_name)
        return result

    def _add_variant_values(self, template, variants, attributes):
        new_lines, updates = self._attribute_line_commands(variants, attributes), []
        new_lines = [command for command in new_lines if not template.attribute_line_ids.filtered(lambda line: line.attribute_id.id == command[2]['attribute_id'])]
        for field, (attribute, values) in attributes.items():
            line = template.attribute_line_ids.filtered(lambda item: item.attribute_id == attribute)
            ids = {values[variant[field]].id for variant in variants if variant.get(field)}
            new_ids = ids - set(line.value_ids.ids)
            if line and new_ids:
                updates.append((line, new_ids))
        if new_lines:
            template.with_context(create_product_product=False).write({'attribute_line_ids': new_lines})
        for line, ids in updates:
            line.with_context(create_product_product=False).write({'value_ids': [(4, value_id) for value_id in ids]})
        if new_lines or updates:
            self._preserve_placeholder_variant(template, variants, attributes)
            template._create_variant_ids()

    def _attribute_line_commands(self, variants, attributes):
        return [(0, 0, {'attribute_id': attribute.id, 'value_ids': [(6, 0, list({values[item[field]].id for item in variants if item.get(field)}))]}) for field, (attribute, values) in attributes.items() if any(item.get(field) for item in variants)]

    def _preserve_placeholder_variant(self, template, variants, attributes):
        placeholder = template.with_context(active_test=False).product_variant_ids.filtered(lambda product: not product.product_template_attribute_value_ids)
        if len(placeholder) != 1 or not variants:
            return
        source = variants[0]
        ids = {attributes[field][1][source[field]].id for field in attributes if source.get(field)}
        combination = template.valid_product_template_attribute_line_ids.mapped('product_template_value_ids').filtered(lambda value: value.product_attribute_value_id.id in ids)
        if len(combination) == len(ids):
            placeholder.product_template_attribute_value_ids = [(6, 0, combination.ids)]

    def _sync_midocean_stock(self, records, sources):
        if records._name != 'vendor.product.stock':
            return
        products = self.env['product.product'].search([('default_code', 'in', [source.get('sku') for source in sources])])
        by_sku = {product.default_code: product for product in products}
        for record, source in zip(records, sources):
            product = by_sku.get(source.get('sku'))
            record.write({'vendor_api_id': self.id, 'product_id': product.id if product else False})

    def _sync_assets(self, assets):
        values = {}
        for owner, key, asset in assets:
            if asset.get('url') and key:
                asset_key = '%s:%s' % (key, asset['url'])
                values[asset_key] = {'vendor_api_id': self.id, 'asset_key': asset_key,
                    'name': asset.get('subtype') or asset.get('type') or asset['url'].rsplit('/', 1)[-1], 'url': asset['url'],
                    'highres_url': asset.get('url_highress'), 'asset_type': asset.get('type'), 'subtype': asset.get('subtype'),
                    'product_tmpl_id': owner.id if owner._name == 'product.template' else False,
                    'product_id': owner.id if owner._name == 'product.product' else False}
        if not values:
            return
        asset_model = self.env['vendor.product.asset']
        existing = asset_model.search([('vendor_api_id', '=', self.id), ('asset_key', 'in', list(values))])
        by_key = {asset.asset_key: asset for asset in existing}
        for key, value in values.items():
            if key in by_key:
                by_key[key].write(value)
        new_values = [value for key, value in values.items() if key not in by_key]
        if new_values:
            asset_model.create(new_values)
