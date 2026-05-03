import feedparser
from config import SECURITY_FEEDS, STACK_KEYWORDS


def fetch_security_news(max_items: int = 20) -> list[dict]:
    relevant = []
    for feed_url in SECURITY_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_items]:
                title = entry.get("title", "").lower()
                summary = entry.get("summary", "").lower()
                if any(kw in title or kw in summary for kw in STACK_KEYWORDS):
                    relevant.append({
                        "title": entry.get("title"),
                        "link": entry.get("link"),
                        "published": entry.get("published", "unknown"),
                        "summary": entry.get("summary", "")[:300],
                    })
        except Exception as e:
            print(f"[news] Failed to fetch {feed_url}: {e}")
    return relevant


if __name__ == "__main__":
    for item in fetch_security_news():
        print(f"- {item['title']} ({item['published']})")
