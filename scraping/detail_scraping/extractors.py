#!/usr/bin/env python3
"""
Per-source detail (article) extraction for detail scraping.

Parser profiles (keyed by the config's "parser" value):
    harvard          - Harvard Health article pages (engine-fetched HTML)
    webmd_topic      - WebMD topic/article pages (engine-fetched HTML)
    who_news         - WHO news item pages (parser fetches the page)
    who_don          - WHO Disease Outbreak News pages (parser fetches)
    who_feature_story- WHO feature story pages (parser fetches)
    who_fact_sheet   - WHO fact-sheet pages (parser fetches)

Parsers with fetch="engine" receive (item, html, final_url, cfg).
Parsers with fetch="internal" receive (item, cfg) and do their own HTTP GET.

All parsers return a dict with standardized fields:
    url, title, slug, published_date, author, medically_reviewed_by,
    tags, meta_description, scrape_timestamp_utc, sections, references, images, pdfs, etc.

Sections format:
    [
        {"heading": "Section Title", "content": ["para1", "para2"], "bullets": ["item1"]},
        {"heading": None, "content_blocks": [{"type": "paragraph", "text": "...", "associated_bullets": [...]}]}
    ]

Usage:
    from scraping.detail_scraping.extractors import get_parser
    parser = get_parser("harvard")
    article = parser(item, html, final_url, cfg)
"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from scraping.common import (
    make_slug,
    normalize_date,
    normalize_space,
    parse_date_str,
    requests_session_with_retries,
    utc_now_iso,
)


# ----------------------------
# Shared text helpers
# ----------------------------

def safe_text(el) -> str:
    return el.get_text(separator=" ", strip=True) if el else ""


def dedupe_keep_order(values) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for value in values:
        if not value:
            continue
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def normalize_date_str(date_str: str) -> str:
    if not date_str:
        return ""
    date_str = date_str.strip()
    match = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
    if match:
        return match.group(1)
    match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", date_str)
    if match:
        return match.group(1)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return date_str


def make_slug_from_url(url: str) -> str:
    path = urlparse(url).path
    slug = path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9\-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-").lower()
    return slug or "topic"


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text


def extract_year_from_date(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", str(date_str))
    if match:
        return int(match.group(0))
    return None


# ----------------------------
# Shared WHO helpers
# ----------------------------

def parse_article_sections(article_div, base_url: str, initial_heading: Optional[str] = None) -> List[dict]:
    """Parse an article div into sections (heading + content paragraphs)."""
    sections: List[dict] = []
    heading_tags = article_div.find_all(["h2", "h3", "h4"])

    def append_list_or_text(contents, items):
        if contents and isinstance(contents[-1], str):
            contents[-1] = {"text": contents[-1], "bullets": items}
        else:
            contents.append({"text": None, "bullets": items})

    def collect_from_node(node, contents):
        if isinstance(node, Tag):
            if node.name == "p":
                text = node.get_text(" ", strip=True)
                if text:
                    contents.append(text)
            elif node.name in ["ul", "ol"]:
                items = [li.get_text(" ", strip=True) for li in node.find_all("li")]
                if items:
                    append_list_or_text(contents, items)
            elif node.name not in ["h2", "h3", "h4"]:
                text = node.get_text(" ", strip=True)
                if text:
                    contents.append(text)
        elif isinstance(node, NavigableString):
            text = node.strip()
            if text:
                contents.append(text)

    if not heading_tags:
        contents: List[Any] = []
        for el in article_div.children:
            collect_from_node(el, contents)
        sections.append({"heading": None, "content": contents if contents else None})
    else:
        first_heading = heading_tags[0]
        lead_contents: List[Any] = []
        for el in article_div.children:
            if el == first_heading:
                break
            collect_from_node(el, lead_contents)
        if lead_contents:
            sections.append({"heading": initial_heading, "content": lead_contents})

        for h in heading_tags:
            heading = h.get_text(strip=True)
            content_blocks: List[Any] = []
            tables: List[List[List[str]]] = []
            images: List[str] = []

            for sib in h.next_siblings:
                if isinstance(sib, Tag) and sib.name in ["h2", "h3", "h4"]:
                    break
                if isinstance(sib, Tag):
                    if sib.name == "table":
                        rows = []
                        for tr in sib.find_all("tr"):
                            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                            if cols:
                                rows.append(cols)
                        if rows:
                            tables.append(rows)
                    elif sib.name == "figure" or sib.find("img"):
                        imgs = sib.find_all("img")
                        for im in imgs:
                            src = im.get("src") or im.get("data-src")
                            if src:
                                images.append(urljoin(base_url, src))
                    else:
                        collect_from_node(sib, content_blocks)
                elif isinstance(sib, NavigableString):
                    collect_from_node(sib, content_blocks)

            section_obj: Dict[str, Any] = {
                "heading": heading,
                "content": content_blocks if content_blocks else None,
            }
            if tables:
                section_obj["tables"] = tables
            if images:
                section_obj["images"] = images
            sections.append(section_obj)

    return sections


def extract_media_contacts(block) -> List[dict]:
    """Extract media contacts from a BeautifulSoup block."""
    contacts: List[dict] = []
    if not block:
        return contacts

    raw = block.get_text("\n", strip=True)
    persons = re.split(r"\n{2,}", raw)
    for p in persons:
        p = p.strip()
        if not p:
            continue
        emails = re.findall(r"[\w\.-]+@[\w\.-]+", p)
        phones = re.findall(r"(\+?\d[\d\s\-\(\)]+)", p)
        lines = [line.strip() for line in p.splitlines() if line.strip()]
        contacts.append(
            {
                "name": lines[0] if lines else None,
                "role": lines[1] if len(lines) > 1 else None,
                "emails": emails or None,
                "phones": phones or None,
                "raw": p,
            }
        )
    return contacts


def extract_media_contacts_from_page(soup: BeautifulSoup) -> List[dict]:
    """Find the 'Media' header on a WHO page and parse the following block."""
    mc_header = soup.find(
        lambda tag: tag.name in ("h2", "h3", "strong", "p")
        and re.search(r"\bmedia\b", tag.get_text(), re.I)
    )
    if not mc_header:
        return []

    block = mc_header.find_next_sibling()
    nodes = []
    cur = block
    while cur and not (cur.name in ("h2", "h3") or cur.name == "hr"):
        nodes.append(cur)
        cur = cur.find_next_sibling()
    if not nodes:
        return []

    bs = BeautifulSoup("<div>" + "".join(str(n) for n in nodes) + "</div>", "html.parser")
    return extract_media_contacts(bs.div)


def validate_author(author: Optional[str]) -> Optional[str]:
    """Default to 'WHO' when the author value looks like malformed data."""
    if not author:
        return "WHO"

    author_stripped = author.strip()
    is_invalid = (
        re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", author_stripped)
        or re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", author_stripped)
        or re.search(r"\d{2}:\d{2}", author_stripped)
        or re.search(r"@[\w\.-]+\.\w+", author_stripped)
        or re.search(r"https?://", author_stripped, re.I)
        or re.search(r"^(contact|media|information|unknown|n/a|none)$", author_stripped, re.I)
        or len(author_stripped) < 2
        or len(author_stripped) > 150
        or (sum(c.isdigit() for c in author_stripped) / max(len(author_stripped), 1) > 0.5)
    )
    if is_invalid:
        return "WHO"
    return author


def extract_images_from_content(content_container, base_url: str, og_image: Optional[str] = None) -> List[str]:
    """Extract images from a WHO content container (data-image / src / style)."""
    images: List[str] = []
    if content_container:
        for d in content_container.select("div.background-image, .hero img, figure img, img"):
            data_image = d.get("data-image") if d.has_attr("data-image") else None
            if data_image:
                images.append(urljoin(base_url, data_image))
                continue
            if d.name == "img" and d.get("src"):
                images.append(urljoin(base_url, d.get("src")))
                continue
            style = d.get("style") or ""
            match = re.search(r'url\((?:&quot;|")?(.*?)(?:&quot;|")?\)', style)
            if match:
                images.append(urljoin(base_url, match.group(1)))
    if not images and og_image:
        images = [og_image]
    return images


def who_request_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    req = cfg.get("request", {})
    return {
        "headers": {"User-Agent": req.get("user_agent", "Mozilla/5.0 (WHO-scraper/1.0)")},
        "timeout": int(req.get("timeout", 15)),
    }


# ----------------------------
# Harvard Health Publishing
# ----------------------------

def extract_text(node: Optional[Tag]) -> str:
    if not node:
        return ""
    return normalize_space(node.get_text(" ", strip=True))


def get_header_category(header: Optional[Tag]) -> str:
    if not header:
        return ""
    for a in header.select("a[href]"):
        if a.find_parent("address") or a.find_parent("ul"):
            continue
        text = extract_text(a)
        if text:
            return text
    return ""


def get_author(scope: Optional[Tag]) -> str:
    if not scope:
        return ""
    address = scope.select_one("address")
    if not address:
        return ""
    text = extract_text(address)
    text = re.sub(r"^By\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+,", ",", text)
    return text


def get_medically_reviewed_by(scope: Optional[Tag]) -> str:
    if not scope:
        return ""
    reviewed_li = scope.select_one("ul li")
    if not reviewed_li:
        return ""
    text = extract_text(reviewed_li)
    text = re.sub(r"^Reviewed\s+by\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+,", ",", text)
    return text


def get_published_date(scope: Optional[Tag]) -> str:
    if not scope:
        return ""
    time_tag = scope.select_one("time")
    if not time_tag:
        return ""
    datetime_attr = time_tag.get("datetime")
    raw = datetime_attr if isinstance(datetime_attr, str) else extract_text(time_tag)
    return normalize_date(raw)


def get_meta_description(soup: BeautifulSoup, header: Optional[Tag]) -> str:
    if header:
        h2 = header.find("h2")
        if h2:
            text = extract_text(h2)
            if text:
                return text
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        content_attr = meta.get("content")
        if isinstance(content_attr, str):
            return normalize_space(content_attr)
    return ""


def node_is_under_content_container(node: Tag, container: Tag) -> bool:
    parent = node.parent
    while isinstance(parent, Tag):
        if parent is container:
            return True
        parent = parent.parent
    return False


def extract_list_items(list_node: Tag) -> List[str]:
    items: List[str] = []
    for li in list_node.find_all("li", recursive=False):
        text = extract_text(li)
        if text:
            items.append(text)
    return dedupe_keep_order(items)


def build_sections(content_root: Optional[Tag]) -> List[Dict]:
    if not content_root:
        return []

    sections: List[Dict] = []
    current_section: Dict = {"heading": None, "content_blocks": []}
    relevant_tags = {"h2", "h3", "h4", "p", "ul", "ol"}

    for node in content_root.descendants:
        if not isinstance(node, Tag):
            continue
        if node.name not in relevant_tags:
            continue
        if not node_is_under_content_container(node, content_root):
            continue
        if node.find_parent(["script", "style", "noscript", "figure", "table"]):
            continue

        if node.name in {"h2", "h3", "h4"}:
            heading_text = extract_text(node)
            if not heading_text:
                continue
            if current_section["heading"] is not None or current_section["content_blocks"]:
                sections.append(current_section)
            current_section = {"heading": heading_text, "content_blocks": []}
            continue

        if node.name == "p":
            if node.find("img"):
                continue
            if node.find_parent("li"):
                continue
            paragraph_text = extract_text(node)
            if not paragraph_text:
                continue
            current_section["content_blocks"].append(
                {"type": "paragraph", "text": paragraph_text, "associated_bullets": None}
            )
            continue

        if node.name in {"ul", "ol"}:
            if node.find_parent(["ul", "ol"]):
                continue
            items = extract_list_items(node)
            if not items:
                continue
            if current_section["content_blocks"]:
                last_block = current_section["content_blocks"][-1]
                if last_block.get("type") == "paragraph":
                    existing = last_block.get("associated_bullets") or []
                    last_block["associated_bullets"] = dedupe_keep_order(existing + items)
                    continue
            current_section["content_blocks"].append(
                {"type": "paragraph", "text": None, "associated_bullets": items}
            )

    if current_section["heading"] is not None or current_section["content_blocks"]:
        sections.append(current_section)

    cleaned_sections: List[Dict] = []
    for sec in sections:
        content_blocks = sec.get("content_blocks") or []
        if not content_blocks and sec.get("heading") is None:
            continue
        cleaned_sections.append({"heading": sec.get("heading"), "content_blocks": content_blocks})
    return cleaned_sections


def parse_harvard(item: dict, html: str, final_url: str, cfg: Dict[str, Any]) -> dict:
    """Parse a Harvard Health article page."""
    soup = BeautifulSoup(html, "html.parser")

    article_scope = soup.select_one("article")
    header = (
        (article_scope.select_one("header") if article_scope else None)
        or soup.select_one("article header")
        or soup.select_one("header")
    )
    content_root = soup.select_one("div.content-repository-content")

    title = extract_text((header.find("h1") if header else None) or soup.find("h1"))
    slug = item.get("slug") or make_slug(final_url)
    published_date = get_published_date(article_scope or header)
    author = get_author(article_scope or header)
    medically_reviewed_by = get_medically_reviewed_by(article_scope or header)
    meta_description = get_meta_description(soup, header)

    list_tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    category_from_list = item.get("category") if isinstance(item.get("category"), str) else ""
    category_from_header = get_header_category(header)
    base_tags = cfg.get("base_tags", ["Harvard Health Publishing", "Blog"])

    tags = dedupe_keep_order(
        [*(list_tags or base_tags), category_from_list, category_from_header]
    )

    sections = build_sections(content_root)

    if not title and item.get("title"):
        item_title = item.get("title")
        if isinstance(item_title, str):
            title = normalize_space(item_title)

    return {
        "url": final_url,
        "title": title,
        "slug": slug,
        "published_date": published_date,
        "author": author,
        "medically_reviewed_by": medically_reviewed_by,
        "tags": tags,
        "meta_description": meta_description,
        "scrape_timestamp_utc": utc_now_iso(),
        "sections": sections,
    }


# ----------------------------
# WebMD
# ----------------------------

def paragraph_introduces_list(paragraph_text: str) -> bool:
    if not paragraph_text:
        return False
    pt = paragraph_text.strip()
    if pt.endswith(":") or pt.endswith("such as:"):
        return True
    cues = [
        r"\bsuch as\b", r"\bincluding\b", r"\bincludes\b", r"\binclude\b",
        r"\bgiven below\b", r"\bfor example\b", r"\bfor instance\b",
        r"\bthe following\b", r"\bexamples?\b", r"\blike\b",
    ]
    for cue in cues:
        if re.search(cue, pt, re.I):
            return True
    return False


def parse_webmd_topic(item: dict, html: str, final_url: str, cfg: Dict[str, Any]) -> dict:
    """Parse a WebMD topic/article page."""
    url = final_url or item.get("url") or ""
    soup = BeautifulSoup(html, "html.parser")
    default_tags = cfg.get("base_tags", ["Health Topics", "WebMD"])

    title = safe_text(soup.find("h1")) or safe_text(soup.find("title"))
    slug = make_slug_from_url(url) if url else (title.lower().replace(" ", "-") if title else "")
    first_letter = (slug[0].upper() if slug else (title[0].upper() if title else ""))

    canonical = None
    c_el = soup.find("link", rel="canonical")
    if c_el and c_el.get("href"):
        canonical = c_el.get("href")

    meta_desc = None
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        meta_desc = md.get("content")

    author = ""
    medically_reviewed_by = ""
    published_date = ""

    authors_span = soup.select_one(".reviewer-info .authors")
    if authors_span:
        names = [a.get_text(strip=True) for a in authors_span.select("a.person") if a.get_text(strip=True)]
        if not names:
            txt = safe_text(authors_span)
            match = re.search(r"Written by\s*[:\-]?\s*(.+)", txt, re.I)
            if match:
                parts = re.split(r",|\band\b", match.group(1))
                names = [p.strip() for p in parts if p.strip()]
        author = ", ".join(names).strip()

    rev_span = soup.select_one(".reviewer-info .reviewer-txt")
    if rev_span:
        rev_person = rev_span.select_one("a.person")
        if rev_person and safe_text(rev_person):
            medically_reviewed_by = safe_text(rev_person)
        else:
            rev_txt = safe_text(rev_span)
            mrev = re.search(r"Medically Reviewed by\s*[:\-]?\s*([^,|on]+)", rev_txt, re.I)
            if mrev:
                medically_reviewed_by = mrev.group(1).strip()

        rev_date_el = rev_span.select_one(".revDate")
        if rev_date_el:
            rev_date_text = safe_text(rev_date_el)
            rev_date_text = re.sub(r"^[Oo]n[\s:,-]*", "", rev_date_text).strip()
            published_date = normalize_date_str(rev_date_text)
        else:
            mdate = re.search(r"\bon\s+([A-Za-z0-9, \-]+)", safe_text(rev_span), re.I)
            if mdate:
                published_date = normalize_date_str(mdate.group(1).strip())

    if not published_date:
        meta_pub = soup.find("meta", attrs={"property": "article:published_time"})
        if meta_pub and meta_pub.get("content"):
            published_date = normalize_date_str(meta_pub.get("content"))
        else:
            meta_pub2 = soup.find("meta", attrs={"name": "pubdate"})
            if meta_pub2 and meta_pub2.get("content"):
                published_date = normalize_date_str(meta_pub2.get("content"))
            else:
                time_tag = soup.find("time")
                if time_tag:
                    published_date = normalize_date_str(time_tag.get("datetime") or safe_text(time_tag))
                else:
                    txt_all = soup.get_text(separator="||", strip=True)
                    m2 = re.search(r"(Published|Updated)\s*[:\-]?\s*([A-Za-z0-9, \-]+)", txt_all)
                    if m2:
                        published_date = normalize_date_str(m2.group(2))

    author = re.sub(r"\s+[,;]\s+", ", ", author).strip()
    medically_reviewed_by = medically_reviewed_by.strip()

    read_time = ""
    rt = soup.find(string=re.compile(r"\bmin read\b", re.I))
    if rt:
        read_time = rt.strip()
    else:
        rt_el = soup.select_one(".reading-time, .read-time, .article-read-time")
        if rt_el:
            read_time = safe_text(rt_el)

    images: List[str] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        images.append(og.get("content"))
    for img in soup.select(".article-body img, .main-article img, .article-image img, img"):
        src = img.get("data-src") or img.get("src") or img.get("data-lazy-src")
        if src and src.startswith("http") and src not in images:
            images.append(src)

    pdfs: List[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href")
        if href and href.lower().endswith(".pdf"):
            pdfs.append(href)

    related_links: List[dict] = []
    for sel in [".related-links a", ".related a", ".module-related a", ".more-like-this a"]:
        for a in soup.select(sel):
            href = a.get("href")
            text = safe_text(a)
            if href and href.startswith("http"):
                related_links.append({"url": href, "text": text})

    sources: List[str] = []
    src_div = soup.select_one("div.sources-section, .sources-section")
    if src_div:
        for p in src_div.find_all("p"):
            txt = safe_text(p)
            if not txt:
                continue
            if re.match(r"SOURCES\s*[:]?$", txt, re.I):
                continue
            sources.append(txt)
    else:
        msrc = re.search(r"Sources?\s*[:\-]\s*(.+)$", soup.get_text(separator="\n"), re.I | re.M)
        if msrc:
            sources.append(msrc.group(1).strip())

    sections: List[dict] = []
    container = soup.select_one(".article-body, .article-content, .main-article, #article") or soup.body

    pages = container.select(".article-page") if container.select(".article-page") else [container]

    for page in pages:
        for sec in page.find_all("section", recursive=False) + page.find_all("section", recursive=True):
            if not getattr(sec, "name", None):
                continue
            sec_cls = " ".join(sec.get("class") or [])
            if "ad" in sec_cls.lower() or "advert" in sec_cls.lower():
                continue

            heading_el = sec.find(["h2", "h3"])
            heading_text = safe_text(heading_el) if heading_el else None

            if not heading_text:
                first_p = sec.find("p")
                if first_p:
                    strong = first_p.find("strong") or first_p.find("b")
                    if strong and safe_text(strong):
                        heading_text = safe_text(strong)
                        try:
                            strong.extract()
                        except Exception:
                            pass

            content_blocks: List[dict] = []
            section_bullets_combined: List[str] = []

            for child in sec.children:
                if getattr(child, "name", None) is None:
                    continue
                tag = child.name.lower()
                if tag == "pagebreak":
                    continue

                if tag == "p":
                    txt = safe_text(child).replace("\xa0", " ").strip()
                    if txt:
                        content_blocks.append({"type": "paragraph", "text": txt, "associated_bullets": None})

                elif tag in ["ul", "ol"]:
                    items = []
                    for li in child.find_all("li", recursive=False):
                        t = safe_text(li).replace("\xa0", " ").strip()
                        if t:
                            items.append(t)
                    if items:
                        if content_blocks and content_blocks[-1]["type"] == "paragraph" and paragraph_introduces_list(content_blocks[-1]["text"]):
                            content_blocks[-1]["associated_bullets"] = items
                        else:
                            content_blocks.append({"type": "bullets", "items": items})
                        section_bullets_combined.extend(items)

                elif tag in ["div", "aside", "figure", "article", "section"]:
                    cls = " ".join(child.get("class") or [])
                    if "ad" in cls.lower() or "instream-related-mod" in cls.lower():
                        for a in child.select("a[href]"):
                            href = a.get("href")
                            txt = safe_text(a)
                            if href and href.startswith("http"):
                                related_links.append({"url": href, "text": txt})
                        continue

                    for p in child.find_all("p", recursive=True):
                        txt = safe_text(p).replace("\xa0", " ").strip()
                        if txt:
                            content_blocks.append({"type": "paragraph", "text": txt, "associated_bullets": None})

                    for ul in child.find_all(["ul", "ol"], recursive=True):
                        items = []
                        for li in ul.find_all("li", recursive=False):
                            t = safe_text(li).replace("\xa0", " ").strip()
                            if t:
                                items.append(t)
                        if items:
                            if content_blocks and content_blocks[-1]["type"] == "paragraph" and paragraph_introduces_list(content_blocks[-1]["text"]):
                                content_blocks[-1]["associated_bullets"] = items
                            else:
                                content_blocks.append({"type": "bullets", "items": items})
                            section_bullets_combined.extend(items)

                else:
                    for p in child.select("p"):
                        txt = safe_text(p).replace("\xa0", " ").strip()
                        if txt:
                            content_blocks.append({"type": "paragraph", "text": txt, "associated_bullets": None})
                    for ul in child.select("ul, ol"):
                        items = []
                        for li in ul.find_all("li"):
                            t = safe_text(li).replace("\xa0", " ").strip()
                            if t:
                                items.append(t)
                        if items:
                            if content_blocks and content_blocks[-1]["type"] == "paragraph" and paragraph_introduces_list(content_blocks[-1]["text"]):
                                content_blocks[-1]["associated_bullets"] = items
                            else:
                                content_blocks.append({"type": "bullets", "items": items})
                            section_bullets_combined.extend(items)

            paragraph_texts = [b["text"] for b in content_blocks if b["type"] == "paragraph"]
            bullets_list = section_bullets_combined if section_bullets_combined else None
            heading_out = heading_text if heading_text or not paragraph_texts else None

            sections.append({
                "heading": heading_out,
                "content": paragraph_texts if paragraph_texts else None,
                "bullets": bullets_list,
                "content_blocks": content_blocks,
            })

    if not sections:
        intro_ps = container.select("p")[:8]
        content_blocks = []
        for p in intro_ps:
            txt = safe_text(p).replace("\xa0", " ").strip()
            if txt:
                content_blocks.append({"type": "paragraph", "text": txt, "associated_bullets": None})
        paragraph_texts = [b["text"] for b in content_blocks]
        if paragraph_texts:
            sections.append({
                "heading": "Overview",
                "content": paragraph_texts,
                "bullets": None,
                "content_blocks": content_blocks,
            })

    if not canonical and url:
        canonical = url
    if not meta_desc:
        first_p = container.find("p") if container else None
        meta_desc = safe_text(first_p)

    return {
        "url": url,
        "title": title or "",
        "slug": slug or "",
        "published_date": published_date or "",
        "first_letter": first_letter or "",
        "author": author or "",
        "medically_reviewed_by": medically_reviewed_by or "",
        "read_time": read_time or "",
        "sections": sections,
        "pdfs": list(dict.fromkeys(pdfs)),
        "images": images,
        "related_links": related_links,
        "sources": sources,
        "meta_description": meta_desc or "",
        "canonical_url": canonical or "",
        "tags": default_tags,
        "scrape_timestamp_utc": utc_now_iso(),
    }


# ----------------------------
# WHO news
# ----------------------------

def parse_who_news(item: dict, cfg: Dict[str, Any]) -> Optional[dict]:
    """Fetch and parse a WHO news item page."""
    url = item.get("url")
    if not url:
        return None

    settings = who_request_settings(cfg)
    resp = requests.get(url, headers=settings["headers"], timeout=settings["timeout"])
    resp.raise_for_status()
    base = resp.url
    soup = BeautifulSoup(resp.text, "html.parser")

    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else None

    date_el = soup.select_one(".sf-item-header-wrapper span.timestamp") or soup.select_one("span.timestamp")
    date = parse_date_str(date_el.get_text(strip=True)) if date_el else None

    header_tag_items = [t.get_text(strip=True) for t in soup.select(".sf-item-header-wrapper .sf-tags-list-item")]
    header_tag_items = [t for t in header_tag_items if t]

    item_type = header_tag_items[0] if header_tag_items else None

    location = None
    header_locations = header_tag_items[1:] if len(header_tag_items) > 1 else []
    if header_locations:
        location = " | ".join(header_locations)

    article = soup.select_one("article.sf-detail-body-wrapper, article, div.sf-detail-body-wrapper")
    first_para = None
    if article:
        first_para = article.select_one("p")
    else:
        first_para = soup.select_one("div.main-content p, article p, .sf-content-block p")

    if first_para:
        sibs = list(first_para.previous_siblings)
        for s in reversed(sibs):
            txt = getattr(s, "get_text", lambda **k: str(s))().strip()
            if txt and txt.upper() == txt and 1 <= len(txt) < 80:
                location = txt
                break

    lead = None
    if first_para:
        lead = first_para.get_text(strip=True)

    content_container = soup.select_one("article.sf-detail-body-wrapper div")
    header_h2 = soup.select_one("div.sf-item-header-wrapper h2")
    initial_heading = header_h2.get_text(strip=True) if header_h2 else None
    sections = parse_article_sections(content_container, resp.url, initial_heading=initial_heading) if content_container else []

    if not content_container:
        content_container = soup

    references = []
    if content_container:
        for a in content_container.select("a[href]"):
            href = a["href"]
            full = urljoin(base, href)
            text = a.get_text(" ", strip=True)
            references.append({"text": text, "url": full})

    contacts = extract_media_contacts_from_page(soup)

    topics = item_type

    author = None
    if contacts:
        names = [c.get("name") for c in contacts if c.get("name")]
        author = ", ".join(names) if names else None
    author = validate_author(author)

    og_image = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        og_image = urljoin(base, og["content"])

    images = extract_images_from_content(content_container, base, og_image)

    return {
        "url": base,
        "title": title,
        "published_date": date,
        "author": author,
        "medically_reviewed_by": "World Health Organization (WHO)",
        "topics": topics,
        "location": location,
        "lead": lead,
        "sections": sections,
        "references": references,
        "images": images,
        "tags": [t for t in ["WHO", title, item_type] if t],
        "scrape_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "first_seen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ----------------------------
# WHO Disease Outbreak News
# ----------------------------

def parse_don_sections(article_div, base_url: str) -> List[dict]:
    """Parse a Disease Outbreak News article into sections."""
    sections: List[dict] = []

    if not article_div:
        return sections

    def append_list_or_text(contents, items):
        if contents and isinstance(contents[-1], str):
            contents[-1] = {"text": contents[-1], "bullets": items}
        else:
            contents.append({"text": None, "bullets": items})

    def collect_from_node(node, contents):
        if isinstance(node, Tag):
            if node.name == "p":
                text = node.get_text(" ", strip=True)
                if text:
                    contents.append(text)
            elif node.name in ["ul", "ol"]:
                items = [li.get_text(" ", strip=True) for li in node.find_all("li")]
                if items:
                    append_list_or_text(contents, items)
            elif node.name not in ["h3", "div"]:
                text = node.get_text(" ", strip=True)
                if text and len(text) > 3:
                    contents.append(text)
        elif isinstance(node, NavigableString):
            text = node.strip()
            if text and len(text) > 3:
                contents.append(text)

    heading_tags = article_div.find_all("h3", class_="don-section")

    if not heading_tags:
        contents: List[Any] = []
        for el in article_div.children:
            if isinstance(el, Tag):
                if el.get("class"):
                    classes = " ".join(el.get("class", []))
                    if "arrowed-link" in classes or "don-images" in classes:
                        continue
            collect_from_node(el, contents)
        if contents:
            sections.append({"heading": None, "content": contents})
    else:
        for heading_tag in heading_tags:
            heading = heading_tag.get_text(strip=True)
            content_blocks: List[Any] = []
            tables: List[List[List[str]]] = []
            images: List[str] = []

            content_div = heading_tag.find_next("div", class_="don-content")

            if content_div:
                for child in content_div.children:
                    if isinstance(child, Tag):
                        if child.get("class"):
                            classes = " ".join(child.get("class", []))
                            if "arrowed-link" in classes or "don-images" in classes:
                                continue

                        if child.name == "table":
                            rows = []
                            for tr in child.find_all("tr"):
                                cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                                if cols:
                                    rows.append(cols)
                            if rows:
                                tables.append(rows)
                        elif child.name == "figure" or child.find("img"):
                            imgs = child.find_all("img")
                            for im in imgs:
                                src = im.get("src") or im.get("data-src")
                                if src:
                                    images.append(urljoin(base_url, src))
                        elif child.name == "div":
                            for nested in child.children:
                                collect_from_node(nested, content_blocks)
                        else:
                            collect_from_node(child, content_blocks)
                    else:
                        collect_from_node(child, content_blocks)

            section_obj: Dict[str, Any] = {
                "heading": heading,
                "content": content_blocks if content_blocks else None,
            }
            if tables:
                section_obj["tables"] = tables
            if images:
                section_obj["images"] = images
            sections.append(section_obj)

    return sections


def parse_who_don(item: dict, cfg: Dict[str, Any]) -> Optional[dict]:
    """Fetch and parse a WHO Disease Outbreak News article."""
    url = item.get("url")
    if not url:
        return None

    settings = who_request_settings(cfg)
    try:
        resp = requests.get(url, headers=settings["headers"], timeout=settings["timeout"])
        resp.raise_for_status()
    except Exception:
        raise
    base = resp.url
    soup = BeautifulSoup(resp.text, "html.parser")

    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else None

    date_el = soup.select_one(".sf-item-header-wrapper span.timestamp") or soup.select_one("span.timestamp")
    date_text = date_el.get_text(strip=True) if date_el else None
    date = parse_date_str(date_text) if date_text else None

    article_container = soup.select_one("article.sf-detail-body-wrapper.don-revamp")
    if not article_container:
        article_container = soup.select_one("article.sf-detail-body-wrapper")

    sections = parse_don_sections(article_container, base)

    references = []
    if article_container:
        for a in article_container.select("a[href]"):
            href = a["href"]
            full = urljoin(base, href)
            text = a.get_text(strip=True)
            if text and not text.startswith("#"):
                references.append({"text": text, "url": full})

    images = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        images.append(urljoin(base, og["content"]))

    contacts = extract_media_contacts_from_page(soup)

    author = None
    if contacts:
        names = [c.get("name") for c in contacts if c.get("name")]
        author = ", ".join(names) if names else None
    author = validate_author(author)

    return {
        "url": base,
        "title": title,
        "published_date": date,
        "author": author,
        "medically_reviewed_by": "World Health Organization (WHO)",
        "topics": "Disease Outbreak",
        "sections": sections,
        "references": references,
        "images": images,
        "tags": [t for t in ["WHO", "Disease Outbreak", title] if t],
        "scraped_at": datetime.now().isoformat(),
        "first_seen_utc": datetime.utcnow().isoformat(),
    }


# ----------------------------
# WHO feature stories
# ----------------------------

def parse_who_feature_story(item: dict, cfg: Dict[str, Any]) -> Optional[dict]:
    """Fetch and parse a WHO feature story page."""
    url = item.get("url")
    if not url:
        return None

    settings = who_request_settings(cfg)
    resp = requests.get(url, headers=settings["headers"], timeout=settings["timeout"])
    resp.raise_for_status()
    base = resp.url
    soup = BeautifulSoup(resp.text, "html.parser")

    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else None

    date_el = soup.select_one(".sf-item-header-wrapper span.timestamp") or soup.select_one("span.timestamp")
    date = parse_date_str(date_el.get_text(strip=True)) if date_el else None

    header_tag_items = [t.get_text(strip=True) for t in soup.select(".sf-item-header-wrapper .sf-tags-list-item")]
    header_tag_items = [t for t in header_tag_items if t]

    item_type = header_tag_items[0] if header_tag_items else "Feature Story"

    location = None
    header_locations = header_tag_items[1:] if len(header_tag_items) > 1 else []
    if header_locations:
        location = " | ".join(header_locations)

    article = soup.select_one("article.sf-detail-body-wrapper, article, div.sf-detail-body-wrapper")

    lead = None
    if article:
        first_para = article.select_one("p")
        if first_para:
            lead = first_para.get_text(strip=True)

    content_container = soup.select_one("article.sf-detail-body-wrapper div")
    header_h2 = soup.select_one("div.sf-item-header-wrapper h2")
    initial_heading = header_h2.get_text(strip=True) if header_h2 else None
    sections = parse_article_sections(content_container, resp.url, initial_heading=initial_heading) if content_container else []

    if not content_container:
        content_container = soup

    references = []
    if content_container:
        for a in content_container.select("a[href]"):
            href = a["href"]
            full = urljoin(base, href)
            text = a.get_text(" ", strip=True)
            references.append({"text": text, "url": full})

    contacts = extract_media_contacts_from_page(soup)

    topics = item_type

    author = None
    if contacts:
        names = [c.get("name") for c in contacts if c.get("name")]
        author = ", ".join(names) if names else None
    author = validate_author(author)

    og_image = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        og_image = urljoin(base, og["content"])

    images = extract_images_from_content(content_container, base, og_image)

    return {
        "url": base,
        "title": title,
        "published_date": date,
        "author": author,
        "medically_reviewed_by": "World Health Organization (WHO)",
        "topics": topics,
        "location": location,
        "lead": lead,
        "sections": sections,
        "references": references,
        "images": images,
        "tags": [t for t in ["WHO", "Feature Story", title, item_type] if t],
        "scrape_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "first_seen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ----------------------------
# WHO fact sheets
# ----------------------------

def parse_who_fact_sheet(item: dict, cfg: Dict[str, Any]) -> Optional[dict]:
    """Fetch and parse a WHO fact-sheet page."""
    url = item.get("url")
    if not url:
        return None

    settings = who_request_settings(cfg)
    session = requests_session_with_retries(settings["headers"].get("User-Agent", "Mozilla/5.0"))

    date_regex = re.compile(r"\b(?:\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)\b")

    def safe_get(target_url: str):
        for attempt in range(3):
            try:
                r = session.get(target_url, timeout=settings["timeout"])
                r.raise_for_status()
                return r
            except Exception:
                if attempt < 2:
                    time.sleep(1.0 + attempt * 0.5)
                    continue
                raise

    r = safe_get(url)
    soup = BeautifulSoup(r.text, "lxml")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else None

    published_date = None
    if title_tag:
        for sib in title_tag.next_siblings:
            if isinstance(sib, NavigableString):
                s = sib.strip()
                if not s:
                    continue
                m = date_regex.search(s)
                if m:
                    published_date = m.group(0)
                    break
            elif isinstance(sib, Tag):
                txt = sib.get_text(" ", strip=True)
                m = date_regex.search(txt)
                if m:
                    published_date = m.group(0)
                    break

    if not published_date:
        top_text = soup.get_text(" ", strip=True)[:400]
        m = date_regex.search(top_text)
        if m:
            published_date = m.group(0)

    main = soup.find("main")
    if not main:
        main = soup.body

    sections: List[dict] = []
    heading_tags = main.find_all(["h2", "h3", "h4"])
    if not heading_tags:
        body_text = main.get_text("\n", strip=True)
        sections.append({"heading": None, "content": [body_text] if body_text else None})
    else:
        for h in heading_tags:
            heading = h.get_text(strip=True)
            contents: List[Any] = []
            tables: List[List[List[str]]] = []
            images: List[str] = []

            for sib in h.next_siblings:
                if isinstance(sib, Tag) and sib.name in ["h2", "h3", "h4"]:
                    break
                if isinstance(sib, Tag):
                    if sib.name == "p":
                        txt = sib.get_text(" ", strip=True)
                        if txt:
                            contents.append(txt)
                    elif sib.name in ["ul", "ol"]:
                        items = [li.get_text(" ", strip=True) for li in sib.find_all("li")]
                        if items:
                            if contents and isinstance(contents[-1], str):
                                contents[-1] = {"text": contents[-1], "bullets": items}
                            else:
                                contents.append({"text": None, "bullets": items})
                    elif sib.name == "table":
                        rows = []
                        for tr in sib.find_all("tr"):
                            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                            if cols:
                                rows.append(cols)
                        if rows:
                            tables.append(rows)
                    elif sib.name == "figure" or sib.find("img"):
                        imgs = sib.find_all("img")
                        for im in imgs:
                            src = im.get("src") or im.get("data-src")
                            if src:
                                images.append(urljoin("https://www.who.int", src))
                    else:
                        txt = sib.get_text(" ", strip=True)
                        if txt:
                            contents.append(txt)
                elif isinstance(sib, NavigableString):
                    txt = sib.strip()
                    if txt:
                        contents.append(txt)

            section_obj: Dict[str, Any] = {
                "heading": heading,
                "content": contents if contents else None,
            }
            if heading and heading.strip().lower() == "references":
                refs = []
                for content_item in contents:
                    if isinstance(content_item, dict):
                        text = (content_item.get("text") or "").strip()
                        if text:
                            refs.append(text)
                        bullets = content_item.get("bullets") or []
                        if isinstance(bullets, str):
                            bullets = [bullets]
                        if isinstance(bullets, list):
                            refs.extend([b.strip() for b in bullets if b])
                    elif isinstance(content_item, str):
                        if content_item.strip():
                            refs.append(content_item.strip())
                section_obj["references"] = refs if refs else None
            if tables:
                section_obj["tables"] = tables
            if images:
                section_obj["images"] = images
            sections.append(section_obj)

    pdf_links = []
    for a in main.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            pdf_links.append(urljoin("https://www.who.int", href))

    references = []
    for sec in sections:
        refs = sec.get("references")
        if isinstance(refs, list):
            references.extend([r for r in refs if r])
    if not references:
        references = None

    all_imgs = []
    for im in main.find_all("img"):
        src = im.get("src") or im.get("data-src") or im.get("data-lazy-src")
        if src:
            all_imgs.append(urljoin("https://www.who.int", src))

    slug = slugify(title or url.rsplit("/", 1)[-1])
    first_letter = (slug[0].upper() if slug else "X")

    return {
        "url": url,
        "title": title,
        "slug": slug,
        "published_date": published_date,
        "author": "World Health Organization (WHO)",
        "first_letter": first_letter,
        "sections": sections,
        "references": references,
        "pdfs": list(sorted(set(pdf_links))),
        "images": list(sorted(set(all_imgs))),
        "tags": ["Health Fact", slug, title, "WHO"],
        "scrape_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ----------------------------
# Parser registry
# ----------------------------

PARSERS: Dict[str, Any] = {
    "harvard": parse_harvard,
    "webmd_topic": parse_webmd_topic,
    "who_news": parse_who_news,
    "who_don": parse_who_don,
    "who_feature_story": parse_who_feature_story,
    "who_fact_sheet": parse_who_fact_sheet,
}


def get_parser(parser_name: str):
    if parser_name not in PARSERS:
        raise KeyError(f"No parser '{parser_name}'. Available: {', '.join(sorted(PARSERS))}")
    return PARSERS[parser_name]
