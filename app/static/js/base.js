// ==================== BASE JS - Shared across all dashboards ====================

async function loadCurrentUser() {
    try {
        const response = await fetch('/api/current-user');
        
        if (!response.ok) {
            const storedUser = localStorage.getItem('currentUser');
            if (storedUser) {
                const user = JSON.parse(storedUser);
                displayUserProfile(user);
                return;
            }
            throw new Error('Could not load user data');
        }

        const userData = await response.json();
        displayUserProfile(userData);
        localStorage.setItem('currentUser', JSON.stringify(userData));
    } catch (error) {
        console.error('Error loading current user:', error);
        displayUserProfile({
            full_name: 'User',
            email: 'user@system.com',
            user_type: 'user'
        });
    }
}

function displayUserProfile(user) {
    const getInitials = (name) => {
        if (!name) return 'U';
        return name.split(' ')
            .map(n => n[0])
            .join('')
            .toUpperCase()
            .substring(0, 2);
    };

    const initials = getInitials(user.full_name);
    const fullName = user.full_name || 'User';
    const email = user.email || 'user@system.com';

    const avatarEl = document.getElementById('userAvatar');
    if (avatarEl) avatarEl.textContent = initials;

    const nameEl = document.getElementById('userName');
    if (nameEl) nameEl.textContent = fullName;

    const emailEl = document.getElementById('userEmail');
    if (emailEl) emailEl.textContent = email;
}

function setupMobileMenu() {
    const toggle = document.getElementById('mobileToggle');
    const sidebar = document.getElementById('sidebar');
    const overlay =
        document.getElementById('mobileOverlay') ||
        document.getElementById('sidebarOverlay');

    if (!toggle || !sidebar || !overlay) return;

    if (toggle.dataset.mobileMenuBound === 'true') return;
    toggle.dataset.mobileMenuBound = 'true';

    function closeSidebarInner() {
        sidebar.classList.remove('active');
        overlay.classList.remove('active');
        document.body.classList.remove('sidebar-open');
    }

    toggle.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();

        const isOpening = !sidebar.classList.contains('active');
        sidebar.classList.toggle('active');
        overlay.classList.toggle('active');

        if (isOpening) {
            document.body.classList.add('sidebar-open');
        } else {
            document.body.classList.remove('sidebar-open');
        }
    });

    overlay.addEventListener('click', closeSidebarInner);

    document.querySelectorAll('.menu-item').forEach(item => {
        item.addEventListener('click', closeSidebarInner);
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            closeSidebarInner();
        }
    });
}

function closeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay =
        document.getElementById('mobileOverlay') ||
        document.getElementById('sidebarOverlay');

    if (sidebar) sidebar.classList.remove('active');
    if (overlay) overlay.classList.remove('active');
    document.body.classList.remove('sidebar-open');
}

function showNotification(type, message) {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 16px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        z-index: 3000;
        animation: slideIn 0.3s ease-out;
        max-width: 400px;
        word-wrap: break-word;
        font-weight: 600;
        font-size: 14px;
    `;

    const colors = {
        success: { bg: 'rgba(16, 185, 129, 0.95)', text: 'white' },
        error: { bg: 'rgba(239, 68, 68, 0.95)', text: 'white' },
        warning: { bg: 'rgba(245, 158, 11, 0.95)', text: 'white' },
        info: { bg: 'rgba(59, 130, 246, 0.95)', text: 'white' }
    };

    const color = colors[type] || colors.success;
    notification.style.backgroundColor = color.bg;
    notification.style.color = color.text;
    notification.innerHTML = message;

    if (!document.querySelector('style[data-notification-animation]')) {
        const style = document.createElement('style');
        style.setAttribute('data-notification-animation', 'true');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(400px); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(400px); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
    }

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

function setupLogout() {
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', function() {
            window.location.href = '/logout';
        });
    }
}

// ===== INITIALIZATION =====
document.addEventListener('DOMContentLoaded', function() {
    setupMobileMenu();
    setupLogout();
    loadCurrentUser();
});
