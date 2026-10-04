from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from catalog.models import Product, Brand
from sales.document_forms import WeekForm, InvoiceSerializer
from sales.documents import weekly_brand_report, is_shine, create_invoice, SELLER
from sales.models import CompanyInvoice


@login_required
@never_cache
def shine_report(request):
    if not request.user.is_admin_user:
        return HttpResponseForbidden('Отчёт доступен только администратору.')
    today = timezone.localdate()
    current = today - timedelta(days=today.weekday())
    previous = current - timedelta(days=7)
    # При смене бренда браузер отправляет только brand. В этом случае используем
    # текущий понедельник, чтобы отчёт открывался сразу, а не становился невалидным.
    params = request.GET.copy()
    if not params.get('week'):
        params['week'] = current.isoformat()
    form = WeekForm(params)
    brands = Brand.objects.order_by('name')
    default_brand = brands.filter(name__iexact='Shine Systems').first() or brands.first()
    selected_brand_id = request.GET.get('brand', str(default_brand.pk) if default_brand else '')
    selected_brand = brands.filter(pk=selected_brand_id).first()
    # Получатель не вводится вручную: для отчёта Shine заранее задан Маршал.
    recipient = 'Маршал' if selected_brand and is_shine(selected_brand.name) else ''
    report = None
    if form.is_valid() and selected_brand:
        try:
            report = weekly_brand_report(form.cleaned_data['week'], selected_brand.name)
            report['recipient'] = recipient
        except ValueError as exc:
            form.add_error(None, str(exc))
    elif form.is_valid():
        form.add_error(None, 'Добавьте бренд в каталог, затем выберите его для отчёта.')
    if request.GET.get('format') == 'pdf' and report is not None:
        from sales.document_pdf import shine_pdf
        response = HttpResponse(shine_pdf(report), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="weekly-report-{report["start"]}.pdf"'
        return response
    return render(request, 'desktop/sales/shine_report.html', {
        'form': form, 'report': report, 'current_week': current, 'previous_week': previous,
        'brands': brands, 'selected_brand': selected_brand, 'recipient': recipient,
    },
                  status=200 if not form.errors else 400)


@login_required
@never_cache
def invoices(request):
    records = CompanyInvoice.objects.order_by('-pk')
    if not request.user.is_admin_user:
        records = records.filter(creator=request.user)
    return render(request, 'desktop/sales/invoices.html', {
        'seller': SELLER, 'invoices': records[:50],
        'products_data': list(Product.objects.filter(is_active=True).order_by('name').values(
            'id', 'name', 'sku', 'barcode', 'retail_price', 'unit')),
    })


class InvoiceCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = create_invoice(request.user, serializer.validated_data)
        return Response({'number': invoice.number, 'url': reverse('invoice_pdf', args=[invoice.pk])}, status=201)


@login_required
@never_cache
def invoice_download(request, pk):
    queryset = CompanyInvoice.objects.all()
    if not request.user.is_admin_user:
        queryset = queryset.filter(creator=request.user)
    invoice = get_object_or_404(queryset, pk=pk)
    from sales.document_pdf import invoice_pdf
    response = HttpResponse(invoice_pdf(invoice), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="invoice-{invoice.number}.pdf"'
    return response
