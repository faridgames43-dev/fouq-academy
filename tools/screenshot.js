const { chromium } = require("/home/claude/.npm-global/lib/node_modules/playwright");

async function main() {
  const [, , url, outPath, cookie] = process.argv;
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args: ["--no-sandbox"] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  if (cookie) {
    const u = new URL(url);
    await context.addCookies([{ name: "session", value: cookie, domain: u.hostname, path: "/" }]);
  }
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  await page.screenshot({ path: outPath, fullPage: true });
  await browser.close();
}
main().catch((e) => { console.error(e); process.exit(1); });
