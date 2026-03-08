# Upload This Template to GitHub

Step-by-step process to push this project to GitHub **as a template**, then use it for different projects (each with its own repo and no link back to the template).

---

## Prerequisites

- **Git** installed and configured (name and email set).
- **GitHub account**.
- This project is already a git repo (you have a `.git` folder).

---

## Step 1: Create a New Repository on GitHub

1. Go to [github.com](https://github.com) and sign in.
2. Click the **+** (top right) → **New repository**.
3. Fill in:
   - **Repository name:** e.g. `agent_template` or `pegaso-agent-template`.
   - **Description:** (optional) e.g. "Full-Stack FastAPI + Next.js template for AI/LLM apps".
   - **Visibility:** Public or Private.
   - **Do not** check "Add a README", "Add .gitignore", or "Choose a license" — this repo already has those.
4. Click **Create repository**.

---

## Step 2: Add GitHub as Remote

Open a terminal in this project root and run (replace with your GitHub username and repo name):

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
```

Example:

```bash
git remote add origin https://github.com/santi/agent_template.git
```

To use SSH instead:

```bash
git remote add origin git@github.com:YOUR_USERNAME/YOUR_REPO_NAME.git
```

Check that the remote is set:

```bash
git remote -v
```

You should see `origin` pointing to your GitHub URL.

---

## Step 3: Push Your Code

If your default branch is `master`:

```bash
git push -u origin master
```

If GitHub created the repo with a default branch named `main`, you can either:

**Option A – Push and set upstream to `main`:**

```bash
git branch -M main
git push -u origin main
```

**Option B – Keep `master` and push it:**

```bash
git push -u origin master
```

Then in GitHub: **Settings → General → Default branch** you can change the default to `master` if you prefer.

---

## Step 4: Verify on GitHub

1. Refresh the repository page on GitHub.
2. You should see all your files (backend, frontend, docs, etc.).
3. Confirm that **README.md** is shown on the main page.

---

## Step 5: Mark the Repo as a Template

So others (and you) can use **Use this template** for new projects:

1. On the repository page, go to **Settings** (repo settings, not your account).
2. Under **General**, find **Template repository**.
3. Check **Template repository**.
4. Save.

The repo will now show a green **Use this template** button. New repos created from it get a fresh copy of the code and their own git history (no connection to the template repo).

---

## Using the Template for Different Projects

You have two ways to start a new project from this template.

### Option A: From GitHub (recommended)

1. Open your template repo on GitHub.
2. Click **Use this template** → **Create a new repository**.
3. Name the new repo (e.g. `my-new-app`), create it.
4. Clone the new repo locally:  
   `git clone https://github.com/YOUR_USERNAME/my-new-app.git`
5. The clone already has `origin` pointing to the new repo. Start coding and push as usual.

No need to change or delete any remote: each new repo is independent.

### Option B: From this same local folder (reuse one folder for each new project)

If you want to keep using **this** folder to push to different new projects (e.g. you create a new GitHub repo per client and push from here):

1. **After the first upload** (template is already on GitHub and marked as template), remove the template remote from this folder so this folder is no longer tied to the template repo:
   ```bash
   git remote remove origin
   ```
2. For **each new project**:
   - Create a **new** repository on GitHub (empty, no README).
   - Add that new repo as `origin` and push:
     ```bash
     git remote add origin https://github.com/YOUR_USERNAME/NEW_PROJECT_REPO.git
     git push -u origin master
     ```
   - After pushing, if you will use this folder again for another project, remove `origin` again so you don’t accidentally push to the wrong repo:
     ```bash
     git remote remove origin
     ```
   - Repeat for the next project.

**Note:** This reuses the same git history in every new repo. If you want each project to have a clean history and no shared commits, use **Option A** (Use this template → create new repo → clone) instead.

---

## Optional: Update README for Your Repo

- Edit **README.md** to replace any "vstorm-co" or template links with your repo URL and project name.
- You can add a line at the top like: "Forked/adapted from [full-stack-fastapi-nextjs-llm-template](https://github.com/vstorm-co/full-stack-fastapi-nextjs-llm-template)."

---

## Summary Checklist

| Step | Action |
|------|--------|
| 1 | Create new repo on GitHub (no README/.gitignore) |
| 2 | `git remote add origin https://github.com/USER/REPO.git` |
| 3 | `git push -u origin master` (or `main` after `git branch -M main`) |
| 4 | Verify files on GitHub |
| 5 | Settings → **Template repository** ✓ (so "Use this template" appears) |

**For each new project:** use **Use this template** on GitHub (Option A), or from this folder add a new `origin` and push, then `git remote remove origin` if you reuse the folder (Option B).
