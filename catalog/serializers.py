from rest_framework import serializers
from catalog.models import Product, Category, Brand

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'icon']

class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ['id', 'name', 'slug', 'country']

class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.ReadOnlyField(source='category.name')
    brand_name = serializers.ReadOnlyField(source='brand.name', default='')

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        # Закупочная цена — внутренняя информация магазина.  Не отдаём её
        # кассиру даже если он напрямую вызвал API поиска товара.
        if request is not None and not request.user.is_admin_user:
            fields.pop('purchase_price', None)
        return fields

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'sku', 'barcode', 'category', 'category_name',
            'brand', 'brand_name', 'purchase_price', 'retail_price',
            'unit', 'stock_qty', 'min_stock_alert', 'is_low_stock', 'is_active'
        ]
