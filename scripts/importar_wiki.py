#!/usr/bin/env python3
"""Importador conservador: nome, nível e imagem devem vir do MESMO bloco da Wiki.

Execute na raiz do repositório:
  pip install requests beautifulsoup4
  python scripts/importar_wiki.py
Não altera o catálogo ativo: gera arquivos de revisão para aprovação.
"""
import json
import re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE = "https://wiki.priston.com.br"
CATEGORIES = {
    "Escudos": "ESCUDOS", "Espadas": "ESPADAS", "Arcos": "ARCOS",
    "Lanças": "LANÇAS", "Machados": "MACHADOS", "Cajados": "CAJADOS",
    "Varinhas": "VARINHAS", "Braceletes": "BRACELETES",
    "Orbitais": "ORBITAIS", "Martelos": "MARTELOS",
    "Garras": "GARRAS", "Foices": "FOICES", "Botas": "BOTAS",
    "Luvas": "LUVAS", "Armaduras": "ARMADURAS", "Roupões": "ROUPÕES",
}
out = Path("assets/wiki-importacao")
out.mkdir(parents=True, exist_ok=True)
http = requests.Session()
http.headers["User-Agent"] = "NoCry-Wiki-Catalog/1.0 (catalog verification)"
verified, rejected, failures = [], [], []
for category, title in CATEGORIES.items():
    try:
        response = http.get(BASE + "/api.php", params={
            "action": "parse", "page": title, "prop": "text", "format": "json"
        }, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise ValueError(payload["error"].get("info", "Wiki page not found"))
        soup = BeautifulSoup(payload["parse"]["text"]["*"], "html.parser")
        # Table rows and gallery items are independent; never pair by global index.
        candidates = soup.select("tr, li.gallerybox")
        for block in candidates:
            raw = block.get_text(" ", strip=True)
            level_match = re.search(r"Nível\\s+Necessário\\s*:\\s*(\\d+)", raw, re.I)
            imgs = [img for img in block.select("img") if img.get("src")]
            if not level_match or len(imgs) != 1:
                continue
            img = imgs[0]
            name = ""
            for node in block.select("th, b, strong, a[title]"):
                txt = node.get_text(" ", strip=True)
                if txt and "Nível" not in txt and len(txt) < 90:
                    name = txt
                    break
            image_url = urljoin(BASE, img.get("src", ""))
            if not name or not image_url.startswith(BASE + "/"):
                rejected.append({"category": category, "text": raw[:180], "reason": "unreliable_name_or_image"})
                continue
            verified.append({"category": category, "name": name, "level": int(level_match.group(1)),
                             "image_url": image_url, "source": BASE + "/index.php/" + title,
                             "status": "extracted_pending_visual_review"})
    except (requests.RequestException, ValueError, KeyError) as exc:
        failures.append({"category": category, "error": str(exc)})
result = {"source": BASE, "status": "NOT_PUBLISHED", "items": verified,
          "rejected": rejected, "failures": failures}
(out / "resultado.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Extraídos: {len(verified)} | rejeitados: {len(rejected)} | falhas: {len(failures)}")
if failures:
    raise SystemExit("Importação parcial; catálogo de produção preservado")
