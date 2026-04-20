# VIKRAM v2 — Content Manager

A secure, self-hosted admin tool that lets non-technical clients publish images and videos to their website with zero code. Files go to **Cloudflare R2**, the HTML is updated on **GitHub**, and **Cloudflare Pages** auto-deploys in ~60 seconds.

---

## What's New in v2

| Feature | v1 | v2 |
|---|---|---|
| Post-login landing page | ❌ | ✅ Home screen with feature cards |
| News images appear first | ❌ | ✅ New uploads always shown in batch 1 |
| Attractions — country targeting | ❌ | ✅ Dropdown: country → city |
| Attractions — city targeting | ❌ | ✅ Inserts card into exact city section |
| Favicon for VIKRAM UI only | ❌ | ✅ Served from `/static/favicon.ico` |

---

## Project Structure

```
vikram/
├── main.py                  # FastAPI app — all backend logic
├── requirements.txt         # Python dependencies
├── Procfile                 # For Railway deployment (if used)
├── railway.json             # Railway config (if used)
├── .env.example             # Copy to .env and fill values
├── .gitignore
├── static/
│   └── favicon.ico          # VIKRAM app favicon (not injected into website)
└── templates/
    ├── login.html           # Login page
    ├── home.html            # NEW: Landing page after login
    └── dashboard.html       # Upload dashboard
```

---

## How It Works

```
Client logs in → Home page (feature overview)
        ↓
Clicks "Content Manager" → Dashboard
        ↓
Selects page (News or Attractions)
        ↓ (Attractions only)
Selects Country → City
        ↓
Uploads image or video (max 50 MB)
        ↓
FastAPI uploads file to Cloudflare R2 → gets public URL
        ↓
GitHub API fetches news2.html or attractions.html
        ↓
Inserts HTML/JS snippet at the correct marker position
        ↓
GitHub API commits updated HTML to main branch
        ↓
Cloudflare Pages detects commit → auto-deploys in ~60 seconds ✅
```

---

## Insert Markers

VIKRAM uses HTML comments as insertion targets. These must exist in your website HTML files on GitHub.

### news2.html

The marker sits at the **very top** of the `galleryItems` JavaScript array, so new uploads always appear first in the gallery (batch 1, loaded immediately).

```javascript
const galleryItems = [
    // VIKRAM:INSERT_GALLERY_ITEM        ← new items injected here
    { type: 'image', src: '...', thumb: '...', batch: 1 },
    ...
```

### attractions.html

Each country/city section has its own marker placed at the **end** of that city's `.cards` grid:

```html
<!-- VIKRAM:INSERT:thailand:bangkok -->
<!-- VIKRAM:INSERT:thailand:pattaya -->
<!-- VIKRAM:INSERT:thailand:phuket -->
<!-- VIKRAM:INSERT:thailand:krabi -->
<!-- VIKRAM:INSERT:thailand:khon-kaen -->
<!-- VIKRAM:INSERT:thailand:hua-hin -->
<!-- VIKRAM:INSERT:indonesia:must-visit -->
<!-- VIKRAM:INSERT:vietnam:must-visit -->
<!-- VIKRAM:INSERT:japan:must-visit -->
```

**Important:** Never delete or rename these comments — VIKRAM finds injection points by searching for them exactly.

---

## Setup — Step by Step

### Step 1 — Cloudflare R2

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → **R2 Object Storage**
2. Create a bucket (e.g. `media`)
3. In the bucket → **Settings** → enable **Public Access** → copy the public URL
4. Go to **R2 → Manage R2 API Tokens** → create a token with **Object Read & Write**
5. Copy: **Account ID**, **Access Key ID**, **Secret Access Key**

### Step 2 — GitHub Token

1. Go to GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Name: `vikram-cms`
4. Scope: ✅ **repo** (full control of private repositories)
5. Copy the token (starts with `ghp_`)

### Step 3 — Website HTML Files

Make sure both marker files exist in your website GitHub repo:

- `news2.html` — must contain `// VIKRAM:INSERT_GALLERY_ITEM` inside the `galleryItems` array
- `attractions.html` — must contain all 9 `<!-- VIKRAM:INSERT:country:city -->` markers

These markers are already in place after running the v2 migration (see below).

### Step 4 — Local Setup

```bash
# Clone this repo
git clone https://github.com/yourusername/vikram.git
cd vikram

# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your real values

# Generate a SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"
# Paste the output as SECRET_KEY in .env

# Run locally
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 → you'll see the login page.

### Step 5 — Deploy to Render (Free)

1. Push this VIKRAM project to a **new GitHub repo** (separate from your website repo)
2. Go to [render.com](https://render.com) → sign up with GitHub
3. Click **New → Web Service** → select your VIKRAM repo
4. Set:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Go to **Environment** tab → add all variables from `.env`:
   - `ADMIN_PASSWORD`
   - `SECRET_KEY`
   - `GITHUB_TOKEN`
   - `GITHUB_REPO` (format: `username/repo-name`)
   - `GITHUB_BRANCH` (usually `main`)
   - `R2_ACCOUNT_ID`
   - `R2_ACCESS_KEY`
   - `R2_SECRET_KEY`
   - `R2_BUCKET_NAME`
   - `R2_PUBLIC_URL`
6. Click **Deploy** — Render gives you a URL like `https://vikram.onrender.com`
7. Share that URL + password with your client

**Render free tier note:** The service sleeps after 15 minutes of inactivity and takes ~30 seconds to wake on the next request. This is fine for a low-traffic admin tool. For always-on free hosting, use Koyeb instead.

---

## v2 Migration — Website HTML Files

After deploying v2, you need to update your website HTML files **once** to add the new markers. Push the updated `news2.html` and `attractions.html` to your website GitHub repo (not the VIKRAM repo).

### news2.html change

Find this line:
```javascript
const galleryItems = [
    // Numbered images & videos (100-129)
```

Change it to:
```javascript
const galleryItems = [
    // VIKRAM:INSERT_GALLERY_ITEM
    // Numbered images & videos (100-129)
```

That's it. The marker must be the **first line** inside the array brackets.

### attractions.html change

Add the 9 city markers to the end of each city's `<div class="cards">` section. Example for Bangkok (around line 519 of your current file):

```html
                <!-- VIKRAM:INSERT:thailand:bangkok -->

            </div>   ← this is the closing </div> of <div class="cards">
        </div>       ← this closes <div class="city-section">
```

The updated `attractions.html` file provided with this release already has all markers in place. Simply replace your website's `attractions.html` with that file (or manually add the 9 markers if you've made other changes since).

---

## Adding More Countries or Cities

### In `main.py`

Add to `ATTRACTIONS_LOCATIONS`:
```python
("thailand", "chiang-mai"): "<!-- VIKRAM:INSERT:thailand:chiang-mai -->",
```

Add to `ATTRACTIONS_COUNTRIES`:
```python
"thailand": {
    "label": "Thailand",
    "cities": {
        "bangkok":    "Bangkok",
        "chiang-mai": "Chiang Mai",   # ← add here
        ...
    },
},
```

### In `attractions.html`

Add the corresponding marker inside the new city's `.cards` div:
```html
<div class="cards">
    <!-- existing cards -->
    <!-- VIKRAM:INSERT:thailand:chiang-mai -->
</div>
```

### Adding a New Page (e.g. Events)

In `main.py`, add to `MANAGED_PAGES`:
```python
"events": {
    "label":         "Events",
    "file":          "events.html",
    "r2_prefix":     "media/events",
    "image_dir":     "images",
    "video_dir":     "videos",
    "insert_marker": "<!-- VIKRAM:INSERT -->",
},
```

Then add `<!-- VIKRAM:INSERT -->` to `events.html` in your website repo and add a card builder for the events page if the HTML structure differs from attractions.

---

## Environment Variables Reference

| Variable | Description | Example |
|---|---|---|
| `ADMIN_PASSWORD` | Login password for VIKRAM | `MySecurePass@2025` |
| `SECRET_KEY` | Random hex for signing session cookies | `91477766c238...` |
| `GITHUB_TOKEN` | GitHub personal access token (repo scope) | `ghp_xxxxx` |
| `GITHUB_REPO` | Website repo in `user/repo` format | `VISHAKHNAIR16/VAYOAURA-WEBSITE` |
| `GITHUB_BRANCH` | Branch to commit to | `main` |
| `R2_ACCOUNT_ID` | Cloudflare account ID | `92e3ea011db...` |
| `R2_ACCESS_KEY` | R2 API access key | `5ffa43cb92...` |
| `R2_SECRET_KEY` | R2 API secret key | `45341001aa...` |
| `R2_BUCKET_NAME` | R2 bucket name | `media` |
| `R2_PUBLIC_URL` | Public URL of your R2 bucket | `https://pub-xxx.r2.dev` |

---

## File Type & Size Limits

| Type | Accepted formats | Max size |
|---|---|---|
| Images | JPG, PNG, WebP, GIF | 50 MB |
| Videos | MP4, WebM, OGG | 50 MB |

---

## Security Notes

- Session cookies are signed with `itsdangerous` (7-day expiry, HttpOnly, SameSite=Lax)
- `.env` is gitignored — never commit it
- The VIKRAM repo and the website repo are separate — VIKRAM only has write access to your website via the GitHub token
- Rotate your GitHub token and R2 API keys if they are ever exposed
- Use a strong random password (12+ characters, mix of letters, numbers, symbols)

---

## Health Check

GET `/health` returns:
```json
{
  "status": "ok",
  "app": "VIKRAM CMS",
  "timestamp": "2025-01-01T12:00:00",
  "r2_bucket": "media",
  "github_repo": "VISHAKHNAIR16/VAYOAURA-WEBSITE"
}
```