# MiDocean Integration

This add-on extends the generic `vendor_integration` importer. It does not add
a custom variant model: MiDocean catalogue records create `product.template`
records, while each item in `variants` becomes a normal Odoo `product.product`
through native **Color Code** and **Size** product attributes.

Install with demo data to create a MiDocean vendor and three API
configurations with automatic sync disabled. Replace `/products`, `/stock`, `/prices`, and the demo
authentication connection with MiDocean's real credentials and endpoints.

| API purpose | Target model | External key |
| --- | --- | --- |
| Catalogue | `product.template` | `master_id` |
| Stock | `vendor.product.stock` | `sku` |
| Supplier Price | `product.supplierinfo` | `sku` |

The supplier-price importer resolves the variant by SKU and injects the External
Vendor's `res.partner` into `product.supplierinfo`; map only price and validity
dates in the UI. Add catalogue field
mappings in the UI as needed, for example `product_name` → `name`,
`long_description` → `description_sale`, and weight/dimension fields supported
by your installed Odoo modules.

Digital assets are stored as URLs, including the high-resolution image URL, so
catalogue synchronization does not download files or slow down imports.
