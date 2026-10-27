// Run with Playwright available via NODE_PATH. Native iOS PWA chrome still needs device QA.
const { chromium } = require('playwright');
const { readFileSync } = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const standalone of [false, true]) {
      const page = await browser.newPage({ viewport: { width: 440, height: 956 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(({ standalone }) => {
        const originalMatch = window.matchMedia.bind(window);
        window.matchMedia = q => q === '(display-mode: standalone)'
          ? { matches: standalone, addEventListener() {} } : originalMatch(q);
        // Explicitly model keyboard viewport events, not native iOS rendering.
        const vv = new EventTarget();
        Object.assign(vv, { height: 956, width: 440, offsetTop: 0, pageTop: 0, scale: 1 });
        Object.defineProperty(window, 'visualViewport', { value: vv });
        window.resizeVisualViewport = (height, top = 0) => {
          Object.assign(vv, { height, offsetTop: top, pageTop: top });
          vv.dispatchEvent(new Event('resize'));
        };
      }, { standalone });
      await page.route('https://chat.test/**', route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html',
          body: readFileSync(process.env.CHAT_TEST_HTML || path.join(__dirname, '../web/index.html'), 'utf8') });
        return route.fulfill({ json: { chars: [], msgs: [], err_seqs: [], worldbook: '' } });
      });
      await page.goto('https://chat.test/');
      await page.waitForLoadState('networkidle');
      await page.evaluate(() => openChat('lishen'));
      const box = async id => page.locator(id).boundingBox();
      let input = await box('#inputBar');
      assert.ok(Math.abs(input.y + input.height - 956) < 2, 'idle input bar reaches viewport bottom');
      await page.evaluate(() => {
        document.getElementById('msgArea').innerHTML = '<p style="height:2500px">Long conversation</p>';
      });
      input = await box('#inputBar');
      assert.ok(Math.abs(input.y + input.height - 956) < 2, 'long messages do not push composer offscreen');
      assert.ok(await page.locator('#msgArea').evaluate(el => el.scrollHeight > el.clientHeight));
      await page.locator('#msgInput').focus();
      await page.evaluate(() => resizeVisualViewport(540, 20));
      await page.waitForTimeout(80);
      input = await box('#inputBar');
      assert.ok(Math.abs(input.y + input.height - 560) < 2,
        `keyboard: input bar bottom ${input.y + input.height} must follow visible bottom 560`);
      // Safari can scroll the root to reveal a focused input even with overflow:hidden.
      await page.evaluate(() => {
        const extra = document.createElement('div');
        extra.id = 'root-scroll-fixture';
        extra.style.cssText = 'position:absolute;top:1400px;width:1px;height:1px';
        document.body.appendChild(extra);
        window.scrollTo(0, 120);
        visualViewport.pageTop = scrollY + visualViewport.offsetTop;
        visualViewport.dispatchEvent(new Event('scroll'));
      });
      assert.equal(await page.evaluate(() => scrollY), 120, 'fixture must actually scroll the root');
      await page.waitForTimeout(80);
      input = await box('#inputBar');
      assert.ok(Math.abs(input.y + input.height - 560) < 2,
        `root scroll: composer bottom ${input.y + input.height} must remain at visual bottom 560`);
      await page.evaluate(() => {
        window.scrollTo(0, 0);
        document.getElementById('root-scroll-fixture').remove();
        visualViewport.pageTop = visualViewport.offsetTop;
        visualViewport.dispatchEvent(new Event('scroll'));
      });
      await page.waitForTimeout(80);
      // Blur may precede the keyboard closing animation: do not immediately expand.
      await page.locator('#msgInput').evaluate(el => el.blur());
      await page.waitForTimeout(80);
      input = await box('#inputBar');
      assert.ok(Math.abs(input.y + input.height - 560) < 2, 'retain keyboard layout until viewport restores');
      await page.evaluate(() => {
        const extra = document.createElement('div');
        extra.id = 'dismiss-scroll-fixture';
        extra.style.cssText = 'position:absolute;top:1400px;width:1px;height:1px';
        document.body.appendChild(extra);
        window.scrollTo(0, 120);
        resizeVisualViewport(956);
      });
      await page.waitForTimeout(80);
      assert.equal(await page.evaluate(() => scrollY), 0, 'dismissal clears leftover focus scroll');
      await page.locator('#dismiss-scroll-fixture').evaluate(el => el.remove());
      input = await box('#inputBar');
      assert.ok(Math.abs(input.y + input.height - 956) < 2, 'restore full height after keyboard closes');
      assert.equal(await page.locator('#viewportDebug').count(), 0, 'temporary diagnostics removed');
      await page.setViewportSize({ width: 1280, height: 956 });
      const app = await box('#app');
      assert.equal(app.width, 720, 'desktop maximum width');
      assert.equal(app.x, 280, 'desktop centering');
      await page.evaluate(() => closeChat());
      assert.ok(await page.locator('#listScreen').isVisible(), 'back navigation restores list');
      assert.ok(await page.locator('#chatScreen').isHidden(), 'chat is hidden after back');
      assert.deepEqual(errors, [], 'no uncaught script errors');
      console.log(`PASS ${standalone ? 'standalone detection' : 'browser'}: idle, long messages, keyboard + root scroll, dismissal, desktop`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
