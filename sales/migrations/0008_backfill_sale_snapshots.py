from django.db import migrations


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Item = apps.get_model('sales', 'SaleOrderItem')
    Payment = apps.get_model('sales', 'SalePayment')
    Order = apps.get_model('sales', 'SaleOrder')
    for item in Item.objects.using(alias).select_related('product__brand').iterator(chunk_size=500):
        product = item.product
        if product:
            Item.objects.using(alias).filter(pk=item.pk).update(
                product_name_snapshot=product.name, sku_snapshot=product.sku,
                unit_snapshot=product.unit,
                brand_name_snapshot=product.brand.name if product.brand else '')
    # Legacy mixed amounts cannot be reconstructed: leave them explicitly unknown.
    for order in Order.objects.using(alias).filter(
            payment_method__in=['CASH', 'CARD', 'TRANSFER'], total_amount__gte=0).iterator(chunk_size=500):
        Payment.objects.using(alias).get_or_create(order_id=order.pk, method=order.payment_method,
                                                  defaults={'amount': order.total_amount})


class Migration(migrations.Migration):
    dependencies = [('sales', '0007_saleorderitem_brand_name_snapshot_and_more')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
