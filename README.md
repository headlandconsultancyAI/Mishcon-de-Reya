# Mishcon LinkedIn engagement dashboard

Ranks Mishcon de Reya partners and knowledge lawyers by the median engagement on their own original LinkedIn posts, with a department filter. It refreshes itself every Monday.

## How the weekly refresh works

1. **Monday 8am UK time:** your Apify task runs its scrape on its own schedule.
2. **When it finishes,** Apify's webhook pings this repo and sends the ID of the new dataset.
3. **GitHub Actions** pulls that dataset, merges it into `data/posts.json` and rebuilds the page. It saves the history back to the repo and publishes to GitHub Pages.
4. **Backup:** if the ping never arrives, a schedule runs at 10:30 UTC on Mondays and picks up Apify's last successful run instead.

Posts are merged on their post ID. That means new posts get added, posts seen again get their latest reaction counts, and nothing is ever thrown away. So even if the weekly scrape only reaches back a few weeks, the dashboard still has a full 12 months to work from.

## What's in here

| File | What it's for |
|---|---|
| `build.py` | Fetches, merges and builds. Uses only standard Python. |
| `template.html` | The page design. Numbers and headline copy get filled in on each build. |
| `data/partners.csv` | The partner list: name, department, practice group, title, LinkedIn URL. **Edit this when people join or leave.** |
| `data/focus.json` | The hand-written focus area for each ranked person. |
| `data/posts.json` | Every post collected so far. Updated automatically. |
| `data/meta.json` | When the last scrape ran. Updated automatically. |
| `.github/workflows/update-dashboard.yml` | The automation. |

## One-off setup

**1. Upload the files.** Use GitHub Desktop, or drag them into a new repo on github.com. Be careful with drag and drop, because it skips the `.github` folder. If that happens, click **Add file → Create new file**, name it `.github/workflows/update-dashboard.yml` and paste the contents in.

**2. Turn on Pages.** Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.

**3. Add your Apify details.** Go to **Settings → Secrets and variables → Actions**:
- On the **Secrets** tab, add `APIFY_TOKEN` (your Apify API token)
- On the **Variables** tab, add `APIFY_TASK_ID` (the task's ID, or `username~task-name`)

**4. Schedule the Apify task.** In Apify, go to **Schedules → Create new**. Set the cron to `0 8 * * 1`, set the timezone to **Europe/London**, and add the **task** (not the actor) to it.

**5. Make a GitHub token for Apify.** Click your profile picture, then go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**:
- Repository access: **Only select repositories**, then pick this repo
- Permissions: set **Contents** to **Read and write**
- Copy the token. You only see it once.

**6. Add the webhook to the Apify task.** Open the task, go to **Integrations**, and add an HTTP webhook:
- **Event:** Run succeeded
- **URL:** `https://api.github.com/repos/YOUR-USERNAME/YOUR-REPO/dispatches`
- **Headers template:**
  ```
  {
    "Authorization": "Bearer YOUR_GITHUB_TOKEN",
    "Accept": "application/vnd.github+json"
  }
  ```
- **Payload template** (leave the `{{ }}` bit without quote marks round it, because Apify fills it in):
  ```
  {
    "event_type": "apify-run",
    "client_payload": { "datasetId": {{resource.defaultDatasetId}} }
  }
  ```

**7. Test it.** Go to **Actions → Update dashboard → Run workflow**. That builds from Apify's last successful run. Then click **Test** on the Apify webhook, and a run marked **Repository dispatch** should show up within a minute.

## Keeping it right

- **Someone's ranked but has no focus area.** The Actions run shows a yellow warning naming them. Add a line to `data/focus.json`: `"Name": ["Label", "Short note"]`.
- **A scraped profile isn't in the partner list.** You'll get a warning, and their posts are left out until they're added to `data/partners.csv`. The name must match what you put in `focus.json`.
- **New partner.** Add a row to `partners.csv` and add their URL to the Apify task.
- **Renew the GitHub token before it expires.** Once it lapses, the webhook stops working and you're relying on the 10:30 backup. Nothing breaks, it's just later.
- **A red cross in Actions** means GitHub has emailed you. The log says which step failed. An empty scrape fails on purpose, so a bad Apify run never wipes the page.

## Running it on your own machine

```
python build.py                          # rebuild from saved data
python build.py --import apify-export.json   # add a manual export first
```

Then open `site/index.html`.
