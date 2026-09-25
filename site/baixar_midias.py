#!/usr/bin/env python3
"""Baixa do streaming o que o site mostra mas não está no repositório:

- miniatura de cada vídeo do YouTube → conteudo/midias/videos/<id>.jpg
- capa de cada lançamento sem `capa` e com `capa_fonte` (páginas do Spotify ou do
  Bandcamp, tentadas em ordem) → conteudo/midias/capas/<slug>.jpg

Roda no CI antes do build (a rede do CI alcança esses serviços). Nada aqui para o
deploy: se algo falhar, o build usa a capa do lançamento ou a arte tipográfica.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIDIAS = RAIZ / "conteudo" / "midias"
AGENTE = {"User-Agent": "Mozilla/5.0 (demophobia.band build)"}


def baixar(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENTE), timeout=20) as r:
        return r.read()


def imagem_da_pagina(pagina: str) -> str:
    """URL da capa: oEmbed no Spotify, og:image no Bandcamp (e em qualquer outra página)."""
    if "open.spotify.com" in pagina:
        dados = json.loads(baixar("https://open.spotify.com/oembed?url=" + urllib.parse.quote(pagina, safe="")))
        return dados["thumbnail_url"]
    html = baixar(pagina).decode("utf-8", "replace")
    achado = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html)
    if not achado:
        raise ValueError("página sem og:image")
    url = achado.group(1)
    return re.sub(r"_\d+\.jpg$", "_10.jpg", url)   # Bandcamp: _10 = maior resolução


def miniaturas() -> int:
    falhas = 0
    destino = MIDIAS / "videos"
    destino.mkdir(parents=True, exist_ok=True)
    for arq in sorted((RAIZ / "conteudo" / "videos").glob("*.json")):
        yt = json.loads(arq.read_text(encoding="utf-8"))["youtube"]
        alvo = destino / f"{yt}.jpg"
        if alvo.exists():
            continue
        try:
            alvo.write_bytes(baixar(f"https://i.ytimg.com/vi/{yt}/hqdefault.jpg"))
            print(f"miniatura: {yt}")
        except Exception as e:
            falhas += 1
            print(f"aviso: miniatura de {yt} não baixou ({e})", file=sys.stderr)
    return falhas


def capas() -> int:
    falhas = 0
    destino = MIDIAS / "capas"
    for arq in sorted((RAIZ / "conteudo" / "lancamentos").glob("*.json")):
        l = json.loads(arq.read_text(encoding="utf-8"))
        if l.get("capa") or not l.get("capa_fonte"):
            continue
        alvo = destino / f"{l['slug']}.jpg"
        if alvo.exists():
            continue
        fontes = l["capa_fonte"] if isinstance(l["capa_fonte"], list) else [l["capa_fonte"]]
        for fonte in fontes:
            try:
                alvo.write_bytes(baixar(imagem_da_pagina(fonte)))
                print(f"capa: {l['slug']} ← {fonte}")
                break
            except Exception as e:
                print(f"aviso: capa de {l['slug']} não veio de {fonte} ({e})", file=sys.stderr)
        else:
            falhas += 1
    return falhas


if __name__ == "__main__":
    print(f"mídias do streaming ok, {miniaturas() + capas()} falhas")
