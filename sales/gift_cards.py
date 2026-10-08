"""Gift-card activation, verification and redemption rules."""
import hashlib
import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from analytics.models import AuditLog
from sales.models import GiftCard, GiftCardEvent
from sales.operations import financial_transaction


CODE_PATTERN = re.compile(r'^DKR-[0-9A-F]{5}-[0-9A-F]{5}$')
VALID_ISSUE_METHODS = {'CASH', 'CARD', 'TRANSFER'}


def normalize_code(raw):
    code = str(raw or '').strip().upper()
    if not CODE_PATTERN.fullmatch(code):
        raise ValidationError('Неверный формат штрихкода сертификата.')
    return code


def code_digest(raw):
    return hashlib.sha256(normalize_code(raw).encode('ascii')).hexdigest()


def find_card(raw):
    card = GiftCard.objects.filter(code_hash=code_digest(raw)).first()
    if card is None:
        raise ValidationError('Сертификат не найден в реестре.')
    return card


def card_summary(card):
    state = card.state
    events = list(card.events.select_related('order', 'actor')[:12])
    return {
        'last4': card.code_last4,
        'nominal': str(card.nominal),
        'balance': str(card.balance),
        'state': state,
        'state_label': {
            'UNISSUED': 'Не активирован', 'ACTIVE': 'Можно использовать',
            'SPENT': 'Использован', 'EXPIRED': 'Срок истёк', 'BLOCKED': 'Заблокирован',
        }[state],
        'activated_at': timezone.localtime(card.activated_at).strftime('%d.%m.%Y %H:%M') if card.activated_at else None,
        'expires_at': timezone.localtime(card.expires_at).strftime('%d.%m.%Y %H:%M') if card.expires_at else None,
        'history': [
            {'kind': event.get_kind_display(), 'amount': str(event.amount),
             'balance_after': str(event.balance_after),
             'order_number': event.order.order_number if event.order else None,
             'created_at': timezone.localtime(event.created_at).strftime('%d.%m.%Y %H:%M')}
            for event in events
        ],
    }


def activate(request, raw_code, payment_method):
    if payment_method not in VALID_ISSUE_METHODS:
        raise ValidationError('Выберите способ оплаты сертификата.')
    with financial_transaction():
        card = find_card(raw_code)
        if card.state != 'UNISSUED':
            raise ValidationError('Сертификат уже активирован или заблокирован.')
        now = timezone.now()
        card.balance = card.nominal
        card.activated_at = now
        card.expires_at = now + timedelta(days=45)
        card.activated_by = request.user
        card.activation_payment_method = payment_method
        card.save(update_fields=['balance', 'activated_at', 'expires_at', 'activated_by', 'activation_payment_method'])
        GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.Kind.ACTIVATE,
            amount=card.nominal, balance_after=card.nominal, actor=request.user)
        AuditLog.log(request, AuditLog.ActionType.USER_ACTION,
            f'Активирован подарочный сертификат ••••{card.code_last4} на {card.nominal} ₸.',
            metadata={'action': 'gift_card_activate', 'card_id': card.pk, 'nominal': str(card.nominal)})
        return card


def validate_redemption(card, amount):
    if card.state != 'ACTIVE':
        raise ValidationError('Сертификат недоступен: не активирован, истёк или использован.')
    if amount <= 0 or amount > card.balance:
        raise ValidationError('Сумма сертификата превышает доступный остаток.')


def redeem(card, amount, order, actor):
    """Call inside the same financial transaction as the checkout."""
    validate_redemption(card, amount)
    updated = GiftCard.objects.filter(pk=card.pk, balance__gte=amount, is_blocked=False).update(
        balance=F('balance') - amount)
    if not updated:
        raise ValidationError('Остаток сертификата изменился. Проверьте карту ещё раз.')
    GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.Kind.REDEEM,
        amount=amount, balance_after=card.balance - amount, order=order, actor=actor)


def restore_for_refund(order, actor):
    """Call inside the original order's atomic refund; repeated refunds are guarded there."""
    event = GiftCardEvent.objects.filter(order=order, kind=GiftCardEvent.Kind.REDEEM).select_related('card').first()
    if event is None:
        return
    card = event.card
    if card.balance + event.amount > card.nominal:
        raise ValidationError('Невозможно вернуть сумму на сертификат: превышен номинал.')
    GiftCard.objects.filter(pk=card.pk).update(balance=F('balance') + event.amount)
    GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.Kind.REFUND,
        amount=event.amount, balance_after=card.balance + event.amount, order=order, actor=actor)
