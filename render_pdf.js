// Real PDF generator for Arabic RTL reports.
// Chromium's own text engine handles Arabic shaping/joining and RTL layout
// correctly, which is why this renders through a headless browser instead
// of a Python PDF library (none of the Arabic-shaping python libs are
// available in this environment).
//
// Usage: node render_pdf.js <input_html_url_or_path> <output_pdf_path>
const path = require("path");
const { chromium } = require("/home/claude/.npm-global/lib/node_modules/playwright");

async function main() {
  const [, , inputArg, outputPath, sessionCookie] = process.argv;
  if (!inputArg || !outputPath) {
    console.error("usage: node render_pdf.js <url-or-path> <output.pdf> [session_cookie]");
    process.exit(1);
  }
  const url = inputArg.startsWith("http") ? inputArg : "file://" + path.resolve(inputArg);
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args: ["--no-sandbox"] });
  const context = await browser.newContext();
  if (sessionCookie) {
    const u = new URL(url);
    await context.addCookies([{ name: "session", value: sessionCookie, domain: u.hostname, path: "/" }]);
  }
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  await page.pdf({
    path: outputPath,
    format: "A4",
    printBackground: true,
    margin: { top: "10mm", bottom: "12mm", left: "10mm", right: "10mm" },
  });
  await browser.close();
  console.log("PDF written to", outputPath);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
