# VIKRAM v4 — Content Suite

A secure, self-hosted admin tool that lets non-technical clients manage their website content, generate vouchers, create quotes, and design social media content — all from one place. Files go to **Cloudflare R2**, the HTML is updated on **GitHub**, and **Cloudflare Pages** auto-deploys in ~60 seconds.

---

## What's New in v4

### v1 → v2 (Previous)

| Feature | v1 | v2 |
|---|---|---|
| Post-login landing page | ❌ | ✅ Home screen with feature cards |
| News images appear first | ❌ | ✅ New uploads always shown in batch 1 |
| Attractions — country targeting | ❌ | ✅ Dropdown: country → city |
| Attractions — city targeting | ❌ | ✅ Inserts card into exact city section |
| Favicon for VIKRAM UI only | ❌ | ✅ Served from `/static/favicon.ico` |

### v2 → v3 (Previous)

| Feature | v2 | v3 |
|---|---|---|
| Voucher Generator (VTOP WEB) | ❌ | ✅ Single & bulk PDF generation |
| Quotation Tool | ❌ | ✅ Service search & quote builder |
| Server-Sent Events (SSE) | ❌ | ✅ Real-time bulk processing progress |
| Excel/CSV bulk upload | ❌ | ✅ 500-row batch processing |
| Dynamic PDF headers | ❌ | ✅ Auto-extracts service info from Excel |

### v3 → v4 (Current)

| Feature | v3 | v4 |
|---|---|---|
| VStudio — Social Media Studio | ❌ | ✅ Content creation hub (Beta) |
| Quotation Tool — "New" badge | ❌ | ✅ Visual highlight for new features |
| VStudio — "New" badge | ❌ | ✅ Purple-themed card & glow effect |
| Version badge | v3 | ✅ v4 — Content Suite |
| Feature cards layout | 3 cards + 1 coming | ✅ 4 cards + 1 coming (VStudio added) |
| Date formatting in vouchers | ❌ | ✅ Automatic `dd/mm/yyyy` formatting |

---

## Features Overview

### 1. Content Manager 📤
Upload images and videos directly to your website. Files go to Cloudflare R2 and your site updates in ~60 seconds automatically.

**Pages supported:**
- **News & Gallery** — Images/videos appear at the top of the gallery (batch 1)
- **Attractions** — Select country → city → upload appears in that exact section

### 2. VTOP WEB (Voucher Generator) 📄
Generate hotel and tour booking confirmation PDFs instantly.

**Capabilities:**
- **Single Voucher** — Fill a form, download a print-ready PDF
- **Bulk Voucher** — Upload Excel/CSV with up to 500 rows
- **Real-time Progress** — SSE shows progress as each PDF generates
- **Dynamic Headers** — Auto-extracts service info from Excel data
- **ZIP Download** — All PDFs bundled into one ZIP file

### 3. Quotation Tool 💰
Search services and transfer rates from the live database. Build multi-service quotes with commission and VAT, then copy a formatted message straight to WhatsApp or email.

**Capabilities:**
- **Fuzzy Search** — Search by service name, destination, or type
- **Service Selection** — Click to expand, choose rates, add add-ons
- **Smart Cart** — Sidebar cart with per-item details
- **Commission & VAT** — Global commission and VAT application
- **WhatsApp Ready** — Copy formatted message with markdown support
- **USD Conversion** — Live or manual USD rate for each quote

### 4. VStudio 🎨 *(NEW — Beta)*
Create stunning social media content for your brand. Design posts, stories, and reels with AI-powered templates. Made for travel & hospitality marketing.

**Coming Soon:**
- 🖼️ Post Designer — AI-powered social media posts
- 📱 Story Creator — Animated Instagram/Facebook Stories
- 🎬 Reel Studio — Short-form video content creation
- 🎨 Brand Kit — Store colors, fonts, and logos
- 📊 Content Calendar — Schedule and plan posts
- 🤖 AI Caption Generator — Write engaging captions instantly
- 📈 Analytics Dashboard — Track content performance
- 🔄 Multi-Platform Publishing — Post to all platforms at once

---

## Project Structure
vikram/
├── features/
│ ├── auth.py # Shared authentication
│ ├── quotation/ # Quotation Tool
│ │ ├── router.py
│ │ ├── build_db.py
│ │ ├── parse_bkk_ptt.py
│ │ ├── parse_good_day.py
│ │ └── quotation.db
│ ├── voucher/ # Voucher Generator
│ │ ├── router.py
│ │ ├── generator.py
│ │ ├── bulk_processor.py
│ │ ├── voucher_schemas.py
│ │ ├── assets/ # Images, logos, stamps
│ │ └── templates/ # PDF templates
│ └── vstudio/ # VStudio (NEW)
│ └── router.py
├── static/
│ └── favicon.ico
├── templates/
│ ├── login.html
│ ├── home.html # Landing page with feature cards
│ ├── dashboard.html # Content Manager
│ ├── quotation.html # Quotation Tool
│ └── vstudio.html # VStudio (NEW)
├── main.py # FastAPI app — all backend logic
├── requirements.txt # Python dependencies
├── Procfile # For Railway deployment
├── railway.json # Railway config
├── .env.example # Copy to .env and fill values
└── .gitignore


---

## How It Works

### Content Manager Flow

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


### Voucher Generator Flow

Client navigates to /voucher → Voucher home
↓
Selects "Hotel Voucher" or "Tour Voucher"
↓
Fills form with booking details
↓ (or)
Uploads Excel/CSV for bulk generation
↓
PDF generated with dynamic headers
↓
Streams PDF to browser (single) or ZIP (bulk)
↓ (bulk)
Real-time progress via Server-Sent Events (SSE)
↓
ZIP file ready for download ✅



### Quotation Tool Flow

Client navigates to /quotation → Search interface
↓
Searches for services by name/destination/type
↓
Clicks service card → expands to show details
↓
Selects rate (Private/SIC) → chooses vehicle
↓
Adds optional add-ons → Adds to cart
↓
Sets commission → Sets USD rate
↓
Generates quote → Copy to clipboard ✅



---

## Insert Markers

VIKRAM uses HTML comments as insertion targets. These must exist in your website HTML files on GitHub.

### news2.html

The marker sits at the **very top** of the `galleryItems` JavaScript array, so new uploads always appear first in the gallery (batch 1, loaded immediately).

Setup — Step by Step
Step 1 — Cloudflare R2
Go to Cloudflare Dashboard → R2 Object Storage

Create a bucket (e.g. media)

In the bucket → Settings → enable Public Access → copy the public URL

Go to R2 → Manage R2 API Tokens → create a token with Object Read & Write

Copy: Account ID, Access Key ID, Secret Access Key

Step 2 — GitHub Token
Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)

Click Generate new token (classic)

Name: vikram-cms

Scope: ✅ repo (full control of private repositories)

Copy the token (starts with ghp_)

Step 3 — Website HTML Files
Make sure both marker files exist in your website GitHub repo:

news2.html — must contain // VIKRAM:INSERT_GALLERY_ITEM inside the galleryItems array

attractions.html — must contain all 9 <!-- VIKRAM:INSERT:country:city --> markers

These markers are already in place after running the v2 migration (see below).

Step 4 — Database Setup (Quotation Tool)
The Quotation Tool requires a SQLite database built from Excel files:


# The database is automatically built on first startup
# Or you can build it manually:
python -c "from features.quotation.build_db import main; main()"


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