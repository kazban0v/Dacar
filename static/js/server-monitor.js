(() => {
    'use strict';
    const root = document.getElementById('server-monitor');
    if (!root) return;
    const el = id => document.getElementById(id);
    const text = (id, value) => { el(id).textContent = value; };
    const number = v => typeof v === 'number' && Number.isFinite(v);
    const pct = v => number(v) ? `${v.toLocaleString('ru-RU', {maximumFractionDigits: 1})}%` : '—';
    function bytes(v) {
        if (!number(v)) return '—';
        if (v < 1024) return `${Math.round(v)} Б`;
        const units = ['КиБ', 'МиБ', 'ГиБ', 'ТиБ'];
        let i = -1;
        do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
        return `${v.toLocaleString('ru-RU', {maximumFractionDigits: 1})} ${units[i]}`;
    }
    let paused = false, pending = false, timer, controller, hours = 1, points = [], sample = null, lastHistory = 0;
    let seenAlerts = new Set(), alertsInitialized = false;
    const timeLabel = t => new Date(t * 1000).toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'});
    function bar(id, value) { el(id).style.width = `${number(value) ? Math.max(0, Math.min(100, value)) : 0}%`; }
    function draw(id, fields, colors, isNetwork) {
        const canvas = el(id), width = canvas.clientWidth, height = 210;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * ratio); canvas.height = height * ratio;
        const ctx = canvas.getContext('2d');
        ctx.scale(ratio, ratio);
        const style = getComputedStyle(root);
        const now = Date.now() / 1000, start = now - hours * 3600;
        const visible = points.filter(p => p.timestamp >= start && p.timestamp <= now + 2);
        const values = visible.flatMap(p => fields.map(f => p[f])).filter(number);
        const max = isNetwork ? Math.max(1024, ...values) * 1.15 : 100;
        const left = isNetwork ? 70 : 39, right = width - 8, top = 12, bottom = height - 26;
        const x = t => left + (t - start) / (now - start) * (right - left);
        const y = v => bottom - Math.max(0, Math.min(max, v)) / max * (bottom - top);
        ctx.font = '10px system-ui';
        for (let i = 0; i <= 4; i++) {
            const v = max * i / 4, pos = y(v);
            ctx.strokeStyle = style.getPropertyValue('--mon-line');
            ctx.beginPath(); ctx.moveTo(left, pos); ctx.lineTo(right, pos); ctx.stroke();
            ctx.fillStyle = style.getPropertyValue('--mon-muted'); ctx.textAlign = 'right';
            ctx.fillText(isNetwork ? bytes(v) : `${Math.round(v)}%`, left - 7, pos + 3);
        }
        for (let i = 0; i <= 3; i++) {
            ctx.textAlign = i === 0 ? 'left' : i === 3 ? 'right' : 'center';
            const t = start + (now - start) * i / 3;
            ctx.fillText(timeLabel(t), x(t), height - 5);
        }
        fields.forEach((field, index) => {
            ctx.strokeStyle = style.getPropertyValue(colors[index]); ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = 2;
            ctx.beginPath(); let previous = null;
            visible.forEach(p => {
                if (!number(p[field])) { previous = null; return; }
                if (!previous || p.timestamp - previous.timestamp > 150) ctx.moveTo(x(p.timestamp), y(p[field]));
                else ctx.lineTo(x(p.timestamp), y(p[field]));
                previous = p;
            });
            ctx.stroke();
            const latest = visible.filter(p => number(p[field])).at(-1);
            if (latest) { ctx.beginPath(); ctx.arc(x(latest.timestamp), y(latest[field]), 3, 0, Math.PI * 2); ctx.fill(); }
        });
        if (!values.length) {
            ctx.fillStyle = style.getPropertyValue('--mon-muted'); ctx.font = '12px system-ui'; ctx.textAlign = 'center';
            ctx.fillText('Ожидаем первые замеры', (left + right) / 2, height / 2);
        }
    }
    function charts() {
        draw('resources-chart', ['cpu_percent', 'memory_percent'], ['--mon-blue', '--mon-purple'], false);
        draw('network-chart', ['network_rx', 'network_tx'], ['--mon-blue', '--mon-teal'], true);
    }
    function notifyAlert(alert) {
        if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
        try { new Notification(`DACAR: ${alert.title}`, { body: alert.message, tag: `dacar-${alert.fingerprint}` }); } catch (_) {}
    }
    function render(s, serverAlerts) {
        const age = Math.max(0, Date.now() / 1000 - s.timestamp);
        text('machine-label', `${s.machine} · ${s.system} · ${s.cpu_count} логических CPU`);
        text('cpu-value', pct(s.cpu_percent)); bar('cpu-bar', s.cpu_percent);
        text('cpu-detail', number(s.cpu_percent) ? 'Загрузка всей машины между замерами' : 'Нужен следующий замер через 10 секунд');
        text('ram-value', pct(s.memory_percent)); bar('ram-bar', s.memory_percent);
        text('ram-detail', `Доступно ${bytes(s.memory_available)} из ${bytes(s.memory_total)}`);
        text('disk-value', bytes(s.disk_free)); bar('disk-bar', s.disk_percent);
        text('disk-detail', `Свободно из ${bytes(s.disk_total)} · занято ${pct(s.disk_percent)}`);
        text('swap-value', s.swap_total ? bytes(s.swap_used) : 'Нет swap'); bar('swap-bar', s.swap_percent);
        text('swap-detail', s.swap_total ? `Использовано из ${bytes(s.swap_total)}` : 'Подкачка не настроена на этой машине');
        text('app-status', 'Отвечает'); el('app-status').className = 'good';
        text('database-status', s.database_ok ? `Доступна · ${s.database_ms} мс` : 'Ошибка подключения');
        el('database-status').className = s.database_ok ? 'good' : 'bad';
        text('uptime-value', `${Math.floor(s.uptime / 86400)} д ${Math.floor(s.uptime % 86400 / 3600)} ч ${Math.floor(s.uptime % 3600 / 60)} мин`);
        text('updated-at', new Date(s.timestamp * 1000).toLocaleString('ru-RU'));
        text('resources-summary', `Последний замер: CPU ${pct(s.cpu_percent)} · RAM ${pct(s.memory_percent)}. Пропуски означают отсутствие замеров.`);
        text('network-summary', `Входящий: ${bytes(s.network_rx)}/с · исходящий: ${bytes(s.network_tx)}/с. Интерфейсы машины, кроме loopback.`);
        text('live-status', age > 30 ? 'Данные устарели' : 'Обновляется · 10 с');
        el('live-status').classList.toggle('is-stale', age > 30);
        const alerts = [];
        const incoming = Array.isArray(serverAlerts) ? serverAlerts : [];
        incoming.filter(alert => alert.active).forEach(alert => {
            alerts.push([alert.severity, `${alert.title}: ${alert.message}`]);
            if (alertsInitialized && !seenAlerts.has(alert.fingerprint)) notifyAlert(alert);
            seenAlerts.add(alert.fingerprint);
        });
        incoming.filter(alert => !alert.active).slice(0, 3).forEach(alert => {
            alerts.push(['resolved', `Восстановлено: ${alert.title}`]);
            seenAlerts.delete(alert.fingerprint);
        });
        if (age > 30) alerts.push(['warning', 'Сборщик не предоставил свежие данные. Показан последний доступный замер.']);
        if (!s.database_ok) alerts.push(['error', 'База данных не ответила на проверочный запрос.']);
        if (s.memory_available < 150 * 1024 * 1024) alerts.push(['warning', 'Доступно меньше 150 МиБ RAM. Проверьте, сохраняется ли нехватка памяти.']);
        if (s.cpu_percent >= 90) alerts.push(['warning', 'CPU выше 90% в последнем интервале. Краткий пик не обязательно означает проблему.']);
        if (s.disk_percent >= 80) alerts.push(['warning', `Диск заполнен на ${pct(s.disk_percent)}. Стоит проверить свободное место.`]);
        if (s.swap_total && s.swap_percent >= 50) alerts.push(['warning', 'Используется больше половины swap. Проверьте доступную RAM и отклик приложения.']);
        if (!alerts.length) alerts.push(['', 'По текущим порогам предупреждений нет.']);
        el('monitor-alerts').replaceChildren(...alerts.map(([kind, message]) => { const li = document.createElement('li'); li.className = kind; li.textContent = message; return li; }));
        alertsInitialized = true;
        text('monitor-notice', s.system === 'Darwin' ? 'Сейчас показан локальный Mac, не VPS dacar-market.kz. На сервере этот раздел будет измерять сервер.' : 'Измеряется машина, на которой запущен Django. Только чтение; данные продаж не изменяются.');
        charts();
    }
    function schedule() { clearTimeout(timer); if (!paused && !document.hidden) timer = setTimeout(load, 10000); }
    async function load() {
        if (paused || document.hidden || pending) return;
        pending = true; controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 8000);
        const includeHistory = !lastHistory || Date.now() - lastHistory >= 60000;
        try {
            const response = await fetch(root.dataset.endpoint + (includeHistory ? '?history=1' : ''), {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
            if (response.redirected || response.status === 403) {
                paused = true; text('pause-monitor', 'Продолжить');
                throw new Error('Сессия завершена или доступ запрещён. Войдите в админку заново.');
            }
            if (!response.ok) throw new Error('Метрики недоступны. Повторим запрос через 10 секунд.');
            const data = await response.json();
            if (!data.sample || !number(data.sample.timestamp)) throw new Error('Сервер вернул некорректные метрики.');
            sample = data.sample;
            if (data.history) { points = data.history; lastHistory = Date.now(); }
            points = points.filter(p => p.timestamp >= Date.now() / 1000 - 86400 && p.timestamp !== sample.timestamp);
            points.push(sample); points.sort((a, b) => a.timestamp - b.timestamp); points = points.slice(-1600);
            render(sample, data.alerts || []);
        } catch (error) {
            if (!document.hidden && !paused || error.name !== 'AbortError') {
                text('live-status', 'Нет свежих данных'); el('live-status').classList.add('is-stale');
                text('monitor-notice', error.name === 'AbortError' ? 'Ответ не получен за 8 секунд. Показаны прежние данные; повторим проверку.' : error.message);
                text('app-status', 'Нет свежего ответа'); el('app-status').className = 'bad';
            }
        } finally { clearTimeout(timeout); pending = false; schedule(); }
    }
    el('pause-monitor').addEventListener('click', () => {
        paused = !paused; clearTimeout(timer);
        text('pause-monitor', paused ? 'Продолжить' : 'Пауза');
        if (paused) { controller?.abort(); text('live-status', 'На паузе'); el('live-status').classList.add('is-stale'); }
        else load();
    });
    const notificationButton = el('enable-notifications');
    if (notificationButton && typeof Notification !== 'undefined' && Notification.permission !== 'granted') {
        notificationButton.hidden = false;
        notificationButton.addEventListener('click', async () => {
            const result = await Notification.requestPermission();
            notificationButton.textContent = result === 'granted' ? 'Уведомления включены' : 'Уведомления запрещены';
            notificationButton.disabled = result !== 'granted';
        });
    }
    document.addEventListener('visibilitychange', () => {
        clearTimeout(timer);
        if (document.hidden) controller?.abort(); else if (!paused) load();
    });
    root.querySelectorAll('[data-hours]').forEach(button => button.addEventListener('click', () => {
        hours = Number(button.dataset.hours);
        root.querySelectorAll('[data-hours]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
        charts();
    }));
    new ResizeObserver(charts).observe(el('resources-chart').parentElement);
    new MutationObserver(charts).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
    charts(); load();
})();
