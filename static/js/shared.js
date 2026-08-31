// ===== TELEGRAM WEB APP INIT =====
const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

// ===== API URL =====
const API_URL = window.location.origin + "/api";

// ===== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЯ =====
let currentUser = null;

// ===== УНИВЕРСАЛЬНЫЙ API ЗАПРОС =====
async function apiFetch(endpoint, method = 'GET', body = null) {
    const headers = { 'Authorization': tg.initData || '' };
    if (body) headers['Content-Type'] = 'application/json';

    const options = { method, headers };
    if (body) options.body = JSON.stringify(body);

    const res = await fetch(`${API_URL}${endpoint}`, options);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Ошибка сервера' }));
        throw new Error(err.detail || 'Ошибка запроса');
    }
    return await res.json();
}

// ===== ЗАГРУЗКА ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ =====
async function loadUserProfile() {
    try {
        currentUser = await apiFetch('/user/profile');
        renderHeader();
        return currentUser;
    } catch (e) {
        console.error("Ошибка загрузки профиля:", e);
        return null;
    }
}

// ===== РЕНДЕР ШАПКИ =====
function renderHeader() {
    if (!currentUser) return;

    const nameEl = document.getElementById('headerName');
    const avatarEl = document.getElementById('headerAvatar');
    const balanceEl = document.getElementById('headerBalance');
    const roleEl = document.getElementById('headerRole');

    if (nameEl) nameEl.textContent = currentUser.first_name || 'Исполнитель';
    if (avatarEl) avatarEl.textContent = (currentUser.first_name || '?').charAt(0).toUpperCase();
    if (balanceEl) balanceEl.textContent = currentUser.balance.toLocaleString();

    if (roleEl) {
        if (currentUser.is_pro) {
            roleEl.innerHTML = '<span style="color: #f59e0b; font-weight: 700;">👑 PRO VIP</span>';
            roleEl.classList.add('pro');
        } else {
            roleEl.textContent = 'Исполнитель';
        }
    }
}

// ===== НАВИГАЦИЯ =====
function navigateTo(page) {
    if (tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
    window.location.href = page;
}

// ===== ПОДСВЕТКА АКТИВНОГО ПУНКТА НАВБАРА =====
function highlightActiveNav() {
    const path = window.location.pathname;
    const navItems = document.querySelectorAll('.nav-item');

    navItems.forEach(item => {
        const href = item.getAttribute('data-href');
        if (!href) return;

        item.classList.remove('active');

        // Точное совпадение или совпадение с "/" для главной
        if (path === href || (href === '/' && path === '/') || (href !== '/' && path.startsWith(href))) {
            item.classList.add('active');
        }
    });
}

// ===== ГЕНЕРАЦИЯ HTML ШАПКИ =====
function getHeaderHTML() {
    return `
    <div class="app-header">
        <div class="header-user">
            <div class="header-avatar" id="headerAvatar">?</div>
            <div>
                <div class="header-name" id="headerName">Загрузка...</div>
                <div class="header-role" id="headerRole">Исполнитель</div>
            </div>
        </div>
        <div class="balance-pill">
            <svg viewBox="0 0 24 24"><path d="M12 2l2.4 7.4h7.6l-6.2 4.5 2.4 7.4-6.2-4.5-6.2 4.5 2.4-7.4-6.2-4.5h7.6z"/></svg>
            <span id="headerBalance">0</span>
        </div>
    </div>`;
}

// ===== ГЕНЕРАЦИЯ HTML НАВБАРА =====
function getNavBarHTML() {
    return `
    <div class="nav-bar">
        <a class="nav-item" data-href="/" onclick="navigateTo('/'); return false;">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
            <span>Задания</span>
        </a>
        <a class="nav-item" data-href="/leaderboard" onclick="navigateTo('/leaderboard'); return false;">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>
            <span>Рейтинг</span>
        </a>
        <a class="nav-item pro-tab" data-href="/pro" onclick="navigateTo('/pro'); return false;">
            <svg fill="currentColor" viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
            <span>PRO</span>
        </a>
        <a class="nav-item" data-href="/wallet" onclick="navigateTo('/wallet'); return false;">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M7 15h1m4 0h1m-7 4h12a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
            <span>Кошелек</span>
        </a>
        <a class="nav-item" data-href="/profile" onclick="navigateTo('/profile'); return false;">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
            <span>Профиль</span>
        </a>
    </div>`;
}

// ===== SKELETON ЗАГРУЗКА =====
function showSkeleton(containerId, count = 3) {
    const container = document.getElementById(containerId);
    if (!container) return;
    let html = '';
    for (let i = 0; i < count; i++) {
        html += '<div class="skeleton skeleton-card"></div>';
    }
    container.innerHTML = html;
}

// ===== HAPTIC FEEDBACK =====
function hapticLight() {
    if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
}

function hapticMedium() {
    if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
}

function hapticSuccess() {
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
}

function hapticError() {
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
}

// ===== ИНИЦИАЛИЗАЦИЯ СТРАНИЦЫ =====
document.addEventListener('DOMContentLoaded', async () => {
    // Вставляем шапку и навбар
    const headerSlot = document.getElementById('header-slot');
    const navSlot = document.getElementById('nav-slot');

    if (headerSlot) headerSlot.innerHTML = getHeaderHTML();
    if (navSlot) navSlot.innerHTML = getNavBarHTML();

    // Подсвечиваем активную вкладку
    highlightActiveNav();

    // Загружаем профиль
    await loadUserProfile();

    // Вызываем инициализацию страницы, если определена
    if (typeof onPageInit === 'function') {
        onPageInit();
    }
});
