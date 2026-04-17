# VIKRAM — Content Manager

A simple, secure admin tool that lets non-technical clients upload images and videos to their website. Files go to **Cloudflare R2**, the HTML is updated on **GitHub**, and **Cloudflare Pages** auto-deploys the change.

---

## Project Structure

```
vikram/
├── main.py              # FastAPI app — all backend logic
├── requirements.txt     # Python dependencies
├── Procfile             # For Railway deployment
├── railway.json         # Railway config
├── .env.example         # Copy this to .env and fill values
├── .gitignore
└── templates/
    ├── login.html       # Login page
    └── dashboard.html   # Upload dashboard
```

---

## Step 1 — Prepare Your Website HTML Files

Add this HTML comment block wherever you want new images/videos to appear in `news.html` and `attractions.html`:

```html
<!-- VIKRAM:INSERT -->
```

VIKRAM will insert new media **right after** this marker every time your client publishes. Example:

```html
<section class="news-gallery">
  <h2>Latest News</h2>
  <!-- VIKRAM:INSERT -->
  <!-- Existing items below -->
  <div class="vikram-media-card">
    <img src="https://your-r2-url/old-photo.jpg" alt="Old photo">
  </div>
</section>
```

Optionally add this CSS to your website for basic styling of the injected cards:

```css
.vikram-media-card {
  margin: 20px 0;
}
.vikram-media-card img,
.vikram-media-card video {
  max-width: 100%;
  border-radius: 8px;
}
.vikram-caption {
  margin-top: 8px;
  font-size: 14px;
  color: #555;
}
.vikram-date {
  font-size: 12px;
  color: #999;
}
```

---

## Step 2 — Set Up Cloudflare R2

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → **R2 Object Storage**
2. Create a bucket (e.g. `my-website-media`)
3. In the bucket → **Settings** → Enable **Public Access** → copy the public URL
4. Go to **R2 → Manage R2 API Tokens** → Create Token with **Object Read & Write** permission
5. Copy **Account ID**, **Access Key ID**, and **Secret Access Key**

---

## Step 3 — Set Up GitHub Token

1. Go to GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token**
3. Give it a name like `vikram-cms`
4. Select scope: ✅ **repo** (full control of private repositories)
5. Copy the token (starts with `ghp_`)

---

## Step 4 — Local Setup

```bash
# Clone this repo
git clone https://github.com/yourusername/vikram.git
cd vikram

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your values (see comments in file)

# Generate a SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"
# Paste the output as SECRET_KEY in .env

# Run locally
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 — you should see the login page.

---

## Step 5 — Deploy to Railway (Free Hosting)

1. Push this project to a **new GitHub repo** (separate from your website repo)
2. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**
3. Select your VIKRAM repo
4. Go to **Variables** tab and add all the values from your `.env` file:
   - `ADMIN_PASSWORD`
   - `SECRET_KEY`
   - `GITHUB_TOKEN`
   - `GITHUB_REPO`
   - `GITHUB_BRANCH`
   - `R2_ACCOUNT_ID`
   - `R2_ACCESS_KEY`
   - `R2_SECRET_KEY`
   - `R2_BUCKET_NAME`
   - `R2_PUBLIC_URL`
5. Railway auto-deploys. Go to **Settings → Domains → Generate Domain** for a public URL
6. Share that URL + password with your client

---

## How It Works (Flow)

```
Client uploads file in browser
        ↓
FastAPI receives the file
        ↓
File uploaded to Cloudflare R2  →  gets a public URL
        ↓
GitHub API: fetch news.html or attractions.html
        ↓
Inject <img> or <video> tag after <!-- VIKRAM:INSERT --> marker
        ↓
GitHub API: commit updated HTML back to main branch
        ↓
Cloudflare Pages detects commit → auto-deploys in ~60 seconds ✅
```

---

## Adding More Pages

In `main.py`, find the `MANAGED_PAGES` dict and add your page:

```python
MANAGED_PAGES = {
    "news":        {"label": "News",        "file": "news.html"},
    "attractions": {"label": "Attractions", "file": "attractions.html"},
    "events":      {"label": "Events",      "file": "events.html"},  # ← add like this
}
```

Also add the `<!-- VIKRAM:INSERT -->` marker to `events.html` in your website repo.

---

## Security Notes

- Session cookies are signed with `itsdangerous` (7-day expiry)
- File size limit: 50 MB
- Accepted types: JPG, PNG, WebP, GIF, MP4, WebM
- `.env` is gitignored — never commit it
- For production, use a strong random password (12+ chars)
