import uuid
from decimal import Decimal
from django import forms
from rest_framework import serializers
from sales.serializers import SaleOrderItemCreateSerializer


class WeekForm(forms.Form):
    week = forms.DateField(label='Понедельник начала недели', widget=forms.DateInput(attrs={'type': 'date'}))

    def clean_week(self):
        day = self.cleaned_data['week']
        if day.weekday() != 0:
            raise forms.ValidationError('Выберите понедельник. Отчёт охватывает следующие 7 дней.')
        return day


class InvoiceSerializer(serializers.Serializer):
    key = serializers.UUIDField(default=uuid.uuid4)
    buyer_name = serializers.CharField(max_length=255)
    buyer_bin = serializers.RegexField(r'^[0-9]{12}$')
    buyer_address = serializers.CharField(max_length=500)
    contract = serializers.CharField(max_length=255, allow_blank=True, default='')
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2,
                                               min_value=0, default=Decimal('0'))
    items = SaleOrderItemCreateSerializer(many=True, max_length=200, allow_empty=False)

    def validate_items(self, items):
        if len({item['product_id'] for item in items}) != len(items):
            raise serializers.ValidationError('Объедините одинаковые товары в одну строку.')
        return items
