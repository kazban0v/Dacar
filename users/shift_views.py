"""Cash shift management: open, close, and query the current shift."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum, Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.models import AuditLog
from catalog.templatetags.dacar_format import format_tenge
from sales.models import SaleOrder, SalePayment
from users.models import CashShift


class ShiftOpenAPIView(APIView):
    """POST /api/shifts/open/ — open a new cash shift for the authenticated cashier."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # Already has an open shift?
        existing = CashShift.objects.filter(cashier=request.user, status=CashShift.Status.OPEN).first()
        if existing:
            return Response({
                'success': False,
                'error': 'У вас уже открыта смена. Закройте текущую перед открытием новой.',
                'shift_id': existing.pk,
            }, status=status.HTTP_409_CONFLICT)

        try:
            opening_cash = Decimal(str(request.data.get('opening_cash', '0') or '0')).quantize(Decimal('0.01'))
        except Exception:
            return Response({'success': False, 'error': 'Укажите корректную сумму размена.'}, status=status.HTTP_400_BAD_REQUEST)
        if opening_cash < 0:
            return Response({'success': False, 'error': 'Размен не может быть отрицательным.'}, status=status.HTTP_400_BAD_REQUEST)
        opening_note = str(request.data.get('opening_note', '')).strip()[:255]

        try:
            with transaction.atomic():
                shift = CashShift.objects.create(
                    cashier=request.user,
                    opening_cash=opening_cash,
                    opening_note=opening_note,
                )
        except IntegrityError:
            # The database constraint also protects two double-clicked requests.
            existing = CashShift.objects.filter(cashier=request.user, status=CashShift.Status.OPEN).first()
            return Response({
                'success': False,
                'error': 'У вас уже открыта смена. Закройте текущую перед открытием новой.',
                'shift_id': existing.pk if existing else None,
            }, status=status.HTTP_409_CONFLICT)

        AuditLog.log(
            request, AuditLog.ActionType.USER_ACTION,
            f'Открыта кассовая смена #{shift.pk}. Размен: {format_tenge(opening_cash)} ₸.',
            metadata={
                'action': 'shift_open',
                'shift_id': shift.pk,
                'opening_cash': format(opening_cash, 'f').rstrip('0').rstrip('.') or '0',
            },
        )

        return Response({
            'success': True,
            'message': f'Смена #{shift.pk} открыта.',
            'shift': _serialize_shift(shift),
        }, status=status.HTTP_201_CREATED)


class ShiftCloseAPIView(APIView):
    """POST /api/shifts/<id>/close/ — close the specified cash shift."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            shift = CashShift.objects.get(pk=pk, cashier=request.user)
        except CashShift.DoesNotExist:
            return Response({'success': False, 'error': 'Смена не найдена.'}, status=status.HTTP_404_NOT_FOUND)

        if shift.status == CashShift.Status.CLOSED:
            return Response({
                'success': False,
                'error': 'Смена уже закрыта.',
            }, status=status.HTTP_409_CONFLICT)

        counted_cash_raw = request.data.get('counted_cash')
        try:
            counted_cash = Decimal(str(counted_cash_raw or '0')).quantize(Decimal('0.01'))
        except Exception:
            return Response({'success': False, 'error': 'Укажите корректную сумму наличных.'}, status=status.HTTP_400_BAD_REQUEST)
        if counted_cash < 0:
            return Response({'success': False, 'error': 'Сумма наличных не может быть отрицательной.'}, status=status.HTTP_400_BAD_REQUEST)
        closing_note = str(request.data.get('closing_note', '')).strip()[:255]

        # Compute shift totals before closing
        totals = _compute_shift_totals(shift)

        shift.status = CashShift.Status.CLOSED
        shift.closed_at = timezone.now()
        shift.counted_cash = counted_cash
        shift.closing_note = closing_note
        shift.save(update_fields=['status', 'closed_at', 'counted_cash', 'closing_note'])

        expected_cash = shift.opening_cash + totals['cash_revenue'] - totals['cash_refunds']
        difference = counted_cash - expected_cash

        AuditLog.log(
            request, AuditLog.ActionType.USER_ACTION,
            f'Закрыта кассовая смена #{shift.pk}. '
            f'Чеков: {totals["check_count"]}, выручка: {format_tenge(totals["total_revenue"])} ₸, '
            f'возвратов: {totals["refund_count"]}. '
            f'Наличных в кассе: {format_tenge(counted_cash)} ₸ '
            f'(ожидалось {format_tenge(expected_cash)} ₸, разница {format_tenge(difference)} ₸).',
            metadata={
                'action': 'shift_close',
                'shift_id': shift.pk,
                'totals': {k: str(v) for k, v in totals.items()},
                'counted_cash': str(counted_cash),
                'expected_cash': str(expected_cash),
                'difference': str(difference),
            },
        )

        return Response({
            'success': True,
            'message': f'Смена #{shift.pk} закрыта.',
            'shift': _serialize_shift(shift),
            'totals': {k: str(v) for k, v in totals.items()},
            'expected_cash': str(expected_cash),
            'difference': str(difference),
        })


class ShiftCurrentAPIView(APIView):
    """GET /api/shifts/current/ — get the current open shift with live totals."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        shift = CashShift.objects.filter(cashier=request.user, status=CashShift.Status.OPEN).first()
        if not shift:
            return Response({'success': True, 'shift': None})

        totals = _compute_shift_totals(shift)
        return Response({
            'success': True,
            'shift': _serialize_shift(shift),
            'totals': {k: str(v) for k, v in totals.items()},
            'expected_cash': str(totals['expected_cash']),
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_shift_totals(shift):
    """Aggregate live financial totals for a shift."""
    completed = SaleOrder.objects.filter(shift=shift, status=SaleOrder.Status.COMPLETED)
    refunded = SaleOrder.objects.filter(
        Q(refund_shift=shift) | Q(shift=shift, status=SaleOrder.Status.REFUNDED)
    ).distinct()

    completed_agg = completed.aggregate(
        check_count=Count('id'),
        total_revenue=Sum('total_amount'),
    )
    refunded_agg = refunded.aggregate(
        refund_count=Count('id'),
        refund_amount=Sum('total_amount'),
    )

    # Payment method breakdown (completed only)
    payment_breakdown = {}
    for row in SalePayment.objects.filter(
        order__shift=shift,
        order__status=SaleOrder.Status.COMPLETED,
    ).values('method').annotate(total=Sum('amount')):
        payment_breakdown[row['method']] = row['total'] or Decimal('0')

    # Fallback for completed orders that don't have SalePayment rows
    orders_without_parts = completed.filter(payments__isnull=True)
    for ord_obj in orders_without_parts:
        if ord_obj.payment_method in ('CASH', 'CARD', 'TRANSFER'):
            payment_breakdown[ord_obj.payment_method] = (
                payment_breakdown.get(ord_obj.payment_method, Decimal('0')) + ord_obj.total_amount
            )

    cash_revenue = payment_breakdown.get('CASH', Decimal('0'))

    # Cash refunds (all orders refunded in this shift)
    cash_refunds = Decimal('0')
    for ro in refunded:
        if ro.payments.filter(method='CASH').exists():
            ro_cash = ro.payments.filter(method='CASH').aggregate(total=Sum('amount'))['total']
            cash_refunds += (ro_cash or Decimal('0'))
        elif ro.payment_method == SaleOrder.PaymentMethod.CASH:
            cash_refunds += ro.total_amount

    expected_cash = shift.opening_cash + cash_revenue - cash_refunds

    return {
        'check_count': completed_agg['check_count'] or 0,
        'total_revenue': completed_agg['total_revenue'] or Decimal('0'),
        'refund_count': refunded_agg['refund_count'] or 0,
        'refund_amount': refunded_agg['refund_amount'] or Decimal('0'),
        'cash_revenue': cash_revenue,
        'card_revenue': payment_breakdown.get('CARD', Decimal('0')),
        'transfer_revenue': payment_breakdown.get('TRANSFER', Decimal('0')),
        'cash_refunds': cash_refunds,
        'expected_cash': expected_cash,
    }


def _serialize_shift(shift):
    return {
        'id': shift.pk,
        'cashier': shift.cashier_id,
        'cashier_name': shift.cashier.get_full_name() or shift.cashier.username,
        'status': shift.status,
        'opened_at': timezone.localtime(shift.opened_at).isoformat(),
        'closed_at': timezone.localtime(shift.closed_at).isoformat() if shift.closed_at else None,
        'opening_cash': str(shift.opening_cash),
        'counted_cash': str(shift.counted_cash) if shift.counted_cash is not None else None,
        'opening_note': shift.opening_note,
        'closing_note': shift.closing_note,
    }
