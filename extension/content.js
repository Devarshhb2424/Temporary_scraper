/**
 * Campsite Finder Auto-Cart Assistant - Content Script
 * Runs on Recreation.gov to automatically select dates and lock campsite into cart.
 */

(async function () {
    // 1. Check if URL contains #autocart=START,END or query param
    let startDate = null;
    let endDate = null;

    const hashMatch = window.location.hash.match(/autocart=([\d-]+),([\d-]+)/);
    if (hashMatch) {
        startDate = hashMatch[1];
        endDate = hashMatch[2];
    } else {
        const p = new URLSearchParams(window.location.search);
        if (p.has('autocart')) {
            const parts = p.get('autocart').split(',');
            startDate = parts[0];
            endDate = parts[1] || parts[0];
        }
    }

    if (!startDate || !endDate) {
        // Normal browsing, don't interfere
        return;
    }

    console.log(`[Campsite Finder] Auto-Cart triggered for ${startDate} to ${endDate}`);

    // Create a stylish floating HUD notification
    function createBanner() {
        let b = document.getElementById('cf-autocart-hud');
        if (!b) {
            b = document.createElement('div');
            b.id = 'cf-autocart-hud';
            b.style.cssText = `
                position: fixed;
                top: 24px;
                right: 24px;
                z-index: 9999999;
                background: #0f172a;
                color: #f8fafc;
                padding: 16px 22px;
                border-radius: 12px;
                box-shadow: 0 20px 45px rgba(0,0,0,0.5);
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                font-size: 14px;
                border: 2px solid #10b981;
                max-width: 380px;
                line-height: 1.5;
                transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            `;
            document.body.appendChild(b);
        }
        return b;
    }

    function updateHUD(title, desc, status = 'loading') {
        const b = createBanner();
        const icon = status === 'success' ? '🎉' : (status === 'error' ? '⚠️' : '⚡');
        const color = status === 'success' ? '#10b981' : (status === 'error' ? '#ef4444' : '#38bdf8');
        b.style.borderColor = color;
        b.innerHTML = `
            <div style="display:flex; align-items:center; gap:10px; font-weight:700; font-size:15px; color:${color}; margin-bottom:6px;">
                <span>${icon}</span>
                <span>${title}</span>
            </div>
            <div style="font-size:13px; color:#e2e8f0;">${desc}</div>
        `;
    }

    updateHUD(
        'Auto-Cart Assistant Active',
        `Targeting dates: <b>${startDate}</b> to <b>${endDate}</b>.<br>Opening availability calendar...`
    );

    // Helper: wait for selector with timeout
    function waitForSelector(selector, timeoutMs = 10000) {
        return new Promise((resolve) => {
            const start = Date.now();
            const timer = setInterval(() => {
                const el = document.querySelector(selector);
                if (el) {
                    clearInterval(timer);
                    resolve(el);
                } else if (Date.now() - start > timeoutMs) {
                    clearInterval(timer);
                    resolve(null);
                }
            }, 250);
        });
    }

    // 2. Click 'Enter Dates' or reveal calendar
    const enterBtn = document.querySelector('#add-cart-campsite, button.campsite-page-book-now-button-tracker');
    if (enterBtn && enterBtn.innerText.toLowerCase().includes('enter dates')) {
        enterBtn.click();
    }
    const calEl = document.getElementById('site-availability');
    if (calEl) {
        calEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // Wait for availability container
    await waitForSelector('#site-availability', 8000);
    await new Promise((r) => setTimeout(r, 1200));

    // 3. Date Formatting
    const sDt = new Date(startDate + 'T00:00:00');
    const eDt = new Date(endDate + 'T00:00:00');
    const months = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    ];
    const targetMonthYear = months[sDt.getMonth()] + ' ' + sDt.getFullYear();
    const startAria = months[sDt.getMonth()] + ' ' + sDt.getDate() + ', ' + sDt.getFullYear();
    const endAria = months[eDt.getMonth()] + ' ' + eDt.getDate() + ', ' + eDt.getFullYear();

    // 4. Navigate calendar month if needed
    updateHUD('Navigating Calendar', `Looking for <b>${targetMonthYear}</b> in availability schedule...`);
    for (let i = 0; i < 15; i++) {
        const cal = document.getElementById('site-availability');
        const calText = cal ? cal.innerText : document.body.innerText;
        if (calText.includes(targetMonthYear)) {
            break;
        }

        const nextBtn = document.querySelector(
            "#site-availability button[aria-label*='Next' i], #site-availability button[aria-label*='next' i]"
        ) || Array.from(document.querySelectorAll('#site-availability button')).find(b => b.querySelector('svg'));

        if (nextBtn) {
            nextBtn.click();
            await new Promise((r) => setTimeout(r, 700));
        } else {
            break;
        }
    }

    await new Promise((r) => setTimeout(r, 800));

    function findDateCell(ariaMatch) {
        const cells = document.querySelectorAll("div.calendar-cell, [role='gridcell']");
        for (const c of cells) {
            const label = c.getAttribute('aria-label') || '';
            if (label.includes(ariaMatch)) return c;
        }
        return null;
    }

    // 5. Select Start Date
    updateHUD('Selecting Dates', `Selecting Check-in: <b>${startAria}</b>...`);
    const startCell = findDateCell(startAria);
    if (!startCell) {
        updateHUD('Date Not Found', `Could not find calendar cell for <b>${startAria}</b>.`, 'error');
        return;
    }
    if (startCell.getAttribute('aria-disabled') === 'true' || startCell.className.includes('unavailable')) {
        updateHUD('Date Unavailable', `Check-in date <b>${startAria}</b> is unavailable or restricted.`, 'error');
        return;
    }
    startCell.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    startCell.click();
    await new Promise((r) => setTimeout(r, 900));

    // 6. Select End Date
    updateHUD('Selecting Dates', `Selecting Check-out: <b>${endAria}</b>...`);
    const endCell = findDateCell(endAria);
    if (!endCell) {
        updateHUD('Date Not Found', `Could not find calendar cell for <b>${endAria}</b>.`, 'error');
        return;
    }
    endCell.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    endCell.click();
    await new Promise((r) => setTimeout(r, 1400));

    // 7. Click 'Add to Cart'
    updateHUD('Locking Campsite', 'Clicking <b>Add to Cart</b> to hold campsite...');
    const addBtn = document.querySelector('#add-cart-campsite, button.campsite-page-book-now-button-tracker');

    if (addBtn) {
        addBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        addBtn.click();
        await new Promise((r) => setTimeout(r, 2500));

        // Check for error dialogs (minimum stay rule, etc.)
        const errDialog = document.querySelector("div[role='dialog']");
        if (errDialog && errDialog.offsetParent !== null && errDialog.innerText.toLowerCase().includes('error')) {
            updateHUD('Booking Rule Block', 'Recreation.gov: ' + errDialog.innerText.replace(/\n/g, ' '), 'error');
            return;
        }

        // Check if "Proceed to Cart" button exists
        const proceedBtn = Array.from(document.querySelectorAll('button, a')).find(
            (el) => el.innerText && el.innerText.toLowerCase().includes('proceed to cart')
        );
        if (proceedBtn) {
            proceedBtn.click();
        }

        updateHUD(
            '🎉 Campsite Locked in Your Cart!',
            `Held for 15 minutes! Redirecting you to checkout...<br>
            <a href="https://www.recreation.gov/cart" style="display:inline-block; margin-top:8px; padding:6px 14px; background:#10b981; color:white; border-radius:6px; text-decoration:none; font-weight:700;">Complete Checkout ↗</a>`,
            'success'
        );

        // Remove hash from URL so reloads don't re-trigger
        try {
            history.replaceState(null, '', window.location.pathname + window.location.search);
        } catch (e) {}

        // Auto-redirect to cart after 2 seconds for a true zero-click checkout experience!
        setTimeout(() => {
            window.location.href = 'https://www.recreation.gov/cart';
        }, 2200);
    } else {
        updateHUD('Add to Cart Missing', 'Could not locate Add to Cart button. Please check dates.', 'error');
    }
})();
