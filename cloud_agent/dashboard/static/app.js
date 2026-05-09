/**
 * Cloud Agentic AI — Landing Page JavaScript
 *
 * Handles:
 *  1. Scroll-triggered reveal animations (Framer Motion-style)
 *  2. Navbar scroll intelligence (show/hide)
 *  3. Counter number animations
 *  4. Agent status check (pings the FastAPI backend)
 *  5. Smooth scrolling
 */

// ============================================================
// 1. SCROLL REVEAL (replaces Framer Motion for pure HTML page)
// ============================================================

const revealObserver = new IntersectionObserver(
    (entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                // Unobserve after animation (fire once)
                revealObserver.unobserve(entry.target);
            }
        });
    },
    { threshold: 0.12, rootMargin: '0px 0px -60px 0px' }
);

// Observe all reveal elements after DOM loads
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.reveal, .reveal-left, .reveal-right, .reveal-slow')
        .forEach(el => revealObserver.observe(el));

    // Immediately reveal hero (above fold)
    document.querySelectorAll('.hero .reveal, .hero .reveal-slow')
        .forEach(el => el.classList.add('revealed'));

    initCounters();
    initNavbarScroll();
    initAgentStatus();
    initSmoothScroll();
});

// ============================================================
// 2. NAVBAR SCROLL
// ============================================================

function initNavbarScroll() {
    const navbar = document.getElementById('navbar');
    let lastY = 0;
    window.addEventListener('scroll', () => {
        const y = window.scrollY;
        if (y > 80) {
            navbar.style.opacity = y > lastY && y > 300 ? '0' : '1';
        } else {
            navbar.style.opacity = '1';
        }
        lastY = y;
    }, { passive: true });
}

// ============================================================
// 3. COUNTER ANIMATION
// ============================================================

function initCounters() {
    const counters = document.querySelectorAll('.stat-num[data-target]');
    const counterObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                animateCounter(entry.target);
                counterObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.5 });

    counters.forEach(c => counterObserver.observe(c));
}

function animateCounter(el) {
    const target = parseInt(el.dataset.target, 10);
    const duration = 1500;
    const start = performance.now();
    const update = (now) => {
        const progress = Math.min((now - start) / duration, 1);
        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.round(eased * target);
        if (progress < 1) requestAnimationFrame(update);
    };
    requestAnimationFrame(update);
}

// ============================================================
// 4. AGENT STATUS PING
// ============================================================

function initAgentStatus() {
    const dot = document.querySelector('.ws-dot');
    const label = document.getElementById('ws-label');
    if (!dot || !label) return;

    async function checkStatus() {
        try {
            const resp = await fetch('/api/status', { signal: AbortSignal.timeout(2000) });
            if (resp.ok) {
                const data = await resp.json();
                dot.className = 'ws-dot connected';
                const cycles = data.cycle_count || 0;
                label.textContent = `Agent live · ${cycles} cycle${cycles !== 1 ? 's' : ''} run`;
            } else {
                throw new Error('not ok');
            }
        } catch {
            dot.className = 'ws-dot disconnected';
            label.textContent = 'Agent offline — run --dashboard';
        }
    }

    checkStatus();
    setInterval(checkStatus, 10000);
}

// ============================================================
// 5. SMOOTH SCROLL FOR ANCHOR LINKS
// ============================================================

function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(link => {
        link.addEventListener('click', e => {
            const target = document.querySelector(link.getAttribute('href'));
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });
}
