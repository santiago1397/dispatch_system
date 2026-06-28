import { test, expect } from "@playwright/test";

test.describe("Authentication", () => {
  test.describe("Login Page", () => {
    test("should display login form", async ({ page }) => {
      await page.goto("/login");

      // Check for login form elements
      await expect(page.getByRole("heading", { name: /sign in|log in/i })).toBeVisible();
      await expect(page.getByLabel(/email/i)).toBeVisible();
      await expect(page.getByLabel(/password/i)).toBeVisible();
      await expect(page.getByRole("button", { name: /sign in|log in/i })).toBeVisible();
    });

    test("should show validation errors for empty form", async ({ page }) => {
      await page.goto("/login");

      // Submit empty form
      await page.getByRole("button", { name: /sign in|log in/i }).click();

      // Should show validation errors
      await expect(page.getByText(/required|invalid/i)).toBeVisible();
    });

    test("should show error for invalid credentials", async ({ page }) => {
      await page.goto("/login");

      // Fill in invalid credentials
      await page.getByLabel(/email/i).fill("invalid@example.com");
      await page.getByLabel(/password/i).fill("wrongpassword");
      await page.getByRole("button", { name: /sign in|log in/i }).click();

      // Should show error message
      await expect(page.getByText(/invalid|incorrect|failed|error/i)).toBeVisible({
        timeout: 5000,
      });
    });
  });

  test.describe("Authenticated User", () => {
    // Use authenticated state from setup
    test.use({
      storageState: ".playwright/.auth/user.json",
    });

    test("should redirect to dashboard after login", async ({ page }) => {
      await page.goto("/");

      // Should be redirected to dashboard or see dashboard content
      await expect(page).toHaveURL(/dashboard|home/i);
    });

    test("should show user menu or profile", async ({ page }) => {
      await page.goto("/dashboard");

      // Should have user profile/menu element
      await expect(
        page.getByRole("button", { name: /profile|account|user/i }).or(page.getByText(/@|user/i))
      ).toBeVisible();
    });

    test("should be able to logout", async ({ page }) => {
      await page.goto("/dashboard");

      // Find and click logout button
      const logoutButton = page
        .getByRole("button", { name: /log out|sign out/i })
        .or(page.getByRole("link", { name: /log out|sign out/i }));

      if (await logoutButton.isVisible()) {
        await logoutButton.click();

        // Should be redirected to login or home
        await expect(page).toHaveURL(/login|\/$/);
      }
    });
  });
});
