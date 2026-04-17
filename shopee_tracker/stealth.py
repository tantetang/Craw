"""Anti-detection helpers for Playwright contexts.

Shopee's `/verify/traffic/error` page is triggered by multiple signals:
  - navigator.webdriver === true
  - Missing plugins / WebGL / Canvas
  - Abnormal navigation timing
  - Datacenter IP ranges (proxy-level, not handled here)
  - Too-fast requests without warm-up

This module patches the most common fingerprint leaks. Combine with:
  - residential proxy (quan trọng nhất)
  - warm-up browsing qua home → category → shop
  - delays 2-5s giữa request
"""

from __future__ import annotations

# JS chạy trước mọi page script — xoá các dấu hiệu Playwright để lại
_STEALTH_JS = """
// 1. navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 2. Chrome runtime (Playwright không có object 'chrome')
if (!window.chrome) {
    window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
}

// 3. Plugins + MimeTypes (headless thường rỗng)
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
        {name: 'Native Client', filename: 'internal-nacl-plugin'},
    ],
});

// 4. Languages realistic cho VN
Object.defineProperty(navigator, 'languages', {
    get: () => ['vi-VN', 'vi', 'en-US', 'en'],
});

// 5. Permissions query (Playwright trả 'denied' cho notifications → bất thường)
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : originalQuery(parameters)
    );
}

// 6. WebGL vendor/renderer (headless thường trả 'Brian Paul' / 'Mesa')
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Intel Inc.';        // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'Intel Iris OpenGL'; // UNMASKED_RENDERER_WEBGL
    return getParameter.call(this, param);
};

// 7. hardwareConcurrency + deviceMemory
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
"""


# Launch args giảm leak ở layer Chromium
STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def apply_stealth(context) -> None:
    """Inject stealth init script vào một Playwright BrowserContext."""
    context.add_init_script(_STEALTH_JS)


def warm_up(page, base: str = "https://shopee.vn/") -> None:
    """Giả lập một user bình thường: vào home, đợi, scroll nhẹ.

    Giúp sinh cookie SPC_F, SPC_CLIENTID và qua fingerprint check.
    """
    import random
    import time

    try:
        page.goto(base, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(random.randint(1500, 2800))
        # Scroll giả user xem trang
        for _ in range(random.randint(2, 4)):
            page.mouse.wheel(0, random.randint(300, 700))
            page.wait_for_timeout(random.randint(400, 900))
        time.sleep(random.uniform(0.8, 1.6))
    except Exception as e:
        print(f"[stealth.warm_up] Cảnh báo: {e}")
