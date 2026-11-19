import os
import re
import json
import math
import sqlite3
import feedparser
import requests
import random
import shutil
from collections import Counter
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import format_datetime

# --- KONFIGURASI PATH & DOMAIN OTOMATIS ---
DB_PATH = "database.sqlite"
RSS_FILE = "rss_list.txt"
OUTPUT_DIR = "."
ARTIKEL_DIR = "artikel"
ITEMS_PER_PAGE = 200

GH_REPO = os.environ.get("GITHUB_REPOSITORY", "")
if "/" in GH_REPO:
    gh_owner, gh_name = GH_REPO.split("/", 1)
    BASE_URL = f"https://{gh_owner.lower()}.github.io/{gh_name}"
else:
    BASE_URL = "https://railfansday.github.io/artikelsemua65"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

EN_SYNONYMS = {
    r'\bannounced\b': ['unveiled', 'revealed', 'declared', 'confirmed'],
    r'\bannounces\b': ['unveils', 'reveals', 'introduces', 'confirms'],
    r'\bsaid\b': ['stated', 'noted', 'remarked', 'mentioned'],
    r'\bsays\b': ['states', 'notes', 'indicates', 'claims'],
    r'\bnew\b': ['latest', 'upcoming', 'newly introduced', 'fresh'],
    r'\bbig\b': ['major', 'significant', 'massive', 'substantial'],
    r'\bissue\b': ['problem', 'concern', 'glitch', 'matter'],
    r'\bissues\b': ['problems', 'concerns', 'complications', 'challenges'],
    r'\busers\b': ['customers', 'consumers', 'owners', 'people'],
    r'\bcompany\b': ['firm', 'tech giant', 'organization', 'manufacturer'],
    r'\blaunched\b': ['rolled out', 'introduced', 'released', 'debuted'],
    r'\blaunches\b': ['rolls out', 'introduces', 'releases', 'debuts'],
    r'\bplan\b': ['strategy', 'initiative', 'proposal', 'blueprint'],
    r'\bplans\b': ['aims', 'intends', 'prepares', 'looks'],
    r'\bshowed\b': ['demonstrated', 'indicated', 'highlighted', 'revealed'],
    r'\bshows\b': ['demonstrates', 'indicates', 'highlights', 'reveals'],
    r'\bupdate\b': ['upgrade', 'revision', 'enhancement', 'patch'],
    r'\bimportant\b': ['crucial', 'key', 'notable', 'vital'],
    r'\bquickly\b': ['rapidly', 'swiftly', 'promptly'],
    r'\bcurrently\b': ['presently', 'at the moment', 'right now']
}

CATEGORY_RULES = {
    "AI & Tech": ["ai", "openai", "claude", "gemini", "llm", "chatgpt", "robot", "software", "cloud", "google", "microsoft", "meta", "anthropic"],
    "Hardware & Chips": ["nvidia", "amd", "intel", "chip", "gpu", "cpu", "semiconductor", "tsmc", "processor", "laptop", "gaming"],
    "EV & Auto": ["ev", "electric", "tesla", "byd", "car", "suv", "vehicle", "battery", "autonomous", "waymo", "cybertruck", "motor"],
    "Gadgets": ["apple", "iphone", "android", "samsung", "pixel", "phone", "smartphone", "camera", "watch", "ipad", "macbook"],
    "Business & Policy": ["trump", "senate", "law", "investigation", "china", "tariff", "billion", "million", "ceo", "stock", "market", "sec", "trade"]
}

STOP_WORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'its', 'are', 'was',
    'latest', 'update', 'report', 'news', 'breaking', 'new', 'how', 'why',
    'what', 'when', 'who', 'will', 'can', 'has', 'have', 'had', 'not', 'but',
    'out', 'about', 'into', 'over', 'after', 'more', 'your', 'you', 'all'
}

ADSTERRA_CODE = """
<div style="text-align:center; margin:20px 0;">
    <script type="text/javascript">
        atOptions = {
            'key' : 'f9e40eb3b3cd05f1038aac7b36b33ad2',
            'format' : 'iframe',
            'height' : 90,
            'width' : 728,
            'params' : {}
        };
    </script>
    <script type="text/javascript" src="https://upholdbrilliancerecast.com/f9e40eb3b3cd05f1038aac7b36b33ad2/invoke.js"></script>
</div>
"""

def create_slug(title, artikel_id):
    clean = re.sub(r'[^a-zA-Z0-9\s-]', '', str(title)).lower()
    slug = re.sub(r'[\s-]+', '-', clean).strip('-')
    if not slug:
        slug = "news-article"
    return f"{slug[:65]}-{artikel_id}.html"

def get_article_rel_url(title, artikel_id):
    return f"{ARTIKEL_DIR}/{create_slug(title, artikel_id)}"

def get_article_abs_url(title, artikel_id):
    return f"{BASE_URL}/{ARTIKEL_DIR}/{create_slug(title, artikel_id)}"

def apply_synonym_spin(text):
    for pattern, replacements in EN_SYNONYMS.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            if random.random() < 0.6:
                rep = random.choice(replacements)
                def replace_match(match):
                    word = match.group(0)
                    if word.istitle():
                        return rep.capitalize()
                    elif word.isupper():
                        return rep.upper()
                    return rep
                text = re.sub(pattern, replace_match, text, count=1, flags=re.IGNORECASE)
    return text

def paraphrase_title(title):
    if not title or title == 'Tanpa Judul':
        return title
    spun_title = apply_synonym_spin(title.strip())
    prefixes = ["Latest Update: ", "Tech Report: ", "Industry News: ", "Breaking: "]
    if random.random() < 0.30 and not any(spun_title.startswith(p) for p in prefixes):
        spun_title = random.choice(prefixes) + spun_title
    return spun_title

def local_paraphrase(text_html):
    if not text_html:
        return text_html
    try:
        soup = BeautifulSoup(text_html, 'html.parser')
        paragraphs = soup.find_all('p')
        transition_phrases = [
            "According to recent reports, ",
            "Sources indicate that ",
            "In a recent development, ",
            "Industry observers note that ",
            "It has been revealed that ",
            "Recent findings suggest that "
        ]
        new_paragraphs = []
        for p in paragraphs:
            text = p.get_text().strip()
            if not text:
                continue
            text = apply_synonym_spin(text)
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
            if sentences and len(sentences) > 1 and random.random() < 0.55:
                first_sent = sentences[0]
                if not first_sent.startswith(tuple(transition_phrases)):
                    if len(first_sent) > 1 and not first_sent[:2].isupper():
                        first_sent = first_sent[0].lower() + first_sent[1:]
                    sentences[0] = random.choice(transition_phrases) + first_sent
            modified_text = ' '.join(sentences)
            if not modified_text.endswith(('.', '!', '?')):
                modified_text += '.'
            new_paragraphs.append(f"<p>{modified_text}</p>")
        return "\n".join(new_paragraphs) if new_paragraphs else text_html
    except Exception:
        return text_html

def detect_category(title):
    title_lower = str(title).lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', title_lower) for kw in keywords):
            return category
    return "General"

def detect_sentiment(title):
    title_lower = str(title).lower()
    positive_words = ['surge', 'jump', 'gain', 'growth', 'boost', 'success', 'win', 'positive', 'rise']
    warning_words = ['risk', 'threat', 'attack', 'crash', 'drop', 'warning', 'decline', 'vulnerability', 'lawsuit', 'issue']
    if any(w in title_lower for w in positive_words):
        return "📈 Bullish"
    elif any(w in title_lower for w in warning_words):
        return "⚠️ Warning"
    return "⚖️ Neutral"

def extract_source_domain(url):
    try:
        netloc = urlparse(str(url)).netloc.lower()
        netloc = re.sub(r'^www\.', '', netloc)
        parts = netloc.split('.')
        if len(parts) >= 2:
            name = parts[-2]
            special_names = {
                'theverge': 'The Verge',
                'techcrunch': 'TechCrunch',
                'arstechnica': 'Ars Technica',
                'engadget': 'Engadget',
                'wired': 'WIRED',
                'cnbc': 'CNBC',
                'bbc': 'BBC News',
                'cnn': 'CNN',
                'nytimes': 'NY Times',
                'reuters': 'Reuters',
                'bloomberg': 'Bloomberg',
                'huggingface': 'Hugging Face',
                'spring': 'PsyBlog',
                'delish': 'Delish'
            }
            return special_names.get(name, name.capitalize())
        return netloc or "News Source"
    except Exception:
        return "News Source"

def calculate_read_time(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        words = soup.get_text().split()
        word_count = len(words)
        minutes = max(1, round(word_count / 200))
        return minutes, word_count
    except Exception:
        return 1, 150

def get_depth_tier(word_count):
    if word_count < 150:
        return "⚡ Quick Read"
    elif word_count < 500:
        return "📖 Standard"
    return "📚 Deep Dive"

def generate_tldr(html_content):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        body_div = soup.find('div', class_='article-body')
        paragraphs = body_div.find_all('p') if body_div else soup.find_all('p')
        valid_sents = []
        for p in paragraphs:
            txt = p.get_text().strip()
            if len(txt) > 50 and "read original article" not in txt.lower():
                sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', txt) if len(s.strip()) > 35]
                if sents:
                    valid_sents.append(sents[0])
            if len(valid_sents) >= 3:
                break
        if len(valid_sents) < 2:
            return ""
        bullets = "".join([f"<li style='margin-bottom:6px;'>{s}</li>" for s in valid_sents[:3]])
        return f"""
        <div class="tldr-box" style="background: rgba(37, 99, 235, 0.08); border-left: 4px solid #2563eb; padding: 14px 18px; border-radius: 8px; margin: 18px 0;">
            <div style="font-weight: 700; margin-bottom: 8px; color: #2563eb; font-size: 14px;">💡 Key Takeaways</div>
            <ul style="margin: 0; padding-left: 18px; font-size: 14px; line-height: 1.6;">{bullets}</ul>
        </div>
        """
    except Exception:
        return ""

def generate_table_of_contents(html_content, artikel_id, is_standalone=False):
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        body_div = soup.find('div', class_='article-body')
        if not body_div:
            return html_content
        paragraphs = [p for p in body_div.find_all('p') if "read original article" not in p.get_text().lower()]
        if len(paragraphs) < 5:
            return html_content

        mid_idx = len(paragraphs) // 2
        paragraphs[0]['id'] = f"sec-start-{artikel_id}"
        paragraphs[mid_idx]['id'] = f"sec-mid-{artikel_id}"
        paragraphs[-1]['id'] = f"sec-end-{artikel_id}"

        onclick_attr = "" if is_standalone else f'onclick="jumpToSection(event, \'sec-start-{artikel_id}\')"'
        onclick_mid = "" if is_standalone else f'onclick="jumpToSection(event, \'sec-mid-{artikel_id}\')"'
        onclick_end = "" if is_standalone else f'onclick="jumpToSection(event, \'sec-end-{artikel_id}\')"'

        toc_html = f"""
        <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:12px 0 18px 0; padding:10px 14px; border:1px solid var(--border, #e2e8f0); border-radius:8px; font-size:13px;">
            <strong style="color:var(--text-muted, #64748b);">📑 Quick Jump:</strong>
            <a href="#sec-start-{artikel_id}" {onclick_attr} style="color:var(--primary, #2563eb); text-decoration:none; font-weight:600;">1. Overview</a>
            <span style="color:var(--text-muted, #64748b);">•</span>
            <a href="#sec-mid-{artikel_id}" {onclick_mid} style="color:var(--primary, #2563eb); text-decoration:none; font-weight:600;">2. Key Details</a>
            <span style="color:var(--text-muted, #64748b);">•</span>
            <a href="#sec-end-{artikel_id}" {onclick_end} style="color:var(--primary, #2563eb); text-decoration:none; font-weight:600;">3. Closing Notes</a>
        </div>
        """
        return toc_html + str(soup)
    except Exception:
        return html_content

def extract_trending_topics(chunk, top_n=10):
    word_counts = Counter()
    for row in chunk:
        words = re.findall(r'\b[A-Za-z]{4,}\b', str(row[1]))
        for w in words:
            wl = w.lower()
            if wl not in STOP_WORDS:
                word_counts[w.capitalize()] += 1
    top_words = [word for word, count in word_counts.most_common(top_n) if count >= 2]
    if not top_words:
        return ""
    tags_html = "".join([
        f'<button class="trend-tag" onclick="searchTopic(\'{w.lower()}\')">🔥 #{w}</button>'
        for w in top_words
    ])
    return f"""
    <div class="trending-bar">
        <span style="font-size:12px; font-weight:700; color:var(--text-muted); margin-right:4px;">POPULAR TAGS:</span>
        {tags_html}
    </div>
    """

def generate_breaking_ticker(chunk, limit=6):
    latest = sorted(chunk, key=lambda x: x[0], reverse=True)[:limit]
    if not latest:
        return ""
    links = "".join([
        f'<a class="ticker-item" href="{get_article_rel_url(row[1], row[0])}" style="text-decoration:none;">⚡ #{row[0]} {row[1]}</a>'
        for row in latest
    ])
    return f"""
    <div class="ticker-wrap">
        <div class="ticker-label">BREAKING</div>
        <div class="ticker-scroll">
            <div class="ticker-track">{links}{links}</div>
        </div>
    </div>
    """

def get_related_articles_links(current_id, current_title, all_rows, limit=4, is_inside_subdir=True):
    current_words = {w for w in re.findall(r'\b[a-z]{3,}\b', current_title.lower()) if w not in STOP_WORDS}
    scored = []
    for row in all_rows:
        r_id, r_judul = row[0], row[1]
        if r_id == current_id:
            continue
        r_words = {w for w in re.findall(r'\b[a-z]{3,}\b', r_judul.lower()) if w not in STOP_WORDS}
        overlap = len(current_words & r_words)
        scored.append((overlap, r_id, r_judul))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top_related = scored[:limit]
    if not top_related:
        return ""
    items_html = ""
    for _, r_id, r_judul in top_related:
        slug_href = create_slug(r_judul, r_id) if is_inside_subdir else get_article_rel_url(r_judul, r_id)
        items_html += f"""
        <a href="{slug_href}" style="display:block; width:100%; box-sizing:border-box; text-align:left; text-decoration:none; background:transparent; border:1px solid var(--border, #e2e8f0); padding:10px 14px; border-radius:8px; margin-bottom:8px; color:var(--text-main, #1e293b); font-size:14px; font-weight:600; transition:0.2s;">
            📰 #{r_id}. {r_judul}
        </a>
        """
    return f"""
    <div style="margin-top: 28px; padding-top: 18px; border-top: 1px dashed var(--border, #e2e8f0);">
        <h3 style="font-size: 16px; margin: 0 0 12px 0; color: var(--text-main, #1e293b);">🔗 Related Articles</h3>
        {items_html}
    </div>
    """

def generate_news_schema(chunk, page_url):
    item_list = []
    for idx, item in enumerate(chunk[:20], 1):
        artikel_id, judul, _, gambar, tanggal_publish, _, _ = item
        entry = {
            "@type": "ListItem",
            "position": idx,
            "item": {
                "@type": "NewsArticle",
                "headline": judul[:110],
                "url": get_article_abs_url(judul, artikel_id),
                "datePublished": tanggal_publish or datetime.now().isoformat(),
                "dateModified": tanggal_publish or datetime.now().isoformat(),
                "author": {"@type": "Person", "name": "Global News Desk Editor"},
                "publisher": {
                    "@type": "NewsMediaOrganization",
                    "name": "Global News Desk",
                    "logo": {"@type": "ImageObject", "url": f"{BASE_URL}/favicon.ico"}
                },
                "image": gambar if gambar else "https://via.placeholder.com/350x180/2563eb/ffffff?text=Global+News"
            }
        }
        item_list.append(entry)

    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": "Catalog", "item": page_url}
        ]
    }

    itemListSchema = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": item_list
    }
    return f'<script type="application/ld+json">{json.dumps(itemListSchema, ensure_ascii=False)}</script>\n<script type="application/ld+json">{json.dumps(breadcrumb, ensure_ascii=False)}</script>'

def generate_json_api_export(rows):
    api_dir = os.path.join(OUTPUT_DIR, "api")
    os.makedirs(api_dir, exist_ok=True)
    api_data = []
    for row in rows:
        artikel_id, judul, konten, gambar, tanggal_publish, tanggal_update, link = row
        soup = BeautifulSoup(konten, 'html.parser')
        plain_text = soup.get_text(" ", strip=True)
        api_data.append({
            "id": artikel_id,
            "title": judul,
            "url": get_article_abs_url(judul, artikel_id),
            "source_link": link,
            "image": gambar,
            "category": detect_category(judul),
            "sentiment": detect_sentiment(judul),
            "source": extract_source_domain(link),
            "published": tanggal_publish,
            "snippet": plain_text[:200]
        })
    with open(os.path.join(api_dir, "articles.json"), "w", encoding="utf-8") as f:
        json.dump(api_data, f, indent=2, ensure_ascii=False)

def generate_rss_feeds_per_category(rows):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now_rfc = format_datetime(datetime.now())

    latest_rows = sorted(rows, key=lambda x: x[0], reverse=True)[:30]
    items_xml = []
    for row in latest_rows:
        artikel_id, judul, konten, _, _, _, _ = row
        soup = BeautifulSoup(konten, 'html.parser')
        desc = soup.get_text(" ", strip=True)[:280]
        safe_title = judul.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_desc = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        item_url = get_article_abs_url(judul, artikel_id)
        items_xml.append(f"""
        <item>
            <title>{safe_title}</title>
            <link>{item_url}</link>
            <guid>{item_url}</guid>
            <description>{safe_desc}...</description>
        </item>""")

    main_rss = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>Portal Berita Interaktif - Latest News Feed</title>
    <link>{BASE_URL}/</link>
    <description>Daily curated technology, automotive, and business news.</description>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    {''.join(items_xml)}
</channel>
</rss>"""
    with open(os.path.join(OUTPUT_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(main_rss)

    for cat in CATEGORY_RULES.keys():
        cat_rows = [r for r in rows if detect_category(r[1]) == cat][:15]
        if not cat_rows:
            continue
        cat_items = []
        for row in cat_rows:
            artikel_id, judul, konten, _, _, _, _ = row
            soup = BeautifulSoup(konten, 'html.parser')
            desc = soup.get_text(" ", strip=True)[:280]
            safe_title = judul.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            safe_desc = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            item_url = get_article_abs_url(judul, artikel_id)
            cat_items.append(f"""
            <item>
                <title>{safe_title}</title>
                <link>{item_url}</link>
                <guid>{item_url}</guid>
                <description>{safe_desc}...</description>
            </item>""")

        cat_filename = f"feed-{cat.lower().replace(' & ', '-').replace(' ', '-')}.xml"
        cat_rss = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>Portal Berita - {cat}</title>
    <link>{BASE_URL}/</link>
    <description>Curated news feed for {cat}.</description>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    {''.join(cat_items)}
</channel>
</rss>"""
        with open(os.path.join(OUTPUT_DIR, cat_filename), "w", encoding="utf-8") as f:
            f.write(cat_rss)

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artikel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            judul TEXT,
            konten TEXT,
            gambar TEXT,
            tanggal_publish TEXT,
            tanggal_update TEXT
        )
    """)
    cursor.execute("DELETE FROM artikel WHERE id NOT IN (SELECT id FROM artikel ORDER BY id DESC LIMIT 1000)")
    conn.commit()

    cursor.execute("PRAGMA table_info(artikel)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'gambar' not in columns:
        cursor.execute("ALTER TABLE artikel ADD COLUMN gambar TEXT")
    if 'tanggal_publish' not in columns:
        cursor.execute("ALTER TABLE artikel ADD COLUMN tanggal_publish TEXT")
    if 'tanggal_update' not in columns:
        cursor.execute("ALTER TABLE artikel ADD COLUMN tanggal_update TEXT")
    conn.commit()
    conn.close()

def export_backend_reports(total_baru, total_duplikat):
    try:
        backup_path = os.path.join(OUTPUT_DIR, "database_backup.sqlite")
        shutil.copy(DB_PATH, backup_path)

        conn = sqlite3.connect(DB_PATH)
        conn.execute("VACUUM")
        conn.close()

        report = {
            "lastBuild": datetime.now().isoformat(),
            "totalNewArticles": total_baru,
            "totalDuplicates": total_duplikat,
            "status": "Success"
        }
        with open(os.path.join(OUTPUT_DIR, "scrape_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        manifest = {
            "name": "Portal Berita Interaktif Pro",
            "short_name": "SmartReader",
            "start_url": "index.html",
            "display": "standalone",
            "background_color": "#0f172a",
            "theme_color": "#2563eb",
            "icons": [{"src": "favicon.ico", "sizes": "64x64 32x32 24x24 16x16", "type": "image/x-icon"}]
        }
        with open(os.path.join(OUTPUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception:
        pass

def extract_full_content(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return None, None, None

        soup = BeautifulSoup(response.text, 'html.parser')

        image_url = None
        og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
        if og_image and og_image.get('content'):
            image_url = og_image['content']

        if not image_url:
            image_url = "https://via.placeholder.com/350x180/2563eb/ffffff?text=Global+News+Desk"

        tanggal = None
        for meta_prop in ['article:published_time', 'article:modified_time', 'datePublished', 'date']:
            meta = soup.find('meta', property=meta_prop) or soup.find('meta', attrs={'name': meta_prop})
            if meta and meta.get('content'):
                tanggal = meta['content']
                break

        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()

        content_container = soup.find('article') or soup.find('div', class_=re.compile('content|post|article|body|entry', re.I))

        paragraphs = []
        if content_container:
            paragraphs = content_container.find_all(['p', 'h2', 'h3', 'li'])
        if not paragraphs:
            paragraphs = soup.find_all('p')

        full_text = []
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 30 and not any(x in text.lower() for x in ['cookie', 'privacy policy', 'subscribe', 'all rights reserved', 'terms of use', 'sign up', 'log in', 'copyright']):
                if p.name in ['h2', 'h3']:
                    full_text.append(f"<{p.name} style='margin-top:16px; margin-bottom:8px;'>{text}</{p.name}>")
                elif p.name == 'li':
                    full_text.append(f"<li>{text}</li>")
                else:
                    full_text.append(f"<p>{text}</p>")

        if not full_text:
            return None, image_url, tanggal

        return "\n".join(full_text), image_url, tanggal
    except Exception:
        return None, None, None

def process_single_feed(feed_url):
    feed_url = feed_url.strip()
    if not feed_url or feed_url.startswith("#"):
        return 0, 0
    baru = 0
    duplikat = 0
    try:
        feed = feedparser.parse(feed_url)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        cursor = conn.cursor()
        for entry in feed.entries:
            link = entry.get('link', '')
            raw_judul = entry.get('title', 'No Title')
            if not link:
                continue
            cursor.execute("SELECT id FROM artikel WHERE link = ?", (link,))
            if cursor.fetchone():
                duplikat += 1
                continue
            full_text_html, image_url, tanggal = extract_full_content(link)
            if not full_text_html:
                summary = entry.get('summary', entry.get('description', 'Content not available.'))
                full_text_html = f"<p>{summary}</p>"

            judul = paraphrase_title(raw_judul)
            print(f"[*] Paraphrasing (EN): {judul[:45]}...")
            full_text_html = local_paraphrase(full_text_html)

            if not tanggal:
                published_parsed = entry.get('published_parsed')
                if published_parsed:
                    tanggal = datetime(*published_parsed[:6]).isoformat()
                else:
                    tanggal = datetime.now().isoformat()
            tanggal_update = datetime.now().isoformat()
            img_tag = f'<div style="text-align:center; margin-bottom:20px;"><img src="{image_url}" alt="{judul}" style="max-width:100%; height:auto; border-radius:8px;" loading="lazy"></div>' if image_url else ""
            konten_lengkap = f"""
            {img_tag}
            {ADSTERRA_CODE}
            <div class="article-body" style="line-height: 1.8;">
                {full_text_html}
                <p style="margin-top: 25px;">
                    <a href="{link}" target="_blank" rel="nofollow noopener" style="color: #0d6efd; text-decoration: none; font-weight: bold;">
                        &rarr; Read original article
                    </a>
                </p>
            </div>
            {ADSTERRA_CODE}
            """
            try:
                cursor.execute(
                    "INSERT INTO artikel (link, judul, konten, gambar, tanggal_publish, tanggal_update) VALUES (?, ?, ?, ?, ?, ?)",
                    (link, judul, konten_lengkap, image_url, tanggal, tanggal_update)
                )
                conn.commit()
                baru += 1
            except sqlite3.IntegrityError:
                duplikat += 1
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return baru, duplikat

def generate_standalone_article_pages(rows):
    artikel_path_dir = os.path.join(OUTPUT_DIR, ARTIKEL_DIR)
    os.makedirs(artikel_path_dir, exist_ok=True)
    generated_urls = []

    for item in rows:
        artikel_id, judul, konten, gambar, tanggal_publish, tanggal_update, link = item
        slug_filename = create_slug(judul, artikel_id)
        file_path = os.path.join(artikel_path_dir, slug_filename)
        abs_url = get_article_abs_url(judul, artikel_id)
        generated_urls.append(abs_url)

        safe_title = str(judul).replace('"', '&quot;')
        soup_desc = BeautifulSoup(konten, 'html.parser')
        clean_desc = re.sub(r'\s+', ' ', soup_desc.get_text(" ", strip=True))[:155].replace('"', '&quot;')
        img_thumb = gambar if gambar else "https://via.placeholder.com/600x320/2563eb/ffffff?text=Global+News"
        category = detect_category(judul)
        sentiment = detect_sentiment(judul)
        source_name = extract_source_domain(link)
        read_mins, word_count = calculate_read_time(konten)
        depth_tier = get_depth_tier(word_count)
        tldr_box = generate_tldr(konten)
        konten_with_toc = generate_table_of_contents(konten, artikel_id, is_standalone=True)
        related_box = get_related_articles_links(artikel_id, judul, rows, limit=4, is_inside_subdir=True)

        try:
            tgl_pub = datetime.fromisoformat(tanggal_publish.replace('Z', '+00:00')).strftime('%d %b %Y') if tanggal_publish else '-'
        except Exception:
            tgl_pub = tanggal_publish[:10] if tanggal_publish else '-'

        article_schema = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": judul[:110],
            "image": [img_thumb],
            "datePublished": tanggal_publish or datetime.now().isoformat(),
            "dateModified": tanggal_update or datetime.now().isoformat(),
            "author": {"@type": "Person", "name": "Global News Desk Editor"},
            "publisher": {
                "@type": "NewsMediaOrganization",
                "name": "Global News Desk",
                "logo": {"@type": "ImageObject", "url": f"{BASE_URL}/favicon.ico"}
            },
            "mainEntityOfPage": {"@type": "WebPage", "@id": abs_url}
        }

        html_single = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title}</title>
    <meta name="description" content="{clean_desc}">
    <link rel="canonical" href="{abs_url}">
    <meta property="og:title" content="{safe_title}">
    <meta property="og:description" content="{clean_desc}">
    <meta property="og:image" content="{img_thumb}">
    <meta property="og:url" content="{abs_url}">
    <meta property="og:type" content="article">
    <script type="application/ld+json">{json.dumps(article_schema, ensure_ascii=False)}</script>
    <style>
        :root {{
            --primary: #2563eb;
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --badge-bg: #eff6ff;
            --badge-text: #1d4ed8;
        }}
        body.dark-mode {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --border: #334155;
            --badge-bg: #1e3a8a;
            --badge-text: #93c5fd;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
            line-height: 1.8;
        }}
        .article-wrapper {{
            max-width: 840px;
            margin: 0 auto;
            background: var(--card-bg);
            padding: 32px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.06);
        }}
        .top-nav {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 16px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            gap: 10px;
        }}
        .back-link {{
            text-decoration: none;
            color: var(--primary);
            font-weight: 700;
            font-size: 14px;
        }}
        .cat-badge {{
            background: var(--badge-bg);
            color: var(--badge-text);
            font-size: 12px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
        }}
        h1 {{
            font-size: 28px;
            line-height: 1.35;
            margin: 10px 0 14px 0;
        }}
        .meta-line {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 20px;
        }}
        .ctrl-btn {{
            background: var(--bg-color);
            color: var(--text-main);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
        }}
        @media (max-width: 600px) {{
            body {{ padding: 0; }}
            .article-wrapper {{ border-radius: 0; padding: 20px; }}
            h1 {{ font-size: 22px; }}
        }}
    </style>
</head>
<body>
    <div class="article-wrapper">
        <div class="top-nav">
            <a href="../index.html" class="back-link">&larr; Back to Portal Home</a>
            <div style="display:flex; gap:8px; align-items:center;">
                <span class="cat-badge">{category}</span>
                <span class="cat-badge" style="background:#fef3c7; color:#d97706;">{sentiment}</span>
                <button class="ctrl-btn" onclick="document.body.classList.toggle('dark-mode')">🌙 Dark Mode</button>
            </div>
        </div>
        <h1>{judul}</h1>
        <div class="meta-line">🌐 Source: <strong>{source_name}</strong> &bull; 📅 {tgl_pub} &bull; ⏱️ {read_mins} min read ({word_count} words, {depth_tier})</div>
        {tldr_box}
        {konten_with_toc}
        {related_box}
    </div>
</body>
</html>"""

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_single)

    print(f"✅ Halaman Artikel Tunggal: {len(generated_urls)} file HTML dibuat di folder /{ARTIKEL_DIR}/")
    return generated_urls

def generate_sitemap_and_robots(total_baru, total_duplikat, total_pages, single_article_urls):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, judul, konten, gambar, tanggal_publish, tanggal_update, link FROM artikel ORDER BY id DESC")
    articles = cursor.fetchall()
    conn.close()

    today = datetime.now().strftime('%Y-%m-%d')
    sitemap_path = os.path.join(OUTPUT_DIR, "sitemap.xml")
    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'  <url><loc>{BASE_URL}/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>',
        f'  <url><loc>{BASE_URL}/index.html</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>',
        f'  <url><loc>{BASE_URL}/feed.xml</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>'
    ]

    for p in range(2, total_pages + 1):
        xml_parts.append(
            f'  <url><loc>{BASE_URL}/page{p}.html</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>'
        )

    for art_url in single_article_urls:
        xml_parts.append(
            f'  <url><loc>{art_url}</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>'
        )

    xml_parts.append('</urlset>')

    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_parts))

    robots_path = os.path.join(OUTPUT_DIR, "robots.txt")
    robots_content = (
        f"User-agent: *\n"
        f"Allow: /\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
        f"Sitemap: {BASE_URL}/feed.xml\n"
        f"Crawl-delay: 1\n"
    )
    with open(robots_path, "w", encoding="utf-8") as f:
        f.write(robots_content)

    generate_rss_feeds_per_category(articles)
    generate_json_api_export(articles)
    export_backend_reports(total_baru, total_duplikat)
    print(f"✅ Sitemap XML diperbarui: Total {len(single_article_urls) + total_pages + 2} URL siap diindeks GSC.")

def export_paginated_files():
    if not os.path.exists(DB_PATH):
        return 0, []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, judul, konten, gambar, tanggal_publish, tanggal_update, link FROM artikel ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return 0, []

    single_article_urls = generate_standalone_article_pages(rows)

    total_articles = len(rows)
    total_pages = max(1, math.ceil(total_articles / ITEMS_PER_PAGE))

    all_categories = ["All", "AI & Tech", "Hardware & Chips", "EV & Auto", "Gadgets", "Business & Policy", "General"]

    cat_counts = {"All": total_articles}
    for row in rows:
        c = detect_category(row[1])
        cat_counts[c] = cat_counts.get(c, 0) + 1

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        filename = "index.html" if page_num == 1 else f"page{page_num}.html"
        chunk = rows[page_idx * ITEMS_PER_PAGE : (page_idx + 1) * ITEMS_PER_PAGE]
        part_name = "Halaman Utama (Update Terkini)" if page_num == 1 else f"Halaman {page_num} (Arsip Berita)"
        next_page_file = f"page{page_num + 1}.html" if page_num < total_pages else "index.html"
        next_page_label = f"Halaman {page_num + 1}" if page_num < total_pages else "Halaman Utama"

        output_file = os.path.join(OUTPUT_DIR, filename)
        page_url = f"{BASE_URL}/{filename}"
        sample_titles = ", ".join([row[1][:50] for row in chunk[:5]])
        meta_desc = f"Katalog berita interaktif: {sample_titles}. Update harian dengan informasi terpercaya."[:160]

        schema_script = generate_news_schema(chunk, page_url)
        ticker_html = generate_breaking_ticker(chunk, limit=6)
        trending_html = extract_trending_topics(chunk, top_n=10)

        cat_buttons_html = ""
        for cat in all_categories:
            active_cls = "active" if cat == "All" else ""
            cnt = cat_counts.get(cat, 0)
            cat_buttons_html += f'<button class="cat-btn {active_cls}" onclick="filterByCategory(\'{cat}\', this)">{cat} ({cnt})</button>\n'
        cat_buttons_html += '<button class="cat-btn" id="savedTabBtn" onclick="filterByCategory(\'SAVED\', this)">🔖 Saved (<span id="savedCount">0</span>)</button>\n'
        cat_buttons_html += '<button class="cat-btn" id="historyTabBtn" onclick="filterByCategory(\'HISTORY\', this)">🕒 History</button>\n'
        cat_buttons_html += '<button class="cat-btn" id="popularTabBtn" onclick="filterByCategory(\'POPULAR\', this)">🔥 Popular</button>\n'

        pagination_links = []
        for p in range(1, total_pages + 1):
            p_file = "index.html" if p == 1 else f"page{p}.html"
            active_cls = "active" if p == page_num else ""
            pagination_links.append(f'<a href="{p_file}" class="{active_cls}">Halaman {p}</a>')

        html_parts = []
        html_parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Portal Berita Interaktif - {part_name}</title>
    <meta name="description" content="{meta_desc}">
    <link rel="manifest" href="manifest.json">
    <link rel="alternate" type="application/rss+xml" title="RSS Feed" href="feed.xml">
    {schema_script}
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <style>
        :root {{
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --badge-bg: #eff6ff;
            --badge-text: #1d4ed8;
            --font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            --line-height: 1.8;
        }}
        body.dark-mode {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --border: #334155;
            --badge-bg: #1e3a8a;
            --badge-text: #93c5fd;
        }}
        body.sepia-mode {{
            --bg-color: #f4ecd8;
            --card-bg: #fdfbf7;
            --text-main: #5c4033;
            --text-muted: #8c6d62;
            --border: #e0d0b8;
            --badge-bg: #faebd7;
            --badge-text: #8b4513;
        }}
        body.oled-mode {{
            --bg-color: #000000;
            --card-bg: #121212;
            --text-main: #e0e0e0;
            --text-muted: #aaaaaa;
            --border: #222222;
            --badge-bg: #1f1f1f;
            --badge-text: #bb86fc;
        }}
        body.font-serif {{ --font-family: Georgia, Cambria, 'Times New Roman', Times, serif; }}
        body.font-mono {{ --font-family: Consolas, Monaco, monospace; }}
        body.spacing-relaxed .article-body {{ line-height: 2.2 !important; }}
        body.spacing-compact .article-body {{ line-height: 1.4 !important; }}

        mark {{ background-color: #fef08a; color: inherit; padding: 2px 4px; border-radius: 4px; }}
        body.dark-mode mark {{ background-color: #854d0e; color: #fef9c3; }}

        body.zen-mode .header-box, body.zen-mode .ticker-wrap, body.zen-mode .card-actions, body.zen-mode .pagination, body.zen-mode .close-modal {{
            display: none !important;
        }}
        body.zen-mode .modal-content {{
            max-width: 950px !important;
            font-size: 19px !important;
        }}
        body {{
            font-family: var(--font-family);
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
            transition: background-color 0.25s, color 0.25s;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .ticker-wrap {{
            display: flex;
            align-items: center;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 18px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.04);
        }}
        .ticker-label {{
            background: #dc2626;
            color: #fff;
            font-weight: 800;
            font-size: 12px;
            padding: 10px 14px;
            letter-spacing: 0.6px;
            flex-shrink: 0;
        }}
        .ticker-scroll {{
            overflow: hidden;
            white-space: nowrap;
            width: 100%;
            padding: 8px 0;
        }}
        .ticker-track {{
            display: inline-block;
            animation: tickerMove 35s linear infinite;
        }}
        .ticker-track:hover {{
            animation-play-state: paused;
        }}
        .ticker-item {{
            display: inline-block;
            margin-right: 36px;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-main);
            cursor: pointer;
        }}
        .ticker-item:hover {{
            color: var(--primary);
        }}
        @keyframes tickerMove {{
            0% {{ transform: translateX(0); }}
            100% {{ transform: translateX(-50%); }}
        }}
        .header-box {{
            background: var(--card-bg);
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
            text-align: center;
            margin-bottom: 30px;
            position: relative;
        }}
        .top-controls {{
            display: flex;
            justify-content: flex-end;
            gap: 8px;
            margin-bottom: 12px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .ctrl-btn {{
            background: var(--bg-color);
            color: var(--text-main);
            border: 1px solid var(--border);
            padding: 7px 12px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
        }}
        .ctrl-btn:hover {{
            border-color: var(--primary);
        }}
        .header-box h1 {{
            margin: 0 0 8px 0;
            font-size: 28px;
            color: var(--text-main);
        }}
        .search-row {{
            display: flex;
            gap: 10px;
            max-width: 650px;
            margin: 18px auto 0 auto;
            flex-wrap: wrap;
            align-items: center;
        }}
        .search-row input {{
            flex: 1;
            min-width: 220px;
            padding: 12px 18px;
            border: 2px solid var(--border);
            border-radius: 8px;
            font-size: 15px;
            background: var(--bg-color);
            color: var(--text-main);
            outline: none;
        }}
        .search-row input:focus {{
            border-color: var(--primary);
        }}
        .search-row select {{
            padding: 12px 14px;
            border: 2px solid var(--border);
            border-radius: 8px;
            font-size: 14px;
            background: var(--bg-color);
            color: var(--text-main);
            font-weight: 600;
            cursor: pointer;
        }}
        .mic-btn {{
            background: var(--primary);
            color: white;
            border: none;
            padding: 12px 16px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
        }}
        .trending-bar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            align-items: center;
            gap: 6px;
            margin-top: 14px;
        }}
        .trend-tag {{
            background: transparent;
            border: 1px dashed var(--border);
            color: var(--text-muted);
            padding: 4px 10px;
            border-radius: 14px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .trend-tag:hover {{
            color: var(--primary);
            border-color: var(--primary);
        }}
        .category-bar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 8px;
            margin-top: 16px;
        }}
        .cat-btn {{
            background: var(--bg-color);
            color: var(--text-main);
            border: 1px solid var(--border);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: 0.2s;
        }}
        .cat-btn.active, .cat-btn:hover {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}
        .grid-container {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }}
        .article-card {{
            background: var(--card-bg);
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
            padding: 20px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: transform 0.2s, box-shadow 0.2s;
            position: relative;
        }}
        .article-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.12);
        }}
        .article-card img {{
            width: 100%;
            height: 180px;
            object-fit: cover;
            border-radius: 8px;
            margin-bottom: 12px;
        }}
        .badge-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
            gap: 6px;
        }}
        .cat-badge {{
            display: inline-block;
            background: var(--badge-bg);
            color: var(--badge-text);
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            cursor: pointer;
        }}
        .source-badge {{
            font-size: 11px;
            font-weight: 700;
            color: var(--text-muted);
            background: var(--bg-color);
            border: 1px solid var(--border);
            padding: 3px 8px;
            border-radius: 6px;
        }}
        .article-card h2 {{
            font-size: 18px;
            margin: 0 0 10px 0;
            color: var(--text-main);
            line-height: 1.4;
        }}
        .article-card h2 a {{
            color: inherit;
            text-decoration: none;
        }}
        .article-card h2 a:hover {{
            color: var(--primary);
        }}
        .article-meta {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 15px;
        }}
        .card-actions {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 6px;
            border-top: 1px solid var(--border);
            padding-top: 15px;
            margin-top: auto;
            flex-wrap: wrap;
        }}
        .btn {{
            background-color: var(--primary);
            color: white;
            border: none;
            padding: 8px 11px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .btn:hover {{
            background-color: var(--primary-dark);
        }}
        .btn-save {{
            background-color: #f59e0b;
        }}
        .btn-save.saved {{
            background-color: #7c3aed;
        }}
        .btn-share {{
            background-color: #10b981;
        }}
        .btn-pdf {{
            background-color: #ef4444;
        }}
        .pagination {{
            text-align: center;
            margin: 40px 0;
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .pagination a {{
            display: inline-block;
            padding: 10px 18px;
            background: var(--card-bg);
            border-radius: 6px;
            text-decoration: none;
            color: var(--text-main);
            font-weight: bold;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }}
        .pagination a.active {{
            background: var(--primary);
            color: white;
        }}
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.65);
            justify-content: center;
            align-items: center;
            padding: 20px;
            box-sizing: border-box;
        }}
        .modal-content {{
            background: var(--card-bg);
            color: var(--text-main);
            width: 100%;
            max-width: 820px;
            max-height: 88vh;
            border-radius: 12px;
            padding: 0 30px 30px 30px;
            overflow-y: auto;
            position: relative;
            font-size: 16px;
        }}
        .modal-sticky-bar {{
            position: sticky;
            top: 0;
            background: var(--card-bg);
            z-index: 20;
            padding: 12px 0 10px 0;
            border-bottom: 1px solid var(--border);
            margin-bottom: 16px;
        }}
        .progress-track {{
            width: 100%;
            height: 4px;
            background: var(--border);
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 10px;
        }}
        .progress-fill {{
            height: 100%;
            width: 0%;
            background: var(--primary);
            transition: width 0.1s;
        }}
        .modal-toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .close-modal {{
            font-size: 26px;
            font-weight: bold;
            cursor: pointer;
            color: var(--text-muted);
            line-height: 1;
        }}
        .social-share-bar {{
            display: flex;
            gap: 8px;
            margin: 15px 0;
            flex-wrap: wrap;
        }}
        .share-pill {{
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            color: #fff;
            text-decoration: none;
            cursor: pointer;
            border: none;
        }}
        .back-to-top {{
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: var(--primary);
            color: #fff;
            border: none;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            display: none;
            z-index: 900;
        }}
        #floatingNotePopup {{
            display: none;
            position: absolute;
            z-index: 1500;
            background: #1e293b;
            color: #fff;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            box-shadow: 0 4px 6px rgba(0,0,0,0.2);
        }}
    </style>
</head>
<body>
    <div id="floatingNotePopup" onclick="quickSaveHighlightToNotes()">📝 Simpan ke Catatan</div>
    <div class="container">
        {ticker_html}
        <div class="header-box">
            <div class="top-controls">
                <span id="streakBadge" style="font-size:12px; font-weight:700; color:var(--primary); background:var(--badge-bg); padding:6px 10px; border-radius:8px;">🔥 Streak: 1 Days</span>
                <button class="ctrl-btn" onclick="openAnalyticsModal()">📊 Analytics</button>
                <button class="ctrl-btn" onclick="openRandomArticle()">🎲 Surprise Me</button>
                <button class="ctrl-btn" onclick="exportHistoryJSON()">💾 Export History</button>
                <input type="color" id="customColorPicker" title="Pilih Warna Tema Kustom" onchange="applyCustomColor(this.value)" style="width:32px; height:32px; border:none; cursor:pointer; background:none; vertical-align:middle;">
                <a href="feed.xml" target="_blank" class="ctrl-btn" style="text-decoration:none;">📡 RSS</a>
                <button class="ctrl-btn" id="themeBtn" onclick="cycleThemeMode()">🌙 Theme</button>
            </div>
            <h1>Katalog Berita & Artikel Interaktif - {part_name}</h1>
            <p style="color:var(--text-muted); margin:0;">Halaman {page_num} dari {total_pages} • Total {total_articles} artikel tersedia dalam direktori berita harian.</p>
            <div class="search-row">
                <input type="text" id="searchInput" placeholder="Cari judul, kata kunci, atau source:nama..." onkeyup="filterArticles()">
                <button class="mic-btn" onclick="startVoiceSearch()" title="Cari dengan suara">🎙️</button>
                <select id="sortOrder" onchange="sortArticles()">
                    <option value="newest">⬇️ Terbaru (Newest)</option>
                    <option value="oldest">⬆️ Terlama (Oldest)</option>
                </select>
            </div>
            {trending_html}
            <div class="category-bar">
                {cat_buttons_html}
            </div>
            <div style="margin-top: 15px;">
                <a href="{next_page_file}" style="color: var(--primary); text-decoration: none; font-weight: bold;">📄 Beralih ke {next_page_label} →</a>
            </div>
        </div>

        <div class="grid-container" id="articleGrid">""")

        for item in chunk:
            artikel_id, judul, konten, gambar, tanggal_publish, tanggal_update, link = item
            safe_title = str(judul).replace('"', '').replace("'", '').replace('/', '_').replace('\\', '_')
            rel_article_url = get_article_rel_url(judul, artikel_id)
            img_thumb = gambar if gambar else "https://via.placeholder.com/350x180/2563eb/ffffff?text=Global+News"
            category = detect_category(judul)
            sentiment = detect_sentiment(judul)
            source_name = extract_source_domain(link)
            read_mins, word_count = calculate_read_time(konten)
            depth_tier = get_depth_tier(word_count)
            tldr_box = generate_tldr(konten)
            konten_with_toc = generate_table_of_contents(konten, artikel_id, is_standalone=False)
            related_box = get_related_articles_links(artikel_id, judul, rows, limit=3, is_inside_subdir=False)

            try:
                tgl_pub = datetime.fromisoformat(tanggal_publish.replace('Z', '+00:00')).strftime('%d %b %Y') if tanggal_publish else '-'
            except Exception:
                tgl_pub = tanggal_publish[:10] if tanggal_publish else '-'

            html_parts.append(f"""
            <div class="article-card" id="artikel-{artikel_id}" data-id="{artikel_id}" data-url="{rel_article_url}" data-title="{judul.lower()}" data-category="{category}" data-source="{source_name.lower()}">
                <div>
                    <a href="{rel_article_url}" style="text-decoration:none;">
                        <img src="{img_thumb}" alt="{judul}" loading="lazy">
                    </a>
                    <div class="badge-row">
                        <div>
                            <span class="cat-badge" onclick="filterByCategory('{category}', null)">{category}</span>
                            <span class="cat-badge" style="background:#fef3c7; color:#d97706; margin-left:4px;">{sentiment}</span>
                        </div>
                        <span class="source-badge">🌐 {source_name}</span>
                    </div>
                    <h2><a href="{rel_article_url}">#{artikel_id}. {judul}</a></h2>
                    <div class="article-meta">📅 {tgl_pub} • ⏱️ {read_mins}m • {depth_tier} ({word_count} w)</div>
                </div>
                <div class="card-actions">
                    <a class="btn" href="{rel_article_url}">📖 Baca</a>
                    <button class="btn" style="background:#334155;" onclick="openModal({artikel_id})">👁️ Quick</button>
                    <button class="btn btn-save" id="save-btn-{artikel_id}" onclick="toggleBookmark({artikel_id})">🔖 Save</button>
                    <button class="btn btn-share" onclick="copyShareLink('{rel_article_url}', this)">🔗 Share</button>
                    <button class="btn btn-pdf" onclick="downloadPDF('content-{artikel_id}', '{safe_title}')">📄 PDF</button>
                </div>
                <div id="content-{artikel_id}" style="display:none;" data-raw-title="{safe_title}">
                    <div class="badge-row" style="justify-content:flex-start;">
                        <span class="cat-badge" onclick="filterByCategory('{category}', null); closeModal();">{category}</span>
                        <span class="cat-badge" style="background:#fef3c7; color:#d97706; margin-left:4px;">{sentiment}</span>
                        <span class="source-badge">🌐 {source_name}</span>
                        <a href="{rel_article_url}" class="cat-badge" style="background:#10b981; color:#fff; text-decoration:none;">↗ Open Full Page</a>
                    </div>
                    <h2 style="margin-top:8px;">{judul}</h2>
                    <p style="font-size:12px; color:var(--text-muted);">📅 {tgl_pub} | ⏱️ {read_mins} min read ({word_count} words, {depth_tier}) | Source: {source_name}</p>
                    <div class="social-share-bar">
                        <button class="share-pill" style="background:#25d366;" onclick="shareToSocial('whatsapp', '{safe_title}', '{rel_article_url}')">💬 WhatsApp</button>
                        <button class="share-pill" style="background:#000000;" onclick="shareToSocial('twitter', '{safe_title}', '{rel_article_url}')">✖ X / Twitter</button>
                        <button class="share-pill" style="background:#1877f2;" onclick="shareToSocial('facebook', '{safe_title}', '{rel_article_url}')">f Facebook</button>
                        <button class="share-pill" style="background:#229ed9;" onclick="shareToSocial('telegram', '{safe_title}', '{rel_article_url}')">✈ Telegram</button>
                        <button class="share-pill" style="background:#64748b;" onclick="copyFullArticleText('content-{artikel_id}', this)">📋 Copy Text</button>
                        <button class="share-pill" style="background:#0f172a;" onclick="copyMarkdownText('{safe_title}', 'content-{artikel_id}', this)">📝 Markdown</button>
                        <button class="share-pill" style="background:#0d6efd;" onclick="speakArticle('content-{artikel_id}', this)">🔊 Listen</button>
                        <button class="share-pill" style="background:#475569;" onclick="printArticle('content-{artikel_id}')">🖨️ Print</button>
                        <button class="share-pill" style="background:#7c3aed;" onclick="toggleAutoScroll(this)">📜 Auto-Scroll</button>
                        <button class="share-pill" style="background:#d97706;" onclick="copyCitation('{safe_title}', '{source_name}', '{tgl_pub}', this)">📌 Cite</button>
                        <button class="share-pill" style="background:#0284c7;" onclick="showQRCode({artikel_id}, '{rel_article_url}')">📱 QR Code</button>
                    </div>
                    <div id="qrcode-{artikel_id}" style="display:none; text-align:center; margin:15px 0; padding:10px; background:#fff; border-radius:8px; width:fit-content; margin-left:auto; margin-right:auto;"></div>
                    
                    <div style="margin: 15px 0; padding: 10px; background: var(--bg-color); border: 1px solid var(--border); border-radius: 8px;">
                        <textarea id="notes-{artikel_id}" placeholder="Tulis catatan pribadi atau highlight teks artikel di sini..." style="width: 100%; height: 60px; background: transparent; border: none; color: var(--text-main); resize: vertical; outline: none; font-family: inherit;" oninput="savePersonalNote({artikel_id})"></textarea>
                    </div>

                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <div id="timeRemaining-{artikel_id}" style="font-size:12px; font-weight:600; color:var(--primary);">⏱️ ~{read_mins} mins left to finish</div>
                        <div id="readPct-{artikel_id}" style="font-size:12px; font-weight:700; color:var(--text-muted);">📖 0% read</div>
                    </div>
                    <hr style="border:0; border-top:1px solid var(--border); margin:15px 0;">
                    {tldr_box}
                    {konten_with_toc}
                    {related_box}
                </div>
            </div>""")

        html_parts.append(f"""
        </div>
        <div class="pagination">
            {''.join(pagination_links)}
        </div>
    </div>

    <button class="back-to-top" id="backToTopBtn" onclick="window.scrollTo({{top: 0, behavior: 'smooth'}})">↑</button>

    <div id="articleModal" class="modal">
        <div class="modal-content" id="modalContainer" onscroll="onModalScroll()">
            <div class="modal-sticky-bar">
                <div class="progress-track"><div class="progress-fill" id="readingProgressBar"></div></div>
                <div class="modal-toolbar">
                    <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                        <span style="font-size:12px; color:var(--text-muted); font-weight:600;">Font:</span>
                        <button class="ctrl-btn" style="padding:4px 8px;" onclick="changeModalFont(-1)">A-</button>
                        <button class="ctrl-btn" style="padding:4px 8px;" onclick="changeModalFont(1)">A+</button>
                        <button class="ctrl-btn" style="padding:4px 8px;" onclick="cycleFontFamily()">🔤 Style</button>
                        <button class="ctrl-btn" style="padding:4px 8px;" onclick="cycleLineSpacing()">📏 Spacing</button>
                        <button class="ctrl-btn" style="padding:4px 8px;" onclick="cycleWpmSpeed()">⚡ WPM (<span id="wpmLabel">200</span>)</button>
                        <button class="ctrl-btn" style="padding:4px 8px;" onclick="toggleZenMode()" id="zenBtn">🧘 Zen</button>
                    </div>
                    <span class="close-modal" onclick="closeModal()">&times;</span>
                </div>
            </div>
            <div id="modalBody" onmouseup="handleTextHighlight(event)"></div>
        </div>
    </div>

    <div id="analyticsModal" class="modal">
        <div class="modal-content" style="max-width: 480px; padding: 30px;">
            <h3>📊 Reading Analytics Dashboard</h3>
            <div id="analyticsContent" style="margin: 20px 0; font-size: 15px; line-height: 1.8;">
                <p><b>Total Artikel Dibaca:</b> <span id="statReadCount">0</span></p>
                <p><b>Total Bookmark Tersimpan:</b> <span id="statSavedCount">0</span></p>
                <p><b>Streak Membaca:</b> <span id="statStreak">1</span> Hari</p>
            </div>
            <button class="ctrl-btn" onclick="closeAnalyticsModal()" style="width:100%; padding: 10px;">Tutup</button>
        </div>
    </div>

    <script>
        let currentCategory = 'All';
        let modalFontSize = 16;
        let activeReadingId = null;
        let fontStyles = ['sans', 'serif', 'mono'];
        let currentFontIdx = 0;
        let spacingStyles = ['normal', 'relaxed', 'compact'];
        let currentSpacingIdx = 0;
        let wpmSpeeds = [150, 200, 300];
        let currentWpmIdx = 1;
        let autoScrollInterval = null;
        let selectedTextRange = null;

        function getSavedIds() {{
            try {{ return JSON.parse(localStorage.getItem('savedArticles') || '[]'); }} catch (e) {{ return []; }}
        }}

        function getHistoryIds() {{
            try {{ return JSON.parse(localStorage.getItem('readHistory') || '[]'); }} catch (e) {{ return []; }}
        }}

        function recordClick(id) {{
            let pop = JSON.parse(localStorage.getItem('popularClicks') || '{{}}');
            pop[id] = (pop[id] || 0) + 1;
            localStorage.setItem('popularClicks', JSON.stringify(pop));
        }}

        function recordHistory(id) {{
            let hist = getHistoryIds().filter(x => x !== id);
            hist.unshift(id);
            if (hist.length > 50) hist.pop();
            localStorage.setItem('readHistory', JSON.stringify(hist));
        }}

        function exportHistoryJSON() {{
            let data = {{
                saved: getSavedIds(),
                history: getHistoryIds(),
                exportedAt: new Date().toISOString()
            }};
            let blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
            let url = URL.createObjectURL(blob);
            let a = document.createElement('a');
            a.href = url;
            a.download = 'reader_history_backup.json';
            a.click();
        }}

        function openAnalyticsModal() {{
            document.getElementById('statReadCount').innerText = getHistoryIds().length;
            document.getElementById('statSavedCount').innerText = getSavedIds().length;
            document.getElementById('statStreak').innerText = localStorage.getItem('readerStreak') || '1';
            document.getElementById('analyticsModal').style.display = 'flex';
        }}

        function closeAnalyticsModal() {{
            document.getElementById('analyticsModal').style.display = 'none';
        }}

        function applyCustomColor(color) {{
            document.documentElement.style.setProperty('--primary', color);
            localStorage.setItem('customPrimaryColor', color);
        }}

        let savedColor = localStorage.getItem('customPrimaryColor');
        if (savedColor) {{
            document.documentElement.style.setProperty('--primary', savedColor);
            let picker = document.getElementById('customColorPicker');
            if (picker) picker.value = savedColor;
        }}

        function startVoiceSearch() {{
            if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {{
                alert('Pencarian suara tidak didukung di browser ini.');
                return;
            }}
            let SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            let recognition = new SpeechRecognition();
            recognition.lang = 'en-US';
            recognition.onresult = function(event) {{
                let speechText = event.results[0][0].transcript;
                document.getElementById('searchInput').value = speechText;
                filterArticles();
            }};
            recognition.start();
        }}

        function checkStreak() {{
            let lastVisit = localStorage.getItem('lastVisitDate');
            let streak = parseInt(localStorage.getItem('readerStreak') || '1');
            let today = new Date().toISOString().slice(0, 10);
            if (lastVisit !== today) {{
                if (lastVisit) {{
                    let d1 = new Date(lastVisit);
                    let d2 = new Date(today);
                    let diffTime = Math.abs(d2 - d1);
                    let diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
                    if (diffDays === 1) streak += 1;
                    else if (diffDays > 1) streak = 1;
                }}
                localStorage.setItem('lastVisitDate', today);
                localStorage.setItem('readerStreak', streak);
            }}
            let badge = document.getElementById('streakBadge');
            if (badge) badge.innerText = `🔥 Streak: ${{streak}} Days`;
        }}

        function syncBookmarkUI() {{
            let saved = getSavedIds();
            document.getElementById('savedCount').innerText = saved.length;
            document.querySelectorAll('.btn-save').forEach(btn => {{
                let id = parseInt(btn.id.replace('save-btn-', ''));
                if (saved.includes(id)) {{
                    btn.classList.add('saved');
                    btn.innerText = '✅ Saved';
                }} else {{
                    btn.classList.remove('saved');
                    btn.innerText = '🔖 Save';
                }}
            }});
        }}

        function toggleBookmark(id) {{
            let saved = getSavedIds();
            if (saved.includes(id)) {{
                saved = saved.filter(item => item !== id);
            }} else {{
                saved.push(id);
            }}
            localStorage.setItem('savedArticles', JSON.stringify(saved));
            syncBookmarkUI();
            if (currentCategory === 'SAVED') filterArticles();
        }}

        function savePersonalNote(id) {{
            let val = document.getElementById('notes-' + id).value;
            localStorage.setItem('note_' + id, val);
        }}

        function getFullArticleUrl(relUrl) {{
            let base = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
            return base + relUrl;
        }}

        function showQRCode(id, relUrl) {{
            let qrDiv = document.getElementById('qrcode-' + id);
            if (qrDiv.innerHTML !== '') {{
                qrDiv.style.display = qrDiv.style.display === 'none' ? 'block' : 'none';
                return;
            }}
            qrDiv.style.display = 'block';
            new QRCode(qrDiv, {{ text: getFullArticleUrl(relUrl), width: 140, height: 140 }});
        }}

        function handleTextHighlight(event) {{
            let selection = window.getSelection();
            let popup = document.getElementById('floatingNotePopup');
            if (!selection.isCollapsed && selection.rangeCount > 0) {{
                selectedTextRange = selection.getRangeAt(0);
                popup.style.top = (event.pageY - 40) + 'px';
                popup.style.left = event.pageX + 'px';
                popup.style.display = 'block';
            }} else {{
                popup.style.display = 'none';
            }}
        }}

        function quickSaveHighlightToNotes() {{
            if (!selectedTextRange || !activeReadingId) return;
            let span = document.createElement('mark');
            span.appendChild(selectedTextRange.extractContents());
            selectedTextRange.insertNode(span);
            
            let textarea = document.getElementById('notes-' + activeReadingId);
            if (textarea) {{
                textarea.value = (textarea.value ? textarea.value + '\\n- ' : '- ') + span.innerText;
                savePersonalNote(activeReadingId);
            }}
            document.getElementById('floatingNotePopup').style.display = 'none';
            window.getSelection().removeAllRanges();
        }}

        function cycleThemeMode() {{
            let current = localStorage.getItem('themeMode') || 'light';
            let next = 'light';
            document.body.classList.remove('dark-mode', 'sepia-mode', 'oled-mode');

            if (current === 'light') {{ next = 'dark'; document.body.classList.add('dark-mode'); }}
            else if (current === 'dark') {{ next = 'sepia'; document.body.classList.add('sepia-mode'); }}
            else if (current === 'sepia') {{ next = 'oled'; document.body.classList.add('oled-mode'); }}
            else {{ next = 'light'; }}

            localStorage.setItem('themeMode', next);
            updateThemeButtonText(next);
        }}

        function updateThemeButtonText(mode) {{
            let btn = document.getElementById('themeBtn');
            if (!btn) return;
            if (mode === 'dark') btn.innerText = '🌙 Dark';
            else if (mode === 'sepia') btn.innerText = '📜 Sepia';
            else if (mode === 'oled') btn.innerText = '⬛ OLED';
            else btn.innerText = '☀️ Light';
        }}

        let savedTheme = localStorage.getItem('themeMode');
        if (!savedTheme && new Date().getHours() >= 18) {{
            savedTheme = 'dark';
        }} else if (!savedTheme) {{
            savedTheme = 'light';
        }}

        if (savedTheme === 'dark') document.body.classList.add('dark-mode');
        else if (savedTheme === 'sepia') document.body.classList.add('sepia-mode');
        else if (savedTheme === 'oled') document.body.classList.add('oled-mode');
        updateThemeButtonText(savedTheme);

        function cycleFontFamily() {{
            currentFontIdx = (currentFontIdx + 1) % fontStyles.length;
            let st = fontStyles[currentFontIdx];
            document.body.classList.remove('font-serif', 'font-mono');
            if (st === 'serif') document.body.classList.add('font-serif');
            if (st === 'mono') document.body.classList.add('font-mono');
        }}

        function cycleLineSpacing() {{
            currentSpacingIdx = (currentSpacingIdx + 1) % spacingStyles.length;
            let sp = spacingStyles[currentSpacingIdx];
            document.body.classList.remove('spacing-relaxed', 'spacing-compact');
            if (sp === 'relaxed') document.body.classList.add('spacing-relaxed');
            if (sp === 'compact') document.body.classList.add('spacing-compact');
        }}

        function cycleWpmSpeed() {{
            currentWpmIdx = (currentWpmIdx + 1) % wpmSpeeds.length;
            let wpm = wpmSpeeds[currentWpmIdx];
            document.getElementById('wpmLabel').innerText = wpm;
            if (activeReadingId) onModalScroll();
        }}

        function toggleZenMode() {{
            document.body.classList.toggle('zen-mode');
            let isZen = document.body.classList.contains('zen-mode');
            document.getElementById('zenBtn').innerText = isZen ? '⚡ Exit Zen' : '🧘 Zen';
        }}

        function toggleAutoScroll(btn) {{
            if (autoScrollInterval) {{
                clearInterval(autoScrollInterval);
                autoScrollInterval = null;
                btn.innerText = '📜 Auto-Scroll';
                btn.style.background = '#7c3aed';
            }} else {{
                btn.innerText = '⏹️ Stop Scroll';
                btn.style.background = '#dc2626';
                let container = document.getElementById('modalContainer');
                autoScrollInterval = setInterval(() => {{
                    if (container.scrollTop + container.clientHeight >= container.scrollHeight) {{
                        clearInterval(autoScrollInterval);
                        autoScrollInterval = null;
                        btn.innerText = '📜 Auto-Scroll';
                        btn.style.background = '#7c3aed';
                    }} else {{
                        container.scrollTop += 1.5;
                    }}
                }}, 30);
            }}
        }}

        function searchTopic(keyword) {{
            let input = document.getElementById('searchInput');
            input.value = keyword;
            filterArticles();
        }}

        function filterByCategory(category, btnElement) {{
            currentCategory = category;
            if (btnElement) {{
                document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
                btnElement.classList.add('active');
            }}
            filterArticles();
        }}

        function filterArticles() {{
            let input = document.getElementById('searchInput').value.toLowerCase();
            let saved = getSavedIds();
            let hist = getHistoryIds();
            let cards = document.getElementsByClassName('article-card');

            for (let i = 0; i < cards.length; i++) {{
                let id = parseInt(cards[i].getAttribute('data-id'));
                let title = cards[i].getAttribute('data-title');
                let cat = cards[i].getAttribute('data-category');
                let source = cards[i].getAttribute('data-source');
                
                let matchText = true;
                if (input.startsWith('source:')) {{
                    let qSource = input.replace('source:', '').trim();
                    matchText = source.includes(qSource);
                }} else {{
                    matchText = title.includes(input);
                }}
                
                let matchCat = true;
                if (currentCategory === 'SAVED') matchCat = saved.includes(id);
                else if (currentCategory === 'HISTORY') matchCat = hist.includes(id);
                else if (currentCategory === 'POPULAR') {{
                    let pop = JSON.parse(localStorage.getItem('popularClicks') || '{{}}');
                    matchCat = (pop[id] || 0) > 0;
                }}
                else if (currentCategory !== 'All') matchCat = (cat === currentCategory);

                cards[i].style.display = (matchText && matchCat) ? "" : "none";
            }}
        }}

        function sortArticles() {{
            let order = document.getElementById('sortOrder').value;
            let grid = document.getElementById('articleGrid');
            let cards = Array.from(grid.getElementsByClassName('article-card'));
            cards.sort((a, b) => {{
                let idA = parseInt(a.getAttribute('data-id'));
                let idB = parseInt(b.getAttribute('data-id'));
                if (currentCategory === 'POPULAR') {{
                    let pop = JSON.parse(localStorage.getItem('popularClicks') || '{{}}');
                    let pA = pop[idA] || 0;
                    let pB = pop[idB] || 0;
                    if (pA !== pB) return pB - pA;
                }}
                return order === 'newest' ? (idB - idA) : (idA - idB);
            }});
            cards.forEach(c => grid.appendChild(c));
        }}

        function openRandomArticle() {{
            let cards = Array.from(document.getElementsByClassName('article-card')).filter(c => c.style.display !== 'none');
            if (!cards.length) return;
            let randCard = cards[Math.floor(Math.random() * cards.length)];
            let id = parseInt(randCard.getAttribute('data-id'));
            openModal(id);
        }}

        function openModal(id) {{
            if ('speechSynthesis' in window) window.speechSynthesis.cancel();
            let el = document.getElementById('content-' + id);
            if (!el) return;
            activeReadingId = id;
            recordClick(id);
            recordHistory(id);

            document.getElementById('modalBody').innerHTML = el.innerHTML;
            
            let savedNote = localStorage.getItem('note_' + id);
            if (savedNote) {{
                let textarea = document.getElementById('modalBody').querySelector('#notes-' + id);
                if (textarea) textarea.value = savedNote;
            }}

            let container = document.getElementById('modalContainer');
            container.style.fontSize = modalFontSize + 'px';
            document.getElementById('articleModal').style.display = 'flex';
            
            let savedScroll = localStorage.getItem('scrollPos_' + id);
            container.scrollTop = savedScroll ? parseInt(savedScroll) : 0;
            
            document.getElementById('readingProgressBar').style.width = '0%';
        }}

        function closeModal() {{
            if (autoScrollInterval) {{
                clearInterval(autoScrollInterval);
                autoScrollInterval = null;
            }}
            if (activeReadingId) {{
                let container = document.getElementById('modalContainer');
                localStorage.setItem('scrollPos_' + activeReadingId, container.scrollTop);
            }}
            if ('speechSynthesis' in window) window.speechSynthesis.cancel();
            document.body.classList.remove('zen-mode');
            document.getElementById('zenBtn').innerText = '🧘 Zen';
            document.getElementById('articleModal').style.display = 'none';
        }}

        function changeModalFont(delta) {{
            modalFontSize = Math.min(22, Math.max(13, modalFontSize + delta));
            document.getElementById('modalContainer').style.fontSize = modalFontSize + 'px';
        }}

        function onModalScroll() {{
            let container = document.getElementById('modalContainer');
            let scrollTop = container.scrollTop;
            let scrollHeight = container.scrollHeight - container.clientHeight;
            let pct = scrollHeight > 0 ? Math.round((scrollTop / scrollHeight) * 100) : 0;
            document.getElementById('readingProgressBar').style.width = pct + '%';

            if (activeReadingId) {{
                let timeEl = document.getElementById('modalBody').querySelector('#timeRemaining-' + activeReadingId);
                let pctEl = document.getElementById('modalBody').querySelector('#readPct-' + activeReadingId);
                let currentWpm = wpmSpeeds[currentWpmIdx];
                if (timeEl) {{
                    let cardEl = document.getElementById('artikel-' + activeReadingId);
                    let wordsText = cardEl ? cardEl.querySelector('.article-meta').innerText : '200 words';
                    let matchW = wordsText.match(/\\((\\d+)\\s+w\\)/);
                    let totalWords = matchW ? parseInt(matchW[1]) : 250;
                    let remainingWords = Math.max(10, Math.round(totalWords * (1 - (scrollTop / (scrollHeight || 1)))));
                    let remainingMin = Math.max(1, Math.ceil(remainingWords / currentWpm));
                    timeEl.innerText = `⏱️ ~${{remainingMin}} mins left (${{currentWpm}} WPM)`;
                }}
                if (pctEl) {{
                    pctEl.innerText = `📖 ${{pct}}% read`;
                }}
            }}
        }}

        function jumpToSection(event, targetId) {{
            event.preventDefault();
            let modalBody = document.getElementById('modalBody');
            let target = modalBody.querySelector('#' + targetId);
            if (target) {{
                target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}
        }}

        function copyShareLink(relUrl, btn) {{
            let shareUrl = getFullArticleUrl(relUrl);
            navigator.clipboard.writeText(shareUrl).then(() => {{
                let orig = btn.innerText;
                btn.innerText = '✅ Copied!';
                setTimeout(() => {{ btn.innerText = orig; }}, 1800);
            }});
        }}

        function shareToSocial(platform, title, relUrl) {{
            let url = encodeURIComponent(getFullArticleUrl(relUrl));
            let text = encodeURIComponent(title);
            let shareLink = '';
            if (platform === 'whatsapp') shareLink = `https://api.whatsapp.com/send?text=${{text}}%20${{url}}`;
            else if (platform === 'twitter') shareLink = `https://twitter.com/intent/tweet?text=${{text}}&url=${{url}}`;
            else if (platform === 'facebook') shareLink = `https://www.facebook.com/sharer/sharer.php?u=${{url}}`;
            else if (platform === 'telegram') shareLink = `https://t.me/share/url?url=${{url}}&text=${{text}}`;
            if (shareLink) window.open(shareLink, '_blank');
        }}

        function copyCitation(title, source, date, btn) {{
            let citeText = `Global News Desk. (${{date.slice(-4)}}). ${{title}}. Retrieved from ${{window.location.href}}`;
            navigator.clipboard.writeText(citeText).then(() => {{
                let orig = btn.innerText;
                btn.innerText = '✅ Cited!';
                setTimeout(() => {{ btn.innerText = orig; }}, 1800);
            }});
        }}

        function copyFullArticleText(elementId, btn) {{
            let el = document.getElementById(elementId);
            if (!el) return;
            let text = el.innerText.replace(/Read original article.*/g, '').trim();
            navigator.clipboard.writeText(text).then(() => {{
                let orig = btn.innerText;
                btn.innerText = '✅ Text Copied!';
                setTimeout(() => {{ btn.innerText = orig; }}, 1800);
            }});
        }}

        function copyMarkdownText(title, elementId, btn) {{
            let el = document.getElementById(elementId);
            if (!el) return;
            let text = el.innerText.replace(/Read original article.*/g, '').trim();
            let mdText = `# ${{title}}\\n\\n${{text}}\\n\\n*Source: ${{window.location.href}}*`;
            navigator.clipboard.writeText(mdText).then(() => {{
                let orig = btn.innerText;
                btn.innerText = '✅ Markdown!';
                setTimeout(() => {{ btn.innerText = orig; }}, 1800);
            }});
        }}

        function printArticle(elementId) {{
            let el = document.getElementById(elementId);
            if (!el) return;
            let w = window.open('', '_blank');
            w.document.write('<html><head><title>Print Article</title></head><body style="font-family:sans-serif; padding:20px; line-height:1.6;">' + el.innerHTML + '</body></html>');
            w.document.close();
            w.print();
        }}

        function speakArticle(elementId, btn) {{
            if (!('speechSynthesis' in window)) {{
                alert('Text-to-speech tidak didukung di browser ini.');
                return;
            }}
            let el = document.getElementById(elementId);
            if (!el) return;
            let text = el.innerText.replace(/Read original article.*/g, '').replace(/Quick Jump:.*/g, '').trim();

            if (window.speechSynthesis.speaking) {{
                window.speechSynthesis.cancel();
                btn.innerText = '🔊 Listen';
                return;
            }}

            let utterance = new SpeechSynthesisUtterance(text);
            utterance.rate = 1.0;
            utterance.onend = () => {{ btn.innerText = '🔊 Listen'; }};
            window.speechSynthesis.speak(utterance);
            btn.innerText = '⏹️ Stop Audio';
        }}

        window.addEventListener('scroll', () => {{
            let btn = document.getElementById('backToTopBtn');
            btn.style.display = window.scrollY > 400 ? 'block' : 'none';
        }});

        window.addEventListener('DOMContentLoaded', () => {{
            checkStreak();
            syncBookmarkUI();
            let hash = window.location.hash;
            if (hash && hash.startsWith('#artikel-')) {{
                let id = hash.replace('#artikel-', '');
                openModal(id);
            }}
        }});

        window.onclick = function(event) {{
            let modal = document.getElementById('articleModal');
            let analytics = document.getElementById('analyticsModal');
            if (event.target == modal) {{
                closeModal();
            }}
            if (event.target == analytics) {{
                closeAnalyticsModal();
            }}
        }}

        function downloadPDF(elementId, filename) {{
            let element = document.getElementById(elementId);
            let originalDisplay = element.style.display;
            element.style.display = 'block';
            
            const opt = {{
                margin: 10,
                filename: filename + '.pdf',
                image: {{ type: 'jpeg', quality: 0.98 }},
                html2canvas: {{ scale: 2, useCORS: true }},
                jsPDF: {{ unit: 'mm', format: 'a4', orientation: 'portrait' }}
            }};

            html2pdf().set(opt).from(element).save().then(() => {{
                element.style.display = originalDisplay;
            }});
        }}
    </script>
</body>
</html>""")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(html_parts))

        print(f"✅ Katalog Grid HTML: {output_file} ({len(chunk)} artikel) | Domain: {BASE_URL}")

    return total_pages, single_article_urls

def scrape_massive_rss():
    if not os.path.exists(RSS_FILE):
        print(f"[ERROR] File {RSS_FILE} tidak ditemukan!")
        return

    init_db()

    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"[*] Target Domain Otomatis : {BASE_URL}")
    print(f"[*] Memulai penarikan data dari {len(rss_urls)} sumber RSS...\n")

    total_baru = 0
    total_duplikat = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_single_feed, url): url for url in rss_urls}
        for future in as_completed(futures):
            b, d = future.result()
            total_baru += b
            total_duplikat += d

    print("\n" + "="*45)
    print(f"[SELESAI PEMANENAN]")
    print(f" - Artikel Baru : {total_baru}")
    print(f" - Duplikat/Eksis: {total_duplikat}")
    print("="*45)

    print("\n[*] Generate Halaman Tunggal & Katalog Paginasi (Max 200 per halaman)...")
    total_pages, single_article_urls = export_paginated_files()

    print("\n[*] Generate sitemap.xml, robots.txt, JSON API, multi-channel feeds & reports...")
    generate_sitemap_and_robots(total_baru, total_duplikat, total_pages, single_article_urls)

    print("\n" + "="*45)
    print("[SELESAI] Proses build selesai dengan sukses!")
    print("="*45)

if __name__ == "__main__":
    scrape_massive_rss()
