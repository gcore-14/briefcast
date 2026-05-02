#!/usr/bin/env python3
"""
Daily News Podcast Generator
-----------------------------
Fetches top news stories, filters by your interests using Claude AI,
generates a podcast script, and converts it to audio via ElevenLabs.

Requirements:
    pip install feedparser anthropic requests python-dotenv schedule

Optional (for audio):
    pip install elevenlabs

Setup:
    1. Copy .env.example to .env and fill in your API keys
    2. Edit YOUR_INTERESTS and YOUR_PROFILE below
    3. Run: python news_podcast_generator.py
    4. To run daily at 6am: python news_podcast_generator.py --schedule
"""

import feedparser
import anthropic
import requests
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
import json
import os
import argparse
import schedule
import time
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Fix SSL certificate verification errors (common on macOS)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

load_dotenv()

# ─────────────────────────────────────────────
#  PERSONALIZATION — EDIT THIS SECTION
# ─────────────────────────────────────────────

YOUR_INTERESTS = [
    "macroeconomics and monetary policy",
    "technology and AI",
    "financial markets and investing",
    "geopolitics",
    "climate and energy transition",
    "sports",
    # Add/remove from this list to tune what Claude focuses on:
    # "healthcare and biotech", "startups and venture capital",
    # "real estate", "artificial intelligence", "formula 1",
    # "NHL hockey", "NFL football", "NBA basketball", "MLB baseball",
]

# ─────────────────────────────────────────────
#  FEED CATEGORIES
#  Maps interest areas → which RSS_FEEDS keys to activate.
#  Used today to filter; will power the future app's preference UI.
# ─────────────────────────────────────────────

FEED_CATEGORIES = {
    "business":          ["The Economist Business", "CNBC Finance", "MarketWatch Top Stories",
                          "The Guardian Business", "Quartz"],
    "finance":           ["The Economist Finance", "CNBC Markets", "CNBC Investing",
                          "MarketWatch Top Stories", "MarketWatch Real-time", "NPR Economy",
                          "Investopedia", "Kiplinger"],
    "financial_markets": ["CNBC Markets", "CNBC Investing", "MarketWatch Real-time",
                          "Investopedia", "Kiplinger"],
    "general_news":      ["BBC World", "BBC Business", "Axios", "NPR News",
                          "The Guardian World", "Vox", "The Atlantic", "Slate", "Quartz"],
    "geopolitics":       ["Foreign Affairs", "Foreign Policy", "The Hill", "Defense One",
                          "War on the Rocks", "The Diplomat", "Lawfare"],
    "technology":        ["Ars Technica", "The Verge", "MIT Tech Review", "Wired",
                          "TechCrunch", "The Guardian Tech", "VentureBeat", "Hacker News Best"],
    "artificial_intelligence": ["VentureBeat AI", "MIT Tech Review", "Ars Technica",
                                "AI Business", "Import AI (Jack Clark)",
                                "The Batch (deeplearning)", "Last Week in AI"],
    "healthcare":        ["STAT News", "STAT Health", "NPR Health", "MedPage Today",
                          "Kaiser Health News", "Scientific American"],
    "science":           ["Scientific American", "New Scientist", "Nature News", "STAT News"],
    "climate":           ["Inside Climate News", "Yale Environment 360", "CleanTechnica",
                          "The Guardian Environment", "Canary Media", "Heatmap News"],
    "startups":          ["TechCrunch Startups", "Y Combinator News", "StrictlyVC",
                          "VentureBeat"],
    "real_estate":       ["Calculated Risk", "HousingWire", "The Real Deal", "Curbed"],
    "sports":            ["ESPN Top Headlines", "BBC Sport", "CBS Sports",
                          "Yahoo Sports", "Sports Illustrated"],
    "nfl":               ["ESPN NFL", "Pro Football Talk", "NFL.com News"],
    "nba":               ["ESPN NBA", "HoopsHype"],
    "mlb":               ["ESPN MLB"],
    "nhl":               ["ESPN NHL", "NHL.com News", "The Hockey News", "Sportsnet NHL"],
    "college_sports":    ["ESPN College Football", "ESPN College Basketball"],
    "soccer":            ["ESPN Soccer", "BBC Sport Football"],
    "golf":              ["ESPN Golf"],
    "tennis":            ["ESPN Tennis"],
    "formula_1":         ["Autosport F1", "Motorsport.com"],
}

def get_feeds_for_interests(interests: list[str]) -> dict:
    """
    Filter RSS_FEEDS to only sources relevant to the user's chosen interests.
    Falls back to ALL feeds if no matching categories found.
    Future app hook: call this with user's selected preference categories.
    """
    selected_keys = set()
    for interest in interests:
        # Fuzzy match: "AI" matches "artificial_intelligence", "hockey" matches "nhl", etc.
        normalized = interest.lower().replace(" ", "_").replace("-", "_")
        for category, keys in FEED_CATEGORIES.items():
            if category in normalized or normalized in category:
                selected_keys.update(keys)

    if not selected_keys:
        return RSS_FEEDS  # fallback: all feeds

    return {k: v for k, v in RSS_FEEDS.items() if k in selected_keys}

YOUR_PROFILE = """
I am a finance and data professional based in Boston. I have a strong background in 
markets and investing. I prefer analysis over headlines — I want to understand 
WHY something matters, not just WHAT happened. I like a conversational but 
intelligent tone, similar to how a well-informed colleague would brief me.
"""

PODCAST_STYLE = """
Warm, intelligent, and conversational — like a trusted analyst friend 
giving you a morning briefing over coffee. Not stiff or overly formal. 
Use natural transitions. Occasionally add brief editorial color when appropriate.
"""

# ─────────────────────────────────────────────
#  NEWS SOURCES — customize as needed
# ─────────────────────────────────────────────

# RSS_FEEDS = {
#     # Your custom rss.app feeds (FT + WSJ) — keep these as-is
#     "FT (free headlines)":      "https://rss.app/feed/wPeeflY7S2xnBOGe",
#     "WSJ (free headlines)":     "https://rss.app/feed/m7lrlgUFmHoXYD9M",

#     # Reuters — official RSS feeds (not homepage URLs)
#     "Reuters Top News":         "https://feeds.reuters.com/reuters/topNews",
#     "Reuters Business":         "https://feeds.reuters.com/reuters/businessNews",

#     # AP — official RSS feeds
#     "AP Top News":              "https://feeds.apnews.com/rss/apf-topnews",
#     "AP Business":              "https://feeds.apnews.com/rss/apf-business",

#     # Axios — official feed
#     "Axios":                    "https://api.axios.com/feed/",

#     # NPR Economy
#     "NPR Economy":              "https://feeds.npr.org/1017/rss.xml",

#     # The Economist — finance section
#     "The Economist":            "https://www.economist.com/finance-and-economics/rss.xml",

#     # Ars Technica
#     "Ars Technica":             "https://feeds.arstechnica.com/arstechnica/index",

#     # TechCrunch
#     "TechCrunch":               "https://techcrunch.com/feed/",
# }

RSS_FEEDS = {
    # ── FINANCE & MARKETS ──────────────────────────────────────────────────
    "The Economist Finance":     "https://www.economist.com/finance-and-economics/rss.xml",
    "The Economist Business":    "https://www.economist.com/business/rss.xml",
    "CNBC Markets":              "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Finance":              "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "CNBC Investing":            "https://www.cnbc.com/id/15839135/device/rss/rss.html",
    "MarketWatch Top Stories":   "https://feeds.marketwatch.com/marketwatch/topstories/",
    "MarketWatch Real-time":     "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/",
    "NPR Economy":               "https://feeds.npr.org/1017/rss.xml",
    "The Guardian Business":     "https://www.theguardian.com/business/rss",
    "Investopedia":              "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline",
    "Kiplinger":                 "https://www.kiplinger.com/feeds/rss/investing.xml",

    # ── GENERAL / WORLD NEWS ──────────────────────────────────────────────
    "BBC World":                 "https://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Business":              "https://feeds.bbci.co.uk/news/business/rss.xml",
    "Axios":                     "https://api.axios.com/feed/",
    "NPR News":                  "https://feeds.npr.org/1001/rss.xml",
    "The Guardian World":        "https://www.theguardian.com/world/rss",
    "Vox":                       "https://www.vox.com/rss/index.xml",
    "The Atlantic":              "https://feeds.feedburner.com/TheAtlantic",
    "Slate":                     "https://feeds.feedburner.com/Slate",
    "Quartz":                    "https://qz.com/feed",

    # ── GEOPOLITICS ───────────────────────────────────────────────────────
    "Foreign Affairs":           "https://www.foreignaffairs.com/rss.xml",
    "Foreign Policy":            "https://foreignpolicy.com/feed/",
    "The Hill":                  "https://thehill.com/feed/",
    "Defense One":               "https://www.defenseone.com/rss/all/",
    "War on the Rocks":          "https://warontherocks.com/feed/",
    "The Diplomat":              "https://thediplomat.com/feed/",
    "Lawfare":                   "https://www.lawfaremedia.org/feed",

    # ── TECHNOLOGY & AI ───────────────────────────────────────────────────
    "Ars Technica":              "https://feeds.arstechnica.com/arstechnica/index",
    "The Verge":                 "https://www.theverge.com/rss/index.xml",
    "MIT Tech Review":           "https://www.technologyreview.com/feed/",
    "Wired":                     "https://www.wired.com/feed/rss",
    "TechCrunch":                "https://techcrunch.com/feed/",
    "The Guardian Tech":         "https://www.theguardian.com/technology/rss",
    "VentureBeat AI":            "https://venturebeat.com/category/ai/feed/",
    "VentureBeat":               "https://venturebeat.com/feed/",
    "The Information (free)":    "https://www.theinformation.com/feed",
    "Hacker News Best":          "https://hnrss.org/best",

    # ── ARTIFICIAL INTELLIGENCE (dedicated) ───────────────────────────────
    "AI Business":               "https://aibusiness.com/rss.xml",
    "Import AI (Jack Clark)":    "https://importai.substack.com/feed",
    "The Batch (deeplearning)":  "https://www.deeplearning.ai/the-batch/feed/",
    "Last Week in AI":           "https://lastweekin.ai/feed",

    # ── HEALTHCARE & SCIENCE ──────────────────────────────────────────────
    "STAT News":                 "https://www.statnews.com/feed/",
    "STAT Health":               "https://www.statnews.com/category/health/feed/",
    "NPR Health":                "https://feeds.npr.org/1128/rss.xml",
    "Scientific American":       "https://www.scientificamerican.com/platform/syndication/rss/",
    "New Scientist":             "https://www.newscientist.com/feed/home/",
    "Nature News":               "https://www.nature.com/nature.rss",
    "MedPage Today":             "https://www.medpagetoday.com/rss/headlines.xml",
    "Kaiser Health News":        "https://khn.org/feed/",

    # ── CLIMATE & ENERGY ──────────────────────────────────────────────────
    "Inside Climate News":       "https://insideclimatenews.org/feed/",
    "Yale Environment 360":      "https://e360.yale.edu/feed",
    "CleanTechnica":             "https://cleantechnica.com/feed/",
    "The Guardian Environment":  "https://www.theguardian.com/environment/rss",
    "Carbon180":                 "https://carbon180.org/feed",
    "Canary Media":              "https://www.canarymedia.com/feed",
    "Heatmap News":              "https://heatmap.news/feed",

    # ── STARTUPS & VC ─────────────────────────────────────────────────────
    "TechCrunch Startups":       "https://techcrunch.com/category/startups/feed/",
    "Y Combinator News":         "https://news.ycombinator.com/rss",
    "StrictlyVC":                "https://strictlyvc.com/feed/",
    "The Information Startups":  "https://www.theinformation.com/feed",

    # ── REAL ESTATE ───────────────────────────────────────────────────────
    "Calculated Risk":           "https://feeds.feedburner.com/CalculatedRisk",
    "HousingWire":               "https://www.housingwire.com/feed",
    "The Real Deal":             "https://therealdeal.com/feed/",
    "Curbed":                    "https://www.curbed.com/rss/index.xml",

    # ── SPORTS ────────────────────────────────────────────────────────────
    # — Broad —
    "ESPN Top Headlines":        "https://www.espn.com/espn/rss/news",
    "BBC Sport":                 "https://feeds.bbci.co.uk/sport/rss.xml",
    "CBS Sports":                "https://www.cbssports.com/rss/headlines/",
    "Yahoo Sports":              "https://sports.yahoo.com/rss/",
    "Sports Illustrated":        "https://www.si.com/rss/si_topstories.rss",

    # — NFL —
    "ESPN NFL":                  "https://www.espn.com/espn/rss/nfl/news",
    "Pro Football Talk":         "https://profootballtalk.nbcsports.com/feed/",
    "NFL.com News":              "https://www.nfl.com/rss/rsslanding.html?tag=news",

    # — NBA —
    "ESPN NBA":                  "https://www.espn.com/espn/rss/nba/news",
    "HoopsHype":                 "https://hoopshype.com/feed/",

    # — MLB —
    "ESPN MLB":                  "https://www.espn.com/espn/rss/mlb/news",

    # — NHL —
    "ESPN NHL":                  "https://www.espn.com/espn/rss/nhl/news",
    "NHL.com News":              "https://www.nhl.com/rss/news.xml",
    "The Hockey News":           "https://thehockeynews.com/feed/",
    "Sportsnet NHL":             "https://www.sportsnet.ca/feed/",

    # — College Sports —
    "ESPN College Football":     "https://www.espn.com/espn/rss/ncf/news",
    "ESPN College Basketball":   "https://www.espn.com/espn/rss/ncb/news",

    # — Soccer —
    "ESPN Soccer":               "https://www.espn.com/espn/rss/soccer/news",
    "BBC Sport Football":        "https://feeds.bbci.co.uk/sport/football/rss.xml",

    # — Golf & Tennis —
    "ESPN Golf":                 "https://www.espn.com/espn/rss/golf/news",
    "ESPN Tennis":               "https://www.espn.com/espn/rss/tennis/news",

    # — Formula 1 —
    "Autosport F1":              "https://www.autosport.com/rss/feed/f1",
    "Motorsport.com":            "https://www.motorsport.com/rss/f1/news/",
}

# ─────────────────────────────────────────────
#  SETTINGS
# ─────────────────────────────────────────────

MAX_ARTICLES_PER_FEED = 3      # Articles to pull per source (more feeds, fewer per)
MAX_ARTICLES_TO_CLAUDE = 60     # Cap before sending to Claude (cost control)
PODCAST_LENGTH_MINUTES = 15     # Target podcast length
OUTPUT_DIR = Path("briefcast/backend/output")
# OUTPUT_DIR = Path("./output")   # Where to save scripts and audio
ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Default: "Rachel" — change as desired

# ─────────────────────────────────────────────
#  STEP 1: FETCH NEWS
# ─────────────────────────────────────────────

def fetch_articles(feeds: dict, max_per_feed: int = 8) -> list[dict]:
    """Fetch articles from all RSS feeds."""
    articles = []
    print("\n📡 Fetching news feeds...")

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    for source_name, url in feeds.items():
        try:
            # Use requests first — it handles SSL and redirects more reliably than urllib
            try:
                resp = requests.get(url, headers=headers, timeout=10, verify=False)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception:
                # Final fallback: let feedparser try directly
                feed = feedparser.parse(url)

            # Detect HTML page mistakenly used as RSS URL
            if feed.bozo and not feed.entries:
                bozo_msg = str(feed.get("bozo_exception", "unknown parse error"))
                print(f"  ✗ {source_name}: Not a valid RSS feed — {bozo_msg[:80]}")
                print(f"      URL was: {url}")
                print(f"      Tip: Use an RSS/Atom XML URL, not a website homepage.")
                continue

            if not feed.entries:
                print(f"  ✗ {source_name}: Feed parsed OK but 0 entries returned (URL: {url})")
                continue

            count = 0
            for entry in feed.entries[:max_per_feed]:
                article = {
                    "source":    source_name,
                    "title":     entry.get("title", "").strip(),
                    "summary":   entry.get("summary", entry.get("description", ""))[:600].strip(),
                    "link":      entry.get("link", ""),
                    "published": entry.get("published", ""),
                }
                if article["title"]:
                    articles.append(article)
                    count += 1
            print(f"  ✓ {source_name}: {count} articles")

        except Exception as e:
            print(f"  ✗ {source_name}: unexpected error — {e}")

    print(f"\n  Total articles fetched: {len(articles)}")
    return articles



def format_articles_for_prompt(articles: list[dict], max_articles: int) -> str:
    """Format articles into a clean string for the Claude prompt."""
    # Deduplicate by title similarity (simple approach)
    seen_titles = set()
    unique = []
    for a in articles:
        key = a["title"].lower()[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(a)
    
    # Cap total
    selected = unique[:max_articles]
    
    lines = []
    for i, a in enumerate(selected, 1):
        lines.append(f"[{i}] SOURCE: {a['source']}")
        lines.append(f"    TITLE: {a['title']}")
        if a["summary"]:
            lines.append(f"    SUMMARY: {a['summary']}")
        lines.append("")
    
    return "\n".join(lines)

# ─────────────────────────────────────────────
#  STEP 2: GENERATE PODCAST SCRIPT WITH CLAUDE
# ─────────────────────────────────────────────

def generate_podcast_script(articles_text: str) -> str:
    """Send articles to Claude and get back a podcast script."""
    print("\n🤖 Generating podcast script with local model...")
    
    # client = anthropic.Anthropic(api_key=os.getenv("flying-sausauge-28-ai"))
    # client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    today = datetime.now().strftime("%A, %B %d, %Y")
    
    prompt = f"""You are a world-class daily news podcast producer and writer. 
Today is {today}.

YOUR LISTENER'S PROFILE:
{YOUR_PROFILE.strip()}

LISTENER'S INTERESTS (prioritize stories related to these):
{chr(10).join(f"- {i}" for i in YOUR_INTERESTS)}

PODCAST STYLE:
{PODCAST_STYLE.strip()}

TARGET LENGTH: {PODCAST_LENGTH_MINUTES} minutes when read aloud at a natural pace 
(approximately {PODCAST_LENGTH_MINUTES * 130} words).

HERE ARE TODAY'S TOP STORIES:
{articles_text}

YOUR TASK:
Write a complete, ready-to-record podcast script. Select and prioritize the 
stories most relevant to the listener's interests and profile. Skip stories 
that are clearly not relevant.

REQUIRED STRUCTURE:
1. INTRO (30 sec): Warm welcome, date, quick "what's in today's episode" tease
2. TOP STORY (4-5 min): The single most important/relevant story, with context and why it matters
3. STORY 2 (3-4 min): Second major story
4. STORY 3 (3-4 min): Third major story
5. QUICK HITS (3-4 min): 4-6 shorter items, ~30 seconds each
6. MARKETS BRIEF (2 min): Any market-relevant news woven together
7. CLOSING (30 sec): Brief recap, sign off warmly

IMPORTANT GUIDELINES:
- Write for the ear, not the eye. Short sentences. Natural rhythm. 
- Explain jargon or acronyms briefly when first used.
- Add "why this matters" context for each story — don't just describe events.
- Use natural verbal transitions like "Now, turning to..." or "Meanwhile..." 
- Do NOT use bullet points, headers, or markdown in the script — just clean prose.
- Write out numbers naturally (say "three billion dollars", not "$3B").
- Avoid filler phrases like "In today's fast-paced world" or "It's important to note".

OUTPUT FORMAT:
Return ONLY the podcast script — no preamble, no notes, no instructions. 
Start with the host's first word and end with the sign-off.
"""



    
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    
    script = message.content[0].text
    word_count = len(script.split())
    est_minutes = round(word_count / 130)
    print(f"  ✓ Script generated: ~{word_count} words (~{est_minutes} min)")
    return script


# ─────────────────────────────────────────────
#  STEP 3: CONVERT TO AUDIO (ELEVENLABS)
# ─────────────────────────────────────────────

def text_to_speech_elevenlabs(script: str, output_path: Path) -> bool:
    """Convert script to audio using ElevenLabs API."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("\n⚠️  No ELEVENLABS_API_KEY found — skipping audio generation.")
        print("   Set it in your .env file to enable audio output.")
        return False
    
    print(f"\n🎙️  Converting to audio with ElevenLabs...")
    
    # ElevenLabs has a character limit per request (~5000 chars)
    # For longer scripts, we split and concatenate
    MAX_CHARS = 4500
    chunks = []
    words = script.split()
    current_chunk = []
    current_len = 0
    
    for word in words:
        current_len += len(word) + 1
        current_chunk.append(word)
        if current_len >= MAX_CHARS:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
            current_len = 0
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    
    print(f"  Sending {len(chunks)} chunk(s) to ElevenLabs...")
    
    audio_parts = []
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    
    for i, chunk in enumerate(chunks):
        payload = {
            "text": chunk,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            audio_parts.append(response.content)
            print(f"  ✓ Chunk {i+1}/{len(chunks)} done")
        else:
            print(f"  ✗ Chunk {i+1} failed: {response.status_code} - {response.text[:200]}")
            return False
    
    # Combine and save
    with open(output_path, "wb") as f:
        for part in audio_parts:
            f.write(part)
    
    size_mb = output_path.stat().st_size / 1_000_000
    print(f"  ✓ Audio saved: {output_path} ({size_mb:.1f} MB)")
    return True


# ─────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline():
    """Run the full news → script → audio pipeline."""
    print(f"\n{'='*50}")
    print(f"  Daily News Podcast — {datetime.now().strftime('%A, %B %d, %Y')}")
    print(f"{'='*50}")
    
    # Setup output directory
    OUTPUT_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Step 1: Fetch news
    # Filter feeds to only those matching the user's interests
    active_feeds = get_feeds_for_interests(YOUR_INTERESTS)
    print(f"\n  Using {len(active_feeds)} feed sources for interests: {', '.join(YOUR_INTERESTS)}")
    articles = fetch_articles(active_feeds, MAX_ARTICLES_PER_FEED)
    if not articles:
        print("❌ No articles fetched. Check your internet connection or RSS feeds.")
        return
    
    articles_text = format_articles_for_prompt(articles, MAX_ARTICLES_TO_CLAUDE)
    
    # Step 2: Generate script
    script = generate_podcast_script(articles_text)
    
    # Save script
    script_path = OUTPUT_DIR / f"podcast_script_{date_str}.txt"
    with open(script_path, "w") as f:
        f.write(f"DAILY NEWS PODCAST — {date_str}\n")
        f.write("="*50 + "\n\n")
        f.write(script)
    print(f"\n  📄 Script saved: {script_path}")
    
    # Step 3: Generate audio
    audio_path = OUTPUT_DIR / f"podcast_{date_str}.mp3"
    audio_success = text_to_speech_elevenlabs(script, audio_path)
    
    # Summary
    print(f"\n{'='*50}")
    print(f"  ✅ Done!")
    print(f"  📄 Script: {script_path}")
    if audio_success:
        print(f"  🎧 Audio:  {audio_path}")
    else:
        print(f"  🎧 Audio:  Not generated (add ElevenLabs key to enable)")
    print(f"{'='*50}\n")


# ─────────────────────────────────────────────
#  SCHEDULER (optional daily run)
# ─────────────────────────────────────────────

def run_scheduled(time_str: str = "06:00"):
    """Run the pipeline every day at the specified time."""
    print(f"⏰ Scheduler started — will run daily at {time_str}")
    print("   Press Ctrl+C to stop.\n")
    
    schedule.every().day.at(time_str).do(run_pipeline)
    
    # Run once immediately on start
    run_pipeline()
    
    while True:
        schedule.run_pending()
        time.sleep(60)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily News Podcast Generator")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on a daily schedule (default: 6:00 AM)"
    )
    parser.add_argument(
        "--time",
        default="06:00",
        help="Time to run daily (24h format, e.g. 06:30). Used with --schedule."
    )
    args = parser.parse_args()
    
    if args.schedule:
        run_scheduled(args.time)
    else:
        run_pipeline()