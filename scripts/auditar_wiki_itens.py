#!/usr/bin/env python3
"""Read-only Priston Wiki audit. Never rewrites the live catalog.

Usage:
  pip install requests beautifulsoup4
  python scripts/auditar_wiki_itens.py
Outputs assets/wiki-auditoria.json for human verification.
"""
import json
import re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT = "https://wiki.priston.com.br"
API = ROOT + "/api.php"
PAGES = {
    "Itda": "ARMADURAS", "Itdr": "ROUPÕES", "Itdb": "BOTAS",
    "Itdg": "LUVAS", "Itds": "ESCUDOS", "Itoa": "ACESSÓRIOS",
    "Itom": "BRACELETES", "Itwa": "LANÇAS", "Itwc": "MARTELOS",
    "Itwh": "Garras", "Itwm": "MACHADOS", "Itwp": "Foices",
    "Itws": "Espadas", "Itwt": "Cajados/Varinhas",
}
CATALOG = Path("assets/itens-catalogo.json")
OUT = Path("assets/wiki-auditoria.json")
session = requests.Session()
session.headers["User-Agent"] = "NoCryCatalogAudit/1.0 (guild internal reference)"
local = json.loads(CATALOG.read_text(encoding="utf-8"))
audit = {"source": ROOT, "categories": {}, "items": [], "errors": []}
for category, page in PAGES.items():
    try:
        response = session.get(API, params={
            "action": "parse", "page": page, "prop": "text", "format": "json"
        }, timeout=25)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise ValueError(data["error"].get("info", "Wiki page unavailable"))
        html = data["parse"]["text"]["*"]
        soup = BeautifulSoup(html, "html.parser")
        # Wiki layouts differ between categories. Gather candidates but do not
        # infer image-to-item relationships from the order of image files.
        content = soup.get_text(" ", strip=True)
        audit["categories"][category] = {
            "page": page,
            "url": ROOT + "/index.php/" + page.replace(" ", "_"),
            "available": True,
            "image_count": len(soup.select("img")),
        }
        for item in (x for x in local if x.get("category") == category):
            name = item["name"]
            found = re.search(r"(?<!\\w)" + re.escape(name) + r"(?!\\w)", content, re.I)
            # Image and level remain unverified until associated with the same
            # item block in the Wiki, not merely present on the category page.
            audit["items"].append({
                "id": item["id"], "category": category, "local_name": name,
                "local_level": item.get("level"), "local_image": item.get("image"),
                "name_found_in_wiki": bool(found),
                "level_verified": False, "image_verified": False,
                "status": "requires_item_level_and_image_validation",
            })
    except (requests.RequestException, ValueError, KeyError) as exc:
        audit["errors"].append({"category": category, "page": page, "error": str(exc)})
OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Audit written: {OUT} | {len(audit['items'])} items | {len(audit['errors'])} errors")
if audit["errors"]:
    raise SystemExit(1)
