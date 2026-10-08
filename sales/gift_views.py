import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from config.rendering import render
from sales.gift_cards import activate, card_summary, find_card
from sales.models import GiftCard, GiftCardEvent


def _error_text(exc):
    detail = exc.detail
    if isinstance(detail, (list, tuple)):
        return str(detail[0]) if detail else 'Ошибка проверки сертификата.'
    if isinstance(detail, dict):
        return str(next(iter(detail.values()), 'Ошибка проверки сертификата.'))
    return str(detail)


@login_required
def gift_cards_page(request):
    if request.method == 'POST':
        if request.POST.get('payment_received') != 'yes':
            messages.error(request, 'Подтвердите получение оплаты за сертификат.')
        else:
            try:
                card = activate(request, request.POST.get('code'), request.POST.get('payment_method'))
            except APIException as exc:
                messages.error(request, _error_text(exc))
            else:
                messages.success(request, f'Сертификат ••••{card.code_last4} активирован на 45 дней.')
                return redirect('gift_cards')

    cards = GiftCard.objects.all()
    return render(request, 'sales/gift_cards.html', {
        'cards_total': cards.count(),
        'cards_unissued': cards.filter(activated_at__isnull=True, is_blocked=False).count(),
        'cards_active': sum(card.state == 'ACTIVE' for card in cards.filter(activated_at__isnull=False)),
        'recent_events': GiftCardEvent.objects.select_related('card', 'actor', 'order')[:12],
    })


@login_required
@require_POST
def gift_card_lookup(request):
    try:
        payload = json.loads(request.body)
        card = find_card(payload.get('code'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Введите штрихкод сертификата.'}, status=400)
    except APIException as exc:
        return JsonResponse({'error': _error_text(exc)}, status=400)
    return JsonResponse(card_summary(card))
