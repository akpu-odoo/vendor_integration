# MiDocean Integration

This add-on extends the generic `vendor_integration` importer. It does not add
a custom variant model: MiDocean catalogue records create `product.template`
records, while each item in `variants` becomes a normal Odoo `product.product`
through native **Color Code** and **Size** product attributes.

Install with demo data to create a MiDocean vendor and five API
configurations with automatic sync disabled. Replace `/products`, `/stock`, `/prices`, and the demo
authentication connection with MiDocean's real credentials and endpoints.

| API purpose | Target model | External key |
| --- | --- | --- |
| Catalogue | `product.template` | `master_id` |
| Stock | `vendor.product.stock` | `sku` |
| Supplier Price | `product.supplierinfo` | `sku` |
| Print Data | `midocean.print.product` | `master_code` |
| Print Pricelist | `midocean.print.pricelist` | `id` |

The supplier-price importer resolves the variant by SKU and injects the External
Vendor's `res.partner` into `product.supplierinfo`; map only price and validity
dates in the UI. Add catalogue field
mappings in the UI as needed, for example `product_name` → `name`,
`long_description` → `description_sale`, and weight/dimension fields supported
by your installed Odoo modules.

Digital assets are stored as URLs, including the high-resolution image URL, so
catalogue synchronization does not download files or slow down imports.

## Printing data and pricing

Two additional API purposes are available in **Vendor APIs**:

- **Print Data** (`/gateway/printdata/1.0`) imports Dutch technique names,
  printable-product data, print positions, allowed techniques, points, and
  position images.
- **Print Pricelist** (`/gateway/printpricelist/2.0`) imports currency,
  validity dates, manipulation charges, technique setup charges, variable-cost
  areas, and every quantity scale.

Set **Sync Enabled** and **Sync Every (Hours)** independently on both APIs.
The scheduler evaluates each endpoint separately, so these intervals do not
need to match catalogue, stock, or supplier-price intervals.

A MiDocean purchase-order line for a printable product exposes **Print
Position**, **Print Technique**, and **Printing Cost**. The technique selector
is limited to the product and, when selected, its print position. This phase
stores the cost as a manual placeholder only; it does not change purchase-order
totals or send printing data to MiDocean. Those belong to the later calculation
and print-order submission phases.
