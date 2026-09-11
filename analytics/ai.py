"""Read-only AI explanations for the analytics dashboard."""

import hashlib
import json
import os
from decimal import Decimal

from django.core.cache import cache
from groq import Groq


class AIUnavailable(Exception):
    """Raised when the configured AI provider cannot return an answer."""


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def build_analytics_context(report, start, end):
    """Keep the prompt small and expose only already-calculated business facts."""
    inventory = report['inventory']
    return _json_value({
        'period': {'from': start.isoformat(), 'to': end.isoformat()},
        'sales': {
            'checks': report['check_count'],
            'gross_revenue_kzt': report['gross_revenue'],
            'refunds_kzt': report['refund_amount'],
            'refund_operations': report['refund_count'],
            'refunds_from_period_checks': report['cohort_refund_count'],
            'refunds_from_older_checks': report['prior_period_refund_count'],
            'refund_rate_percent': report['refund_rate'],
            'net_revenue_kzt': report['net_revenue'],
            'discounts_kzt': report['discounts'],
            'average_check_kzt': report['average_check'],
            'net_units': report['net_units'],
            'gross_profit_kzt': report['gross_profit'],
            'margin_percent': report['margin'],
            'margin_available': report['margin_available'],
            'returns_exceed_sales': report['returns_exceed_sales'],
        },
        'payments': [
            {'method': row['name'], 'checks': row['checks'], 'amount_kzt': row['amount'], 'share_percent': row['share']}
            for row in report['payment_rows']
        ],
        'top_products': [
            {'name': row['product__name'] or 'Удалённый товар', 'units': row['total_qty'], 'revenue_kzt': row['total_sum'], 'profit_kzt': row['profit']}
            for row in report['top_products'][:8]
        ],
        'cashiers': [
            {'name': row['name'], 'checks': row['checks'], 'revenue_kzt': row['revenue'], 'profit_kzt': row['profit'], 'refunds': row['refunds']}
            for row in report['cashier_rows'][:8]
        ],
        'inventory': {
            'active_products': inventory['active_count'],
            'out_of_stock': inventory['out_of_stock_count'],
            'low_stock': inventory['low_stock_count'],
            'stock_cost_kzt': inventory['stock_cost'],
            'stock_retail_kzt': inventory['stock_retail'],
            'potential_profit_kzt': inventory['potential_profit'],
            'missing_cost': inventory['without_cost_count'],
            'missing_retail_price': inventory['without_price_count'],
            'missing_barcode': inventory['without_barcode_count'],
            'missing_category': inventory['without_category_count'],
        },
    })


def generate_insight(report, start, end, question):
    """Ask Groq to explain a prepared report; the model never receives DB access."""
    api_key = os.environ.get('GROQ_API_KEY', '').strip()
    if not api_key:
        raise AIUnavailable('GROQ_API_KEY не настроен')

    question = (question or 'Сделай краткий анализ показателей и дай приоритетные рекомендации.').strip()[:800]
    context = build_analytics_context(report, start, end)
    cache_payload = json.dumps({'question': question, 'context': context}, ensure_ascii=False, sort_keys=True)
    cache_key = 'dacar:ai-insight:' + hashlib.sha256(cache_payload.encode('utf-8')).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return cached

    system_prompt = (
        'Ты AI-аналитик магазина DACAR Market. Отвечай только по данным JSON, которые передал Django. '
        'Не выдумывай факты, товары, суммы и причины. Если данных недостаточно, так и скажи. '
        'Не выполняй SQL, не предлагай менять базу напрямую и не утверждай, что действие уже выполнено. '
        'Отвечай на русском, коротко и практично: сначала «Итог», затем «Что заметил», затем «Что сделать» (до 3 пунктов). '
        'Все суммы указывай в тенге. Не называй возвраты старыми или новыми, если это прямо не указано в JSON. '
        'Не используй Markdown-жирность или другие служебные маркеры: обычные заголовки и списки с дефисом достаточно.'
    )
    user_prompt = 'Вопрос пользователя:\n' + question + '\n\nПодготовленные данные Django (только чтение):\n' + json.dumps(context, ensure_ascii=False)

    try:
        client = Groq(api_key=api_key, timeout=25.0)
        completion = client.chat.completions.create(
            model=os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b'),
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.2,
            max_tokens=1000,
        )
        answer = (completion.choices[0].message.content or '').strip()
    except Exception as exc:  # provider errors should not break the dashboard
        raise AIUnavailable('Не удалось получить ответ AI') from exc

    if not answer:
        raise AIUnavailable('AI вернул пустой ответ')
    cache.set(cache_key, answer, 300)
    return answer
