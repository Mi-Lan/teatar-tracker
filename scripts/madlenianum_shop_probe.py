"""Temporary: can the tracker sign in to Madlenianum's ticket shop, and what does the page look like after?

Run by .github/workflows/shop-login-test.yml with MADLENIANUM_EMAIL / MADLENIANUM_PASSWORD (GitHub secrets).
The log of a public repo is public, so this prints only: whether the sign-in worked, the page's structure,
and show titles with their dates (which the shop shows to anyone with an account). It never prints the
email, the password, cookies, or text from the account area. GET requests only after the sign-in:
nothing is selected, reserved or bought.
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter

import httpx
from bs4 import BeautifulSoup

BASE = "https://tickets.operatheatremadlenianum.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
DATE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.(\s?\d{4}\.?)?|\b\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}\b|(?i:januar|februar|mart|april|maj|jun|jul|avgust|septembar|oktobar|novembar|decembar)")
ACCOUNT = re.compile(r"(?i)nalog|account|profil|logout|odjav|korisni|user|kupovine|porudz|narudz|order")

email, password = os.environ.get("MADLENIANUM_EMAIL", "").strip(), os.environ.get("MADLENIANUM_PASSWORD", "")
if not email or not password:
    sys.exit("MADLENIANUM_EMAIL / MADLENIANUM_PASSWORD are not set: add them under Settings → Secrets and variables → Actions.")


def clean(text: str, limit: int = 160) -> str:
    """Whitespace collapsed, the account's email (and its name part) masked, cut to `limit`."""
    text = re.sub(r"\s+", " ", text or "").strip()
    for secret in (email, email.split("@")[0], password):
        if len(secret) >= 3:
            text = re.sub(re.escape(secret), "[hidden]", text, flags=re.I)
    return text[:limit]


def chain(el, depth: int = 5) -> str:
    parts = []
    while el is not None and el.name not in (None, "[document]", "html", "body") and len(parts) < depth:
        parts.append(el.name + "".join(f".{c}" for c in (el.get("class") or [])[:3]) + (f"#{el['id']}" if el.get("id") else ""))
        el = el.parent
    return " < ".join(parts)


def in_account_area(el) -> bool:
    for node in [el, *el.parents]:
        if node is None or node.name in (None, "[document]"):
            continue
        marks = " ".join([node.get("id") or "", *(node.get("class") or []), node.get("href") or ""])
        if node.name in ("header", "nav", "footer") or ACCOUNT.search(marks):
            return True
    return False


client = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "sr,en;q=0.8"}, follow_redirects=True, timeout=30)

print("== 1. the public page")
first = client.get(f"{BASE}/sr")
print(f"GET /sr -> {first.status_code}, {len(first.text)} bytes, sign-in form present: {'loginForm' in first.text}, cookies set: {len(client.cookies)}")

print("== 2. sign in")
reply = client.post(
    f"{BASE}/include/action.php",
    data={"fn": "acc_login", "language": "sr", "email": email.lower(), "pass": password},
    headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/sr"},
)
print(f"POST /include/action.php -> {reply.status_code}, {reply.headers.get('content-type')}, {len(reply.text)} bytes")
try:
    data = reply.json()
except ValueError:
    data = None
    print("reply is not JSON; first 200 characters:", clean(reply.text, 200))
if isinstance(data, dict):
    print("reply keys:", sorted(data))
    print("status:", clean(str(data.get("status"))), "| message:", clean(str(data.get("message") or data.get("error") or data.get("msg") or ""), 200))
ok = isinstance(data, dict) and data.get("status") == "ok"

print("== 3. the page after signing in")
page = client.get(f"{BASE}/sr")
soup = BeautifulSoup(page.text, "lxml")
signed_in = "loginForm" not in page.text
print(f"GET /sr -> {page.status_code}, {len(page.text)} bytes, final url {str(page.url).split('?')[0]}")
print("title:", clean(soup.title.get_text() if soup.title else ""), "| sign-in form still shown:", not signed_in)
print("SIGNED IN:", ok and signed_in)
if not signed_in:
    sys.exit("The sign-in did not work (see the reply above). Nothing else to show.")

print("== 4. structure")
print("most used classes:", Counter(c for el in soup.select("[class]") for c in el.get("class")).most_common(70))
print("ids:", [el["id"] for el in soup.select("[id]")][:80])
print("data-* attributes:", Counter(a for el in soup.find_all(True) for a in el.attrs if a.startswith("data-")).most_common(30))
print("forms:", [(f.get("id"), [i.get("name") for i in f.select("input[name], select[name]")]) for f in soup.select("form")])
print("scripts:", [s.get("src") for s in soup.select("script[src]") if "/vendor/" not in (s.get("src") or "")])
names = set()
for s in soup.select("script:not([src])"):
    names |= set(re.findall(r"(?:let|var|const)\s+(\w+)", s.get_text())) | {f"jm.{n}" for n in re.findall(r"\bjm\.(\w+)\s*=", s.get_text())}
print("inline script variable names (no values):", sorted(names)[:60])

print("== 5. links (text shown only outside the account area)")
seen = set()
for a in soup.select("a[href]"):
    href = a["href"]
    if href in seen or href.startswith(("javascript", "mailto", "#")) or "/assets/" in href:
        continue
    seen.add(href)
    account = in_account_area(a) or ACCOUNT.search(href)
    print("  ", clean(re.sub(r"\d{5,}", "N", href) if account else href, 110), "|", "[account/nav]" if account else clean(a.get_text(" "), 70))
    if len(seen) >= 120:
        print("   …")
        break

print("== 6. everything that looks like a date, with where it sits")
shown, blocks = 0, []
for node in soup.find_all(string=DATE):
    el = node.parent
    if el.name in ("script", "style", "option") or in_account_area(el):
        continue
    if shown < 70:
        print("  ", clean(str(node), 60), "  @", chain(el))
        shown += 1
    blocks.append(el)
print(f"({len(blocks)} date-like texts outside the account area)")

print("== 7. the repeating block the dates sit in (first two, as HTML)")
counts = Counter(" ".join(el.get("class")) for el in soup.select("[class]"))
printed = 0
for el in blocks:
    node = el
    while node is not None and node.name not in ("body", "html", "[document]"):
        cls = " ".join(node.get("class") or [])
        if cls and counts[cls] >= 2 and len(node.get_text(" ", strip=True)) > 25 and len(str(node)) > 200:
            break
        node = node.parent
    if node is None or node.name in ("body", "html", "[document]"):
        continue
    print(f"--- block: {chain(node, 3)}  (class used {counts[' '.join(node.get('class') or [])]}×, {len(str(node))} bytes)")
    print(clean(node.prettify(), 3500).replace("> <", ">\n<"))
    printed += 1
    if printed == 2:
        break

print("== 8. a show with several dates: every line mentioning EVITA")
for node in soup.find_all(string=re.compile("(?i)evita")):
    if node.parent.name not in ("script", "style") and not in_account_area(node.parent):
        box = node.parent
        for _ in range(3):
            if box.parent is not None and box.parent.name not in ("body", "html", "[document]") and len(box.parent.get_text(" ", strip=True)) < 600:
                box = box.parent
        print("  ", clean(box.get_text(" "), 400), "  @", chain(node.parent, 4))
