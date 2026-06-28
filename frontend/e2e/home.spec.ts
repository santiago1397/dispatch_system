import { test, expect } from "@playwright/test";

test.describe("Home redirect", () => {
  test("unauthenticated user is redirected to /login", async ({ page }) => {
    await page.context().clearCookies();
    await page.goto("/");

    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe("Home redirect (authenticated)", () => {
  test.use({
    storageState: ".playwright/.auth/user.json",
  });

  test("authenticated user is redirected to /dashboard", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(/\/dashboard/);
  });
});
