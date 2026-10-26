/*
 * Browser smoke test against a RUNNING stack on a FRESH data directory (no seeded data needed).
 *
 *   YTC_DATA_DIR=<scratch dir> python launcher.py          # in one terminal
 *   node frontend/e2e/smoke.cjs [screenshot-dir]            # in another
 *
 * Requires Playwright (not a project dependency): npm i -D playwright && npx playwright install chromium
 * It performs first-run owner setup, so run it only against a throwaway data directory.
 */
const { chromium } = require("playwright");
const BASE = process.env.E2E_BASE || "http://localhost:3000";
const PW = "Correct-Horse-9";
const OUT = process.argv[2];
const problems = [];

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const expected422 = { count: 0 };
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    if (m.text().includes("422") && expected422.count > 0) { expected422.count--; return; }  // our deliberate weak-password try
    problems.push(`console error on ${page.url()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`page error on ${page.url()}: ${e.message}`));
  page.on("response", (r) => { if (r.status() >= 500) problems.push(`HTTP ${r.status()} ${r.url()}`); });
  const shot = (n) => (OUT ? page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true }) : Promise.resolve());

  // first-run setup: weak password is refused with a clear message, strong password creates the owner
  await page.goto(BASE + "/");
  await page.waitForURL("**/login");
  await page.getByRole("heading", { name: "Set up your owner account" }).waitFor();
  expected422.count = 1;
  await page.getByLabel("Password", { exact: true }).fill("weakpass");
  await page.getByLabel("Repeat password").fill("weakpass");
  await page.getByRole("button", { name: "Create owner account" }).click();
  await page.locator("form [role=alert]").filter({ hasText: "at least 12 characters" }).waitFor();
  await page.getByLabel("Password", { exact: true }).fill(PW);
  await page.getByLabel("Repeat password").fill(PW);
  await page.getByRole("button", { name: "Create owner account" }).click();
  await page.waitForURL(BASE + "/");
  await page.getByRole("heading", { name: "Executive overview" }).waitFor();

  // every page renders and shows honest empty/unavailable states on a fresh install
  const pages = [
    ["/", "Executive overview"], ["/channels/happy-birthday-songs", "Happy Birthday Songs"],
    ["/channels/medicine-information", "Medicine Information"], ["/channels/ai-story-videos", "AI Story Videos"],
    ["/organization", "Organization"], ["/board", "Company Intelligence Board"], ["/approvals", "Approval Center"],
    ["/rights", "Originality & Rights"], ["/reports", "Executive reports"], ["/settings", "Settings & control"],
    ["/diagnostics", "Diagnostics"], ["/plan", "Content plan"], ["/operations", "Operations"],
    ["/performance", "Performance"], ["/comments", "Comments"], ["/learning", "Learning"], ["/ai", "AI brain"],
  ];
  for (const [path, heading] of pages) {
    await page.goto(BASE + path);
    await page.getByRole("heading", { name: heading, exact: true }).first().waitFor({ timeout: 15000 });
    await page.waitForLoadState("networkidle");
    await shot(path.replace(/\W+/g, "_") || "root");
  }
  await page.goto(BASE + "/performance");
  await page.getByText("Until then every figure is unavailable; none is shown as zero.").first().waitFor({ timeout: 15000 });
  await page.getByText("Revenue is not collected").first().waitFor({ timeout: 15000 });
  await page.goto(BASE + "/comments");
  await page.getByText("No comments").first().waitFor({ timeout: 15000 });
  await page.goto(BASE + "/learning");
  await page.getByText("No lessons yet").first().waitFor({ timeout: 15000 });
  await page.getByRole("tab", { name: "Experiments" }).click();
  await page.getByText("No experiments yet").first().waitFor({ timeout: 15000 });
  await page.getByRole("tab", { name: "Compare videos" }).click();
  await page.getByText("This compares videos; it is not an experiment").first().waitFor({ timeout: 15000 });
  await page.goto(BASE + "/plan");
  await page.getByRole("button", { name: "Propose an idea" }).click();
  await page.getByLabel("Title", { exact: true }).fill("Lantern Parade Birthday");
  await page.getByLabel("Concept").fill("A gentle original piece about a lantern parade through a quiet village at dusk.");
  await page.getByLabel("Audience", { exact: true }).fill("Parents and children aged 3-8");
  await page.getByRole("button", { name: "Propose and assess" }).click();
  await page.getByText("How the score was worked out").waitFor();
  await page.getByText("Unavailable — no trend, search or audience data has been recorded.").waitFor();  // demand is never invented
  await page.keyboard.press("Escape");
  await page.goto(BASE + "/channels/happy-birthday-songs");
  await page.getByText("Shown as unavailable, never as zero.").first().waitFor();

  // the CEO report can be generated and says what really happened (nothing, on a fresh install)
  await page.goto(BASE + "/reports");
  await page.getByRole("button", { name: "Generate report now" }).click();
  await page.getByText("No tasks were created or completed in this period.").first().waitFor({ timeout: 15000 });
  await page.getByText("Departments with no activity").waitFor();

  // emergency pause: the banner must show immediately (well before the 15 s poll) and clear on resume
  await page.goto(BASE + "/");
  await page.getByRole("button", { name: "Emergency pause" }).click();
  await page.getByLabel(/Reason/).fill("smoke test");
  await page.getByRole("button", { name: "Pause now" }).click();
  await page.locator("main > div[role=alert]").filter({ hasText: "No new work, paid generation, uploads or replies are starting" }).waitFor({ timeout: 3000 });
  await page.goto(BASE + "/settings");
  await page.getByRole("button", { name: "Resume company" }).click();
  await page.locator("main > div[role=alert]").waitFor({ state: "detached", timeout: 3000 });

  // audit chain is intact after all of the above
  await page.goto(BASE + "/diagnostics");
  await page.getByRole("tab", { name: /Audit log/ }).click();
  await page.getByRole("button", { name: "Verify integrity" }).click();
  await page.getByText(/Chain intact/).waitFor();

  // mobile layout has no horizontal scrolling
  const m = await browser.newContext({ viewport: { width: 390, height: 844 }, storageState: await ctx.storageState() });
  const mp = await m.newPage();
  await mp.goto(BASE + "/");
  await mp.getByRole("heading", { name: "Executive overview" }).waitFor();
  await mp.waitForLoadState("networkidle");
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 1) problems.push(`mobile horizontal overflow ${overflow}px`);

  // sign out, and a guarded page bounces back to login
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL("**/login");
  await page.goto(BASE + "/diagnostics");
  await page.waitForURL("**/login");

  await browser.close();
  if (problems.length) { console.error("SMOKE TEST PROBLEMS:\n" + problems.join("\n")); process.exit(1); }
  console.log("SMOKE TEST PASSED");
})().catch((e) => { console.error("SMOKE TEST FAILED:", e.message); process.exit(2); });
