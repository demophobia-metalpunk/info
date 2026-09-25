#!/usr/bin/env python3
"""Gera o site da Demophobia a partir de conteudo/ e escreve tudo em site/dist/.

    python3 site/build.py              # prévia: tudo com noindex
    python3 site/build.py --producao   # site de verdade: indexável (menos /imprensa/)
    python3 site/build.py --servir 8765

Convenção de escape: campos de texto do JSON são texto puro e passam por esc().
Só campos cujo nome termina em `_html` entram crus, porque são escritos pela banda
no próprio repositório. Nada de template engine: o escape é explícito e visível.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import html
import http.server
import json
import math
import re
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
CONTEUDO = RAIZ / "conteudo"
MIDIAS = CONTEUDO / "midias"
ASSETS = Path(__file__).resolve().parent / "assets"
DIST = Path(__file__).resolve().parent / "dist"

DOMINIO = "https://demophobia.com.br"
CONTRATO_INDEX = "demophobia-index/1"
POR_PAGINA = 10
FUSO = ZoneInfo("America/Sao_Paulo")

MENU = [
    ("/banda/", "Banda"),
    ("/discografia/", "Discografia"),
    ("/shows/", "Shows"),
    ("/videos/", "Vídeos"),
    ("/noticias/", "Notícias"),
    ("/loja/", "Loja"),
]
TIPOS_LANCAMENTO = {"album": "Álbum", "ep": "EP", "single": "Single"}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
ATUAL = ' aria-current="page"'
MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


class ErroDeConteudo(Exception):
    pass


# ---------------------------------------------------------------- utilidades

def esc(texto) -> str:
    return html.escape("" if texto is None else str(texto), quote=True)


def json_ld(obj) -> str:
    bruto = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return '<script type="application/ld+json">' + bruto.replace("</", "<\\/") + "</script>"


def ler_json(caminho: Path):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ErroDeConteudo(f"{caminho.relative_to(RAIZ)}: JSON inválido ({e})")


def exigir(obj: dict, campos: list[str], origem: str):
    faltando = [c for c in campos if c not in obj or obj[c] in ("", None, [])]
    if faltando:
        raise ErroDeConteudo(f"{origem}: faltam os campos {', '.join(faltando)}")


def parse_data(valor: str, origem: str) -> tuple[dt.date, str]:
    """Aceita AAAA, AAAA-MM ou AAAA-MM-DD. Devolve a data e a precisão."""
    for fmt, prec in (("%Y-%m-%d", "dia"), ("%Y-%m", "mes"), ("%Y", "ano")):
        try:
            return dt.datetime.strptime(str(valor), fmt).date(), prec
        except ValueError:
            continue
    raise ErroDeConteudo(f"{origem}: data '{valor}' fora do formato AAAA, AAAA-MM ou AAAA-MM-DD")


def data_extenso(d: dt.date, prec: str) -> str:
    if prec == "ano":
        return str(d.year)
    if prec == "mes":
        return f"{MESES[d.month - 1]} de {d.year}"
    dia = "1º" if d.day == 1 else str(d.day)
    return f"{dia} de {MESES[d.month - 1]} de {d.year}"


def preco_br(valor: float) -> str:
    inteiro, dec = f"{valor:.2f}".split(".")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    return f"R$ {inteiro},{dec}"


# ---------------------------------------------------------------- mídias

class Midias:
    """Registra toda mídia usada; só essas são copiadas, e a que faltar trava o build."""

    def __init__(self):
        self.usadas: set[str] = set()

    def url(self, relativo: str, origem: str) -> str:
        if not (MIDIAS / relativo).is_file():
            raise ErroDeConteudo(f"{origem}: mídia não encontrada em conteudo/midias/{relativo}")
        self.usadas.add(relativo)
        return "/midias/" + relativo

    def existe(self, relativo: str) -> bool:
        return (MIDIAS / relativo).is_file()

    def copiar(self):
        for rel in sorted(self.usadas):
            destino = DIST / "midias" / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MIDIAS / rel, destino)


# ---------------------------------------------------------------- acervo

class Acervo:
    def __init__(self, hoje: dt.date):
        self.hoje = hoje
        self.banda = ler_json(CONTEUDO / "banda.json")
        exigir(self.banda, ["nome", "linha_fina", "formacao", "bio_html", "contato", "redes", "destaque"], "banda.json")
        self.galeria = ler_json(CONTEUDO / "galeria.json")
        self.imprensa = ler_json(CONTEUDO / "imprensa.json")

        self.lancamentos = self._pasta("lancamentos", ["slug", "titulo", "tipo", "data", "linha_fina"])
        for l in self.lancamentos:
            if l["tipo"] not in TIPOS_LANCAMENTO:
                raise ErroDeConteudo(f"lancamentos/{l['slug']}.json: tipo '{l['tipo']}' não é album, ep ou single")
            l["_data"], l["_prec"] = parse_data(l["data"], f"lancamentos/{l['slug']}.json")
        self.lancamentos.sort(key=lambda l: l["_data"], reverse=True)
        self.lanc_por_slug = {l["slug"]: l for l in self.lancamentos}

        self.videos = self._pasta("videos", ["slug", "titulo", "youtube", "ano"])
        self.videos.sort(key=lambda v: v.get("ordem", 999))
        self.video_por_slug = {v["slug"]: v for v in self.videos}

        self.shows = []
        for arq in sorted((CONTEUDO / "shows").glob("*.json")):
            s = ler_json(arq)
            exigir(s, ["data"], f"shows/{arq.name}")
            s["_data"], _ = parse_data(s["data"], f"shows/{arq.name}")
            s["_slug"] = arq.stem
            self.shows.append(s)
        self.proximos = sorted([s for s in self.shows if s["_data"] >= hoje], key=lambda s: s["_data"])
        self.passados = sorted([s for s in self.shows if s["_data"] < hoje], key=lambda s: s["_data"], reverse=True)

        # notícia com data futura fica guardada até o dia; o build diário a publica
        todas = self._pasta("noticias", ["slug", "titulo", "data", "linha_fina", "conteudo_html"])
        for n in todas:
            n["_data"], _ = parse_data(n["data"], f"noticias/{n['slug']}.json")
        self.noticias = sorted([n for n in todas if n.get("publicado", True) and n["_data"] <= hoje],
                               key=lambda n: n["_data"], reverse=True)

        # na mídia: o que saiu sobre a banda em outros veículos (links para fora)
        self.midia = self._pasta("na-midia", ["slug", "titulo", "veiculo", "url", "data", "tipo"])
        for m in self.midia:
            m["_data"], m["_prec"] = parse_data(m["data"], f"na-midia/{m['slug']}.json")
            if m.get("sobre") and m["sobre"] not in self.lanc_por_slug:
                raise ErroDeConteudo(f"na-midia/{m['slug']}.json: sobre '{m['sobre']}' não existe em lancamentos/")
        self.midia.sort(key=lambda m: (m["_data"], m["slug"]), reverse=True)

        produtos = self._pasta("loja", ["slug", "titulo", "linha_fina", "fotos"])
        self.produtos = [p for p in produtos if p.get("publicado", True)]
        self.produtos.sort(key=lambda p: (p.get("ordem", 999), p["titulo"]))

        self._conferir_referencias()

    def _pasta(self, nome: str, campos: list[str]) -> list[dict]:
        itens = []
        pasta = CONTEUDO / nome
        if not pasta.is_dir():
            return itens
        for arq in sorted(pasta.glob("*.json")):
            obj = ler_json(arq)
            exigir(obj, campos, f"{nome}/{arq.name}")
            if obj.get("slug") and obj["slug"] != arq.stem:
                raise ErroDeConteudo(f"{nome}/{arq.name}: o slug '{obj['slug']}' precisa ser igual ao nome do arquivo")
            itens.append(obj)
        return itens

    def _conferir_referencias(self):
        if self.banda["destaque"] not in self.lanc_por_slug:
            raise ErroDeConteudo(f"banda.json: destaque '{self.banda['destaque']}' não existe em lancamentos/")
        for l in self.lancamentos:
            for v in l.get("videos", []):
                if v not in self.video_por_slug:
                    raise ErroDeConteudo(f"lancamentos/{l['slug']}.json: vídeo '{v}' não existe em videos/")
            if l.get("do_album") and l["do_album"] not in self.lanc_por_slug:
                raise ErroDeConteudo(f"lancamentos/{l['slug']}.json: do_album '{l['do_album']}' não existe")
        for v in self.videos:
            if v.get("lancamento") and v["lancamento"] not in self.lanc_por_slug:
                raise ErroDeConteudo(f"videos/{v['slug']}.json: lançamento '{v['lancamento']}' não existe")
        for v in self.imprensa.get("videos_ao_vivo", []):
            if v not in self.video_por_slug:
                raise ErroDeConteudo(f"imprensa.json: vídeo '{v}' não existe em videos/")


# ---------------------------------------------------------------- cartão og

class Cartoes:
    """Cartão de compartilhamento 1200x630 desenhado no build, um por página."""

    def __init__(self):
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401  (falha cedo se faltar Pillow)
        self.Image, self.ImageDraw, self.ImageFont = Image, ImageDraw, ImageFont
        fontes = ASSETS / "fontes-og"
        self.f_titulo = lambda tam: ImageFont.truetype(str(fontes / "anton.ttf"), tam)
        self.f_tipo = ImageFont.truetype(str(fontes / "special-elite.ttf"), 26)
        self.f_marca = ImageFont.truetype(str(fontes / "anton.ttf"), 34)
        self.feitos: dict[str, str] = {}

    def _quebrar(self, draw, texto, fonte, largura):
        linhas, atual = [], ""
        for palavra in texto.upper().split():
            teste = (atual + " " + palavra).strip()
            if draw.textlength(teste, font=fonte) <= largura or not atual:
                atual = teste
            else:
                linhas.append(atual)
                atual = palavra
        linhas.append(atual)
        return linhas

    def gerar(self, nome: str, titulo: str, rotulo: str, imagem: str | None) -> str:
        if nome in self.feitos:
            return self.feitos[nome]
        W, H = 1200, 630
        img = self.Image.new("RGB", (W, H), (11, 10, 9))
        area_txt = W - 96
        if imagem:
            foto = self.Image.open(MIDIAS / imagem).convert("RGB")
            lado = H
            r = max(lado / foto.width, lado / foto.height)
            foto = foto.resize((math.ceil(foto.width * r), math.ceil(foto.height * r)), self.Image.LANCZOS)
            x0 = (foto.width - lado) // 2
            y0 = (foto.height - lado) // 2
            foto = foto.crop((x0, y0, x0 + lado, y0 + lado))
            img.paste(foto, (W - lado, 0))
            area_txt = W - lado - 96
        d = self.ImageDraw.Draw(img)
        d.rectangle([0, 0, 12, H], fill=(179, 38, 30))
        d.text((56, 56), rotulo.upper(), font=self.f_tipo, fill=(217, 54, 43))
        tam = 96
        while tam > 44:
            fonte = self.f_titulo(tam)
            linhas = self._quebrar(d, titulo, fonte, area_txt)
            if len(linhas) <= 4 and all(d.textlength(l, font=fonte) <= area_txt for l in linhas):
                break
            tam -= 6
        y = 120
        for linha in linhas:
            d.text((56, y), linha, font=fonte, fill=(233, 225, 208))
            y += int(tam * 1.02)
        d.text((56, H - 56 - 34), "DEMOPHOBIA.COM.BR", font=self.f_marca, fill=(167, 158, 140))
        destino = DIST / "og" / f"{nome}.jpg"
        destino.parent.mkdir(parents=True, exist_ok=True)
        img.save(destino, quality=86, optimize=True, progressive=True)
        self.feitos[nome] = f"/og/{nome}.jpg"
        return self.feitos[nome]


# ---------------------------------------------------------------- HTML base

ICONES = """<svg width="0" height="0" style="position:absolute" aria-hidden="true">
<symbol id="i-play" viewBox="0 0 24 24"><path fill="currentColor" d="M6 4l15 8-15 8z"/></symbol>
<symbol id="i-dl" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3v12m-5-5 5 5 5-5M4 20h16"/></symbol>
<symbol id="i-spotify" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm4.6 14.4a.6.6 0 0 1-.9.2c-2.4-1.5-5.4-1.8-8.9-1a.6.6 0 1 1-.3-1.2c3.9-.9 7.2-.5 9.9 1.1.3.2.4.6.2.9zm1.2-2.7a.8.8 0 0 1-1.1.3c-2.7-1.7-6.9-2.2-10.2-1.2a.8.8 0 1 1-.4-1.5c3.7-1.1 8.3-.6 11.5 1.4.3.2.4.7.2 1zm.1-2.8C14.7 9 9.3 8.8 6.2 9.7a.9.9 0 1 1-.5-1.8c3.6-1.1 9.5-.9 13.2 1.3a.9.9 0 0 1-1 1.7z"/></symbol>
<symbol id="i-youtube" viewBox="0 0 24 24"><path fill="currentColor" d="M23 7.2a3 3 0 0 0-2.1-2.1C19 4.6 12 4.6 12 4.6s-7 0-8.9.5A3 3 0 0 0 1 7.2 31 31 0 0 0 .5 12 31 31 0 0 0 1 16.8a3 3 0 0 0 2.1 2.1c1.9.5 8.9.5 8.9.5s7 0 8.9-.5a3 3 0 0 0 2.1-2.1 31 31 0 0 0 .5-4.8 31 31 0 0 0-.5-4.8zM9.7 15V9l5.9 3z"/></symbol>
<symbol id="i-instagram" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M7 2.5h10A4.5 4.5 0 0 1 21.5 7v10a4.5 4.5 0 0 1-4.5 4.5H7A4.5 4.5 0 0 1 2.5 17V7A4.5 4.5 0 0 1 7 2.5z"/><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="17.6" cy="6.4" r="1.2" fill="currentColor"/></symbol>
<symbol id="i-bandcamp" viewBox="0 0 24 24"><path fill="currentColor" d="M1 18.5 7.8 5.5H23l-6.8 13z"/></symbol>
<symbol id="i-link" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1m2 5a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></symbol>
<symbol id="i-cart" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M3 4h2l2.4 11h11L21 7H6.2M9 20a1 1 0 1 0 0-.01M18 20a1 1 0 1 0 0-.01"/></symbol>
</svg>"""


class Site:
    def __init__(self, acervo: Acervo, producao: bool):
        self.a = acervo
        self.producao = producao
        self.m = Midias()
        self.og = Cartoes()
        self.rotas: list[dict] = []   # tudo que o build escreveu; sitemap e checagens saem daqui
        self.faltas: list[str] = []
        self.css = (ASSETS / "estilo.css").read_text(encoding="utf-8")
        self.js = (ASSETS / "site.js").read_text(encoding="utf-8")

    # ---- peças comuns

    def icone(self, nome: str) -> str:
        return f'<svg aria-hidden="true"><use href="#i-{nome}"/></svg>'

    def redes(self) -> str:
        itens = "".join(
            f'<a href="{esc(r["url"])}" target="_blank" rel="noopener">{self.icone(r.get("rede", "link"))}{esc(r["rotulo"])}</a>'
            for r in self.a.banda["redes"])
        return f'<div class="redes">{itens}</div>'

    def pagina(self, rota: str, titulo: str, descricao: str, corpo: str, *, og: str,
               ld: list | None = None, ativo: str | None = None, indexavel: bool = True,
               no_sitemap: bool = False, lastmod: dt.date | None = None):
        titulo_doc = f"{titulo} · Demophobia" if titulo != "Demophobia" else "Demophobia · Metal punk do ABC Paulista"
        robots = "" if (self.producao and indexavel) else '<meta name="robots" content="noindex, nofollow">\n'
        menu = "".join(
            f'<li><a href="{href}"{ATUAL if ativo == href else ""}>{esc(rot)}</a></li>'
            for href, rot in MENU)
        blocos_ld = "".join(json_ld(x) for x in (ld or []))
        doc = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo_doc)}</title>
<meta name="description" content="{esc(descricao)}">
{robots}<link rel="canonical" href="{DOMINIO}{rota}">
<meta property="og:site_name" content="Demophobia">
<meta property="og:locale" content="pt_BR">
<meta property="og:title" content="{esc(titulo)}">
<meta property="og:description" content="{esc(descricao)}">
<meta property="og:url" content="{DOMINIO}{rota}">
<meta property="og:image" content="{DOMINIO}{og}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b0a09">
<link rel="icon" href="/favicon.png">
<link rel="preload" href="/fontes/anton.woff2" as="font" type="font/woff2" crossorigin>
<link rel="alternate" type="application/json" href="/index.json">
<style>{self.css}</style>
{blocos_ld}
</head>
<body>
<a class="pular" href="#conteudo">Pular para o conteúdo</a>
{ICONES}
<nav class="nav" aria-label="Principal">
  <div class="wrap">
    <a class="marca" href="/">Demo<span>phobia</span></a>
    <button class="menu-btn" aria-expanded="false" aria-controls="menu">Menu</button>
    <ul id="menu">{menu}</ul>
  </div>
</nav>
<main id="conteudo">
{corpo}
</main>
<footer class="rodape">
  <div class="wrap">
    <div>
      <a class="marca" href="/">Demo<span>phobia</span></a>
      <p>Metal punk do ABC Paulista · desde {esc(self.a.banda.get("fundacao", ""))}<br>
      Shows e contato: <a href="mailto:{esc(self.a.banda["contato"]["email"])}">{esc(self.a.banda["contato"]["email"])}</a></p>
    </div>
    {self.redes()}
  </div>
</footer>
<div class="lightbox" role="dialog" aria-modal="true" aria-label="Imagem ampliada">
  <button class="lb-fechar" aria-label="Fechar">✕</button>
  <button class="lb-ant" aria-label="Anterior">‹</button>
  <img alt="">
  <button class="lb-prox" aria-label="Próxima">›</button>
</div>
<script>{self.js}</script>
</body>
</html>
"""
        self.escrever(rota, doc)
        self.rotas.append({"rota": rota, "sitemap": indexavel and not no_sitemap,
                           "lastmod": lastmod, "indexavel": indexavel})

    def escrever(self, rota: str, conteudo: str):
        if rota.endswith("/"):
            destino = DIST / rota.lstrip("/") / "index.html"
        else:
            destino = DIST / rota.lstrip("/")
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo, encoding="utf-8")

    def cabeca(self, kicker: str, titulo: str, lead: str | None = None) -> str:
        lead_html = f'<p class="lead">{esc(lead)}</p>' if lead else ""
        return f'<header class="cabeca"><div class="wrap"><p class="kicker">{esc(kicker)}</p><h1 class="pagina">{esc(titulo)}</h1>{lead_html}</div></header>'

    # ---- componentes

    def capa_de(self, l: dict) -> dict | None:
        """A capa declarada, ou a que site/baixar_midias.py trouxe do streaming (capa_fonte)."""
        if l.get("capa"):
            return l["capa"]
        baixada = f"capas/{l['slug']}.jpg"
        if l.get("capa_fonte") and self.m.existe(baixada):
            return {"arquivo": baixada, "descricao": f"Capa de {l['titulo']}, da Demophobia"}
        return None

    def capa_ou_nome(self, l: dict, origem: str) -> str:
        capa = self.capa_de(l)
        if capa:
            return f'<img src="{self.m.url(capa["arquivo"], origem)}" alt="{esc(capa["descricao"])}" loading="lazy" width="1000" height="1000">'
        return f'<div class="sem-capa" role="img" aria-label="{esc(l["titulo"])}">{esc(l["titulo"])}<small>{esc(TIPOS_LANCAMENTO[l["tipo"]])} · {l["_data"].year}</small></div>'

    def faixa(self, f: dict) -> str:
        nota = f'<span class="nota">{esc(f["nota"])}</span>' if f.get("nota") else ""
        return f'<li><span class="nome">{esc(f["titulo"])}{nota}</span></li>'

    def cartao_lancamento(self, l: dict) -> str:
        origem = f"lancamentos/{l['slug']}.json"
        return f"""<a class="cartao" href="/discografia/{esc(l["slug"])}/">
  <div class="img">{self.capa_ou_nome(l, origem)}</div>
  <div class="meta"><h3>{esc(l["titulo"])}</h3><span class="etiqueta">{esc(TIPOS_LANCAMENTO[l["tipo"]])} · {l["_data"].year}</span></div>
</a>"""

    def miniatura_video(self, v: dict) -> str:
        """Miniatura local se existir (baixada por site/baixar_midias.py); senão a capa do lançamento; senão a foto da banda."""
        propria = f"videos/{v['youtube']}.jpg"
        if self.m.existe(propria):
            return self.m.url(propria, f"videos/{v['slug']}.json")
        l = self.a.lanc_por_slug.get(v.get("lancamento") or "")
        if l and l.get("capa"):
            return self.m.url(l["capa"]["arquivo"], f"videos/{v['slug']}.json")
        return self.m.url(self.a.banda["foto"]["arquivo"], "banda.json")

    def cartao_video(self, v: dict) -> str:
        nota = f'<p>{esc(v["nota"])}</p>' if v.get("nota") else ""
        tipo = f' · {esc(v["tipo"])}' if v.get("tipo") else ""
        return f"""<div class="video">
  <div class="tela"><button data-youtube="{esc(v["youtube"])}" aria-label="Assistir {esc(v["titulo"])}"><img src="{self.miniatura_video(v)}" alt="" loading="lazy"><span class="play">{self.icone("play")}</span></button></div>
  <span class="ano">{esc(v["ano"])}{tipo}</span><h3>{esc(v["titulo"])}</h3>{nota}
</div>"""

    def grade_videos(self, videos: list[dict]) -> str:
        return ('<div class="videos">' + "".join(self.cartao_video(v) for v in videos) + '</div>'
                '<p class="aviso-yt">O player do YouTube só carrega quando você clica.</p>')

    def item_show(self, s: dict) -> str:
        d = s["_data"]
        titulo = s.get("evento") or ("Com " + ", ".join(s["com"]) if s.get("com") else "Show")
        onde = ", ".join(x for x in [s.get("local"), s.get("cidade"), s.get("uf")] if x)
        com = ""
        if s.get("com") and s.get("evento"):
            com = "Com " + ", ".join(s["com"])
        linhas = " · ".join(esc(x) for x in [onde, com] if x)
        cartaz = ""
        if s.get("cartaz"):
            cartaz = f'<img class="cartaz-mini" src="{self.m.url(s["cartaz"], "shows/" + s["_slug"] + ".json")}" alt="" loading="lazy">'
        ingresso = ""
        if s.get("ingressos") and d >= self.a.hoje:
            ingresso = f'<a class="btn primario" href="{esc(s["ingressos"])}" target="_blank" rel="noopener">Ingressos</a>'
        return f"""<li>
  <div class="data">{d.day:02d}.{d.month:02d}<small>{d.year}</small></div>
  <div><h3>{esc(titulo)}</h3>{f"<p>{linhas}</p>" if linhas else ""}</div>
  {ingresso or cartaz}
</li>"""

    def ld_show(self, s: dict) -> dict:
        nome = s.get("evento") or "Demophobia ao vivo"
        ev = {"@context": "https://schema.org", "@type": "MusicEvent", "name": nome,
              "startDate": s["_data"].isoformat(),
              "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
              "performer": [{"@type": "MusicGroup", "name": "Demophobia"}] + [{"@type": "MusicGroup", "name": n} for n in s.get("com", [])]}
        if s.get("local") or s.get("cidade"):
            ev["location"] = {"@type": "Place", "name": s.get("local") or s.get("cidade"),
                              "address": {"@type": "PostalAddress", "addressLocality": s.get("cidade"), "addressRegion": s.get("uf"), "addressCountry": "BR"}}
        if s.get("ingressos"):
            ev["offers"] = {"@type": "Offer", "url": s["ingressos"]}
        return ev

    def galeria(self, itens: list[dict], classe: str, origem: str) -> str:
        botoes = "".join(
            f'<button aria-label="Ampliar: {esc(i["descricao"])}"><img src="{self.m.url(i["arquivo"], origem)}" data-grande="/midias/{esc(i["arquivo"])}" alt="{esc(i["descricao"])}" loading="lazy"></button>'
            for i in itens)
        return f'<div class="galeria {classe}" data-galeria>{botoes}</div>'

    def cartazes(self, itens: list[dict]) -> str:
        """Largos numa grade de 2 colunas, verticais numa de 7: cada formato sem corte."""
        largos = [i for i in itens if i.get("formato") == "largo"]
        verticais = [i for i in itens if i.get("formato") != "largo"]
        html_ = ""
        if largos:
            html_ += self.galeria(largos, "cartazes-largos", "galeria.json").replace(' data-galeria>', '>')
        if verticais:
            html_ += self.galeria(verticais, "cartazes-verticais", "galeria.json").replace(' data-galeria>', '>')
        return f'<div class="cartazes" data-galeria>{html_}</div>'

    def data_midia(self, m: dict) -> str:
        txt = data_extenso(m["_data"], m["_prec"])
        if not m.get("data_confirmada"):
            txt = "c. " + txt
        return txt

    def item_midia(self, m: dict, com_resumo: bool = True) -> str:
        idioma = {"en": "em inglês", "es": "em espanhol"}.get(m.get("idioma", "pt"), "")
        meta = " · ".join(x for x in [esc(m["tipo"]), esc(idioma)] if x)
        resumo = f'<p>{esc(m["resumo"])}</p>' if com_resumo and m.get("resumo") else ""
        return f"""<li>
  <span class="quando">{esc(self.data_midia(m))}</span>
  <div><span class="veiculo">{esc(m["veiculo"])}</span><span class="tipo">{meta}</span>
  <a href="{esc(m["url"])}" target="_blank" rel="noopener">{esc(m["titulo"])}</a>{resumo}</div>
</li>"""

    def lista_midia(self, itens: list[dict], com_resumo: bool = True) -> str:
        return '<ul class="clipping">' + "".join(self.item_midia(m, com_resumo) for m in itens) + "</ul>"

    def cartao_noticia(self, n: dict) -> str:
        img = ""
        if n.get("capa"):
            img = f'<div class="img larga"><img src="{self.m.url(n["capa"]["arquivo"], "noticias/" + n["slug"] + ".json")}" alt="" loading="lazy"></div>'
        return f"""<a class="cartao" href="/noticias/{esc(n["slug"])}/">
  {img}
  <div class="meta"><span class="etiqueta">{esc(data_extenso(n["_data"], "dia"))}</span></div>
  <h3>{esc(n["titulo"])}</h3>
  <p>{esc(n["linha_fina"])}</p>
</a>"""

    def cartao_produto(self, p: dict) -> str:
        foto = p["fotos"][0]
        preco = preco_br(p["preco"]) if p.get("preco") is not None else "Consulte"
        status = "" if p.get("disponivel", True) else " · esgotado"
        return f"""<a class="cartao" href="/loja/{esc(p["slug"])}/">
  <div class="img"><img src="{self.m.url(foto["arquivo"], "loja/" + p["slug"] + ".json")}" alt="{esc(foto["descricao"])}" loading="lazy"></div>
  <div class="meta"><h3>{esc(p["titulo"])}</h3><span class="etiqueta">{esc(preco)}{status}</span></div>
</a>"""

    def ld_banda(self) -> dict:
        b = self.a.banda
        return {"@context": "https://schema.org", "@type": "MusicGroup", "name": b["nome"],
                "url": DOMINIO + "/", "description": b["linha_fina"], "foundingDate": b.get("fundacao"),
                "genre": b.get("generos", []), "image": DOMINIO + "/midias/" + b["foto"]["arquivo"],
                "foundingLocation": {"@type": "Place", "name": f'{b["origem"]["regiao"]}, {b["origem"]["uf"]}'},
                "member": [{"@type": "OrganizationRole", "roleName": p["funcao"],
                            "member": {"@type": "Person", "name": p["nome"]}} for p in b["formacao"]],
                "sameAs": [r["url"] for r in b["redes"]],
                "email": b["contato"]["email"]}

    # ---- páginas

    def home(self):
        a, b = self.a, self.a.banda
        l = a.lanc_por_slug[b["destaque"]]
        origem = f"lancamentos/{l['slug']}.json"
        capa = self.m.url(l["capa"]["arquivo"], origem) if l.get("capa") else self.m.url(b["foto"]["arquivo"], "banda.json")
        carimbo = {"album": "Álbum", "ep": "EP", "single": "Single"}[l["tipo"]]
        ouvir = next((x for x in l.get("links", [])), None)
        btn_ouvir = f'<a class="btn primario" href="{esc(ouvir["url"])}" target="_blank" rel="noopener">{self.icone("play")}{esc(ouvir["rotulo"])}</a>' if ouvir else ""

        if a.proximos:
            shows = '<ul class="shows">' + "".join(self.item_show(s) for s in a.proximos[:4]) + "</ul>"
        else:
            shows = f'<p class="vazio">Nenhuma data anunciada agora. Para levar a Demophobia para o seu evento, escreva para <a href="mailto:{esc(b["contato"]["email"])}">{esc(b["contato"]["email"])}</a>.</p>'
        noticias = ""
        if a.midia or a.noticias:
            notas = f'<div class="grade" style="margin-top:40px">{"".join(self.cartao_noticia(n) for n in a.noticias[:3])}</div>' if a.noticias else ""
            noticias = f"""<section class="bloco alt"><div class="wrap">
  <div class="topo-bloco"><h2>Na mídia</h2><a class="ver-tudo" href="/noticias/">Todas as matérias</a></div>
  {self.lista_midia(a.midia[:4], com_resumo=False)}
  {notas}
</div></section>"""
        loja = ""
        if a.produtos:
            loja = f"""<section class="bloco"><div class="wrap">
  <div class="topo-bloco"><h2>Loja</h2><a class="ver-tudo" href="/loja/">Ver a loja</a></div>
  <div class="grade">{"".join(self.cartao_produto(p) for p in a.produtos[:3])}</div>
</div></section>"""

        corpo = f"""<header class="hero">
  <div class="fundo" style="background-image:url('{capa}')"></div>
  <div class="wrap">
    <div class="hero-capa"><img src="{capa}" alt="{esc(l["capa"]["descricao"]) if l.get("capa") else ""}" width="1000" height="1000"><span class="carimbo">{esc(carimbo)} · {l["_data"].year}</span></div>
    <div>
      <p class="banda">Demophobia</p>
      <h1>{esc(l["titulo"])}</h1>
      <p class="linha">{esc(l["linha_fina"])}</p>
      <div class="btns">{btn_ouvir}<a class="btn fantasma" href="/discografia/{esc(l["slug"])}/">Sobre o disco</a></div>
    </div>
  </div>
</header>
<div class="dados"><div class="wrap"><dl>
  <div><dt>Origem</dt><dd>{esc(b["origem"]["regiao"])} · {esc(b["origem"]["uf"])}</dd></div>
  <div><dt>Desde</dt><dd>{esc(b.get("fundacao", ""))}</dd></div>
  <div><dt>Som</dt><dd>Thrash · Death · Punk</dd></div>
  <div><dt>Discografia</dt><dd>{len(a.lancamentos)} lançamentos</dd></div>
</dl></div></div>
{self.vitrine()}
<section class="bloco"><div class="wrap">
  <div class="topo-bloco"><h2>Próximos shows</h2><a class="ver-tudo" href="/shows/">Agenda e histórico</a></div>
  {shows}
</div></section>
<section class="bloco alt"><div class="wrap">
  <div class="topo-bloco"><h2>Assista</h2><a class="ver-tudo" href="/videos/">Todos os vídeos</a></div>
  {self.grade_videos(a.videos[:3])}
</div></section>
{noticias}
<section class="bloco"><div class="wrap cols">
  <div><p class="kicker">A banda</p><h2>Demophobia?</h2></div>
  <div class="prose"><p class="lead">{esc(b["linha_fina"])}</p><p><a class="ver-tudo" href="/banda/">Conheça a banda</a></p></div>
</div></section>
{loja}"""
        og = self.og.gerar("home", l["titulo"], "Demophobia · " + carimbo, l["capa"]["arquivo"] if l.get("capa") else b["foto"]["arquivo"])
        self.pagina("/", "Demophobia", b["linha_fina"], corpo, og=og, ld=[self.ld_banda()])

    def vitrine(self) -> str:
        v = self.a.banda.get("vitrine")
        if not v or not v.get("itens"):
            return ""
        cards = "".join(f"""<a class="cartao" href="{esc(i["link"])}">
  <div class="img"><img src="{self.m.url(i["capa"]["arquivo"], "banda.json")}" alt="{esc(i["capa"]["descricao"])}" loading="lazy"></div>
  <div class="meta"><h3>{esc(i["titulo"])}</h3><span class="etiqueta">{esc(i["etiqueta"])}</span></div>
</a>""" for i in v["itens"])
        return f'<section class="bloco"><div class="wrap"><h2>{esc(v.get("titulo", ""))}</h2><div class="grade">{cards}</div></div></section>'

    def item_trajetoria(self, t: dict) -> str:
        futuro = t.get("futuro")
        classe = ' class="futuro"' if futuro else ""
        selo = '<span class="em-breve">Em breve</span>' if futuro else ""
        return f'<li{classe}><span class="ano">{esc(t["ano"])}</span>{selo}<h3>{esc(t["titulo"])}</h3><p>{esc(t["texto"])}</p></li>'

    def banda(self):
        b = self.a.banda
        foto = b["foto"]
        etimo = b.get("nome_origem")
        eq = ""
        if etimo:
            partes = '<span class="op" aria-hidden="true">+</span>'.join(
                f'<span class="termo"><span>{esc(p["termo"])}</span><small>{esc(p["sentido"])}</small></span>' for p in etimo["partes"])
            eq = f'<div class="etimo"><div class="eq">{partes}</div><p>{esc(etimo["texto"])}</p></div>'
        formacao = "".join(f'<li><span class="n">{esc(p["nome"])}</span><span class="f">{esc(p["funcao"])}</span></li>' for p in b["formacao"])
        tempo = "".join(self.item_trajetoria(t) for t in b.get("trajetoria", []))
        corpo = f"""{self.cabeca("A banda", "Demophobia", b["linha_fina"])}
<section class="bloco"><div class="wrap">
  <figure class="foto-banda"><img src="{self.m.url(foto["arquivo"], "banda.json")}" alt="{esc(foto["descricao"])}" width="2000" height="1125"></figure>
  <div class="cols">
    <div>{eq}<ul class="formacao">{formacao}</ul></div>
    <div class="prose">{b["bio_html"]}</div>
  </div>
</div></section>
<section class="bloco alt"><div class="wrap">
  <p class="kicker">Trajetória</p><h2>Desde {esc(b.get("fundacao", ""))}</h2>
  <ol class="linha-tempo">{tempo}</ol>
</div></section>
<section class="bloco"><div class="wrap">
  <h2>Contato</h2>
  <p class="lead">Shows, entrevistas e parcerias: <a class="email" href="mailto:{esc(b["contato"]["email"])}">{esc(b["contato"]["email"])}</a></p>
  {self.redes()}
</div></section>"""
        og = self.og.gerar("banda", "A banda", "Demophobia", foto["arquivo"])
        self.pagina("/banda/", "A banda", b["linha_fina"], corpo, og=og, ld=[self.ld_banda()], ativo="/banda/")

    def discografia(self):
        a = self.a
        corpo = f"""{self.cabeca("Discografia", "Discografia", "Álbum, EP e singles da Demophobia, do mais recente ao primeiro.")}
<section class="bloco"><div class="wrap"><div class="grade">{"".join(self.cartao_lancamento(l) for l in a.lancamentos)}</div></div></section>"""
        destaque = a.lanc_por_slug[a.banda["destaque"]]
        og = self.og.gerar("discografia", "Discografia", "Demophobia", destaque["capa"]["arquivo"] if destaque.get("capa") else None)
        ultima = max(l["_data"] for l in a.lancamentos)
        self.pagina("/discografia/", "Discografia", "Álbum, EP e singles da Demophobia, do mais recente ao primeiro.",
                    corpo, og=og, ativo="/discografia/", lastmod=ultima)
        for l in a.lancamentos:
            self.lancamento(l)

    def lancamento(self, l: dict):
        a = self.a
        origem = f"lancamentos/{l['slug']}.json"
        tipo = TIPOS_LANCAMENTO[l["tipo"]]
        links = "".join(f'<a class="btn primario" href="{esc(x["url"])}" target="_blank" rel="noopener">{self.icone("play")}{esc(x["rotulo"])}</a>' for x in l.get("links", []))
        album = ""
        if l.get("do_album"):
            pai = a.lanc_por_slug[l["do_album"]]
            album = f'<a class="btn fantasma" href="/discografia/{esc(pai["slug"])}/">Do álbum {esc(pai["titulo"])}</a>'
        faixas = ""
        if l.get("faixas"):
            itens = "".join(
                self.faixa(f)
                for f in l["faixas"])
            faixas = f'<h2>Faixas</h2><ol class="faixas">{itens}</ol>'
        creditos = ""
        if l.get("creditos"):
            creditos = '<h2 style="margin-top:56px">Ficha técnica</h2><dl class="creditos">' + "".join(
                f'<div><dt>{esc(c["funcao"])}</dt><dd>{esc(c["quem"])}</dd></div>' for c in l["creditos"]) + "</dl>"
        texto = f'<div class="prose" style="margin-top:40px">{l["texto_html"]}</div>' if l.get("texto_html") else ""
        videos = ""
        if l.get("videos"):
            videos = f'<section class="bloco alt"><div class="wrap"><h2>Vídeos</h2>{self.grade_videos([a.video_por_slug[v] for v in l["videos"]])}</div></section>'
        outros = [x for x in a.lancamentos if x["slug"] != l["slug"]][:3]
        corpo = f"""<section class="bloco"><div class="wrap lanc">
  <div class="capa">{self.capa_ou_nome(l, origem)}</div>
  <div>
    <p class="kicker">{esc(tipo)} · {esc(data_extenso(l["_data"], l["_prec"]))}</p>
    <h1 class="pagina">{esc(l["titulo"])}</h1>
    <p class="lead dim">{esc(l["linha_fina"])}</p>
    <div class="btns">{links}{album}</div>
    {f'<blockquote class="citacao">{esc(l["citacao"])}</blockquote>' if l.get("citacao") else ""}
    {texto}
  </div>
</div></section>
{f'<section class="bloco alt"><div class="wrap cols"><div>{faixas}</div><div>{creditos}</div></div></section>' if (faixas or creditos) else ""}
{videos}
<section class="bloco"><div class="wrap">
  <div class="topo-bloco"><h2>Discografia</h2><a class="ver-tudo" href="/discografia/">Todos os lançamentos</a></div>
  <div class="grade">{"".join(self.cartao_lancamento(x) for x in outros)}</div>
</div></section>"""
        ld = {"@context": "https://schema.org",
              "@type": "MusicAlbum" if l["tipo"] in ("album", "ep") else "MusicRecording",
              "name": l["titulo"], "byArtist": {"@type": "MusicGroup", "name": "Demophobia", "url": DOMINIO + "/"},
              "datePublished": l["data"], "description": l["linha_fina"], "url": f"{DOMINIO}/discografia/{l['slug']}/"}
        if self.capa_de(l):
            ld["image"] = DOMINIO + "/midias/" + self.capa_de(l)["arquivo"]
        if l["tipo"] in ("album", "ep"):
            ld["albumProductionType"] = "https://schema.org/StudioAlbum"
            ld["albumReleaseType"] = "https://schema.org/AlbumRelease" if l["tipo"] == "album" else "https://schema.org/EPRelease"
            if l.get("faixas"):
                ld["numTracks"] = len(l["faixas"])
                ld["track"] = [{"@type": "MusicRecording", "position": i + 1, "name": f["titulo"]} for i, f in enumerate(l["faixas"])]
        capa = self.capa_de(l)
        og = self.og.gerar(f"discografia-{l['slug']}", l["titulo"], f"{tipo} · {l['_data'].year}", capa["arquivo"] if capa else None)
        self.pagina(f"/discografia/{l['slug']}/", l["titulo"], l["linha_fina"], corpo, og=og, ld=[ld],
                    ativo="/discografia/", lastmod=l["_data"])

    def shows(self):
        a, g = self.a, self.a.galeria
        email = a.banda["contato"]["email"]
        if a.proximos:
            prox = '<ul class="shows">' + "".join(self.item_show(s) for s in a.proximos) + "</ul>"
        else:
            prox = f'<p class="vazio">Nenhuma data anunciada agora. Contratação: <a href="mailto:{esc(email)}">{esc(email)}</a>.</p>'
        hist = ""
        if a.passados:
            hist = '<h2 style="margin-top:72px">Já passou</h2><ul class="shows">' + "".join(self.item_show(s) for s in a.passados) + "</ul>"
        corpo = f"""{self.cabeca("Ao vivo", "Shows", "Agenda, histórico e cartazes dos shows da Demophobia.")}
<section class="bloco"><div class="wrap">
  <h2>Agenda</h2>{prox}{hist}
</div></section>
<section class="bloco alt"><div class="wrap">
  <h2>No palco</h2>{self.galeria(g.get("ao_vivo", []), "", "galeria.json")}
</div></section>
<section class="bloco"><div class="wrap">
  <h2>Cartazes</h2>{self.cartazes(g.get("cartazes", []))}
</div></section>"""
        foto = g["ao_vivo"][0]["arquivo"] if g.get("ao_vivo") else None
        og = self.og.gerar("shows", "Shows", "Demophobia ao vivo", foto)
        self.pagina("/shows/", "Shows", "Agenda, histórico e cartazes dos shows da Demophobia.", corpo, og=og,
                    ld=[self.ld_show(s) for s in a.proximos], ativo="/shows/")

    def videos(self):
        corpo = f"""{self.cabeca("Vídeos", "Vídeos", "Clipes, lyric videos e apresentações ao vivo.")}
<section class="bloco"><div class="wrap">{self.grade_videos(self.a.videos)}</div></section>"""
        ld = [{"@context": "https://schema.org", "@type": "VideoObject", "name": f"Demophobia · {v['titulo']}",
               "description": v.get("nota") or f"{v.get('tipo', 'vídeo')} de {v['titulo']}, da Demophobia",
               "thumbnailUrl": f"https://i.ytimg.com/vi/{v['youtube']}/hqdefault.jpg",
               "embedUrl": f"https://www.youtube-nocookie.com/embed/{v['youtube']}",
               "contentUrl": f"https://www.youtube.com/watch?v={v['youtube']}",
               "uploadDate": str(v["ano"])} for v in self.a.videos]
        og = self.og.gerar("videos", "Vídeos", "Demophobia", self.a.banda["foto"]["arquivo"])
        self.pagina("/videos/", "Vídeos", "Clipes, lyric videos e apresentações ao vivo da Demophobia.", corpo,
                    og=og, ld=ld, ativo="/videos/")

    def noticias(self):
        """Na mídia (o mapeamento do que saiu sobre a banda) + Da banda (notas próprias, paginadas)."""
        a = self.a
        total = len(a.noticias)
        paginas = max(1, math.ceil(total / POR_PAGINA))   # nunca zero, nem quando o total for múltiplo exato
        og = self.og.gerar("noticias", "Na mídia", "Demophobia · Notícias", self.a.banda["foto"]["arquivo"])
        veiculos = sorted({m["veiculo"] for m in a.midia})
        midia = ""
        if a.midia:
            por_ano: dict[int, list] = {}
            for m in a.midia:
                por_ano.setdefault(m["_data"].year, []).append(m)
            anos = "".join(f'<h3 class="ano-midia">{ano}</h3>{self.lista_midia(itens)}' for ano, itens in por_ano.items())
            midia = f"""<section class="bloco"><div class="wrap">
  <div class="topo-bloco"><h2>Na mídia</h2><p class="dim" style="margin:0">{len(a.midia)} matérias em {len(veiculos)} veículos</p></div>
  {anos}
</div></section>"""
        for n_pag in range(1, paginas + 1):
            fatia = a.noticias[(n_pag - 1) * POR_PAGINA: n_pag * POR_PAGINA]
            rota = "/noticias/" if n_pag == 1 else f"/noticias/pagina/{n_pag}/"
            lista = f'<div class="grade">{"".join(self.cartao_noticia(n) for n in fatia)}</div>' if fatia else '<p class="vazio">Nenhuma nota publicada ainda.</p>'
            pag = ""
            if paginas > 1:
                itens = []
                for i in range(1, paginas + 1):
                    href = "/noticias/" if i == 1 else f"/noticias/pagina/{i}/"
                    itens.append(f'<span aria-current="page">{i}</span>' if i == n_pag else f'<a href="{href}">{i}</a>')
                pag = f'<nav class="paginacao" aria-label="Páginas">{"".join(itens)}</nav>'
            titulo = "Notícias" if n_pag == 1 else f"Notícias · página {n_pag}"
            da_banda = f'<section class="bloco alt"><div class="wrap"><h2>Da banda</h2>{lista}{pag}</div></section>'
            corpo = self.cabeca("Notícias", "Notícias", "O que a imprensa especializada publicou sobre a Demophobia, e as notas da própria banda.")
            corpo += (midia if n_pag == 1 else "") + da_banda
            ultima = max([x["_data"] for x in fatia] + ([a.midia[0]["_data"]] if a.midia and n_pag == 1 else []), default=None)
            self.pagina(rota, titulo, "O que a imprensa especializada publicou sobre a Demophobia, e as notas da própria banda.",
                        corpo, og=og, ativo="/noticias/", lastmod=ultima)
        for n in a.noticias:
            self.noticia(n)

    def noticia(self, n: dict):
        origem = f"noticias/{n['slug']}.json"
        capa = ""
        if n.get("capa"):
            capa = f'<figure class="capa-noticia"><img src="{self.m.url(n["capa"]["arquivo"], origem)}" alt="{esc(n["capa"]["descricao"])}"></figure>'
        leituras = ""
        if n.get("leituras"):
            leituras = '<ul class="leituras">' + "".join(f"<li>{esc(x)}</li>" for x in n["leituras"]) + "</ul>"
        rel = ""
        rel_itens = [self.a.lanc_por_slug[s] for s in n.get("relacionados", []) if s in self.a.lanc_por_slug]
        if rel_itens:
            rel = f'<section class="bloco alt"><div class="wrap"><h2>Relacionados</h2><div class="grade">{"".join(self.cartao_lancamento(l) for l in rel_itens)}</div></div></section>'
        corpo = f"""<section class="bloco"><div class="wrap"><article class="artigo">
  <p class="data-pub"><time datetime="{n["_data"].isoformat()}">{esc(data_extenso(n["_data"], "dia"))}</time></p>
  <h1 class="pagina">{esc(n["titulo"])}</h1>
  <p class="lead dim">{esc(n["linha_fina"])}</p>
  {capa}{leituras}
  <div class="prose">{n["conteudo_html"]}</div>
  <p style="margin-top:40px"><a class="ver-tudo" href="/noticias/">Todas as notícias</a></p>
</article></div></section>
{rel}"""
        ld = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": n["titulo"],
              "description": n["linha_fina"], "datePublished": n["_data"].isoformat(),
              "author": {"@type": "MusicGroup", "name": "Demophobia", "url": DOMINIO + "/"},
              "publisher": {"@type": "Organization", "name": "Demophobia", "url": DOMINIO + "/"},
              "mainEntityOfPage": f"{DOMINIO}/noticias/{n['slug']}/", "inLanguage": "pt-BR"}
        if n.get("capa"):
            ld["image"] = DOMINIO + "/midias/" + n["capa"]["arquivo"]
        if n.get("leituras"):
            ld["speakable"] = {"@type": "SpeakableSpecification", "cssSelector": [".leituras"]}
        og = self.og.gerar(f"noticia-{n['slug']}", n["titulo"], "Notícia · " + data_extenso(n["_data"], "dia"),
                           n["capa"]["arquivo"] if n.get("capa") else None)
        self.pagina(f"/noticias/{n['slug']}/", n["titulo"], n["linha_fina"], corpo, og=og, ld=[ld],
                    ativo="/noticias/", lastmod=n["_data"])

    def loja(self):
        a = self.a
        email = a.banda["contato"]["email"]
        if a.produtos:
            lista = f'<div class="grade">{"".join(self.cartao_produto(p) for p in a.produtos)}</div>'
        else:
            lista = f'<p class="vazio">A loja abre em breve. Enquanto isso, pedidos de CD e camiseta: <a href="mailto:{esc(email)}">{esc(email)}</a>.</p>'
        corpo = f"""{self.cabeca("Merch", "Loja", "Camisetas, CDs e material oficial da Demophobia.")}
<section class="bloco"><div class="wrap">{lista}</div></section>"""
        og = self.og.gerar("loja", "Loja", "Merch oficial", a.produtos[0]["fotos"][0]["arquivo"] if a.produtos else None)
        self.pagina("/loja/", "Loja", "Camisetas, CDs e material oficial da Demophobia.", corpo, og=og, ativo="/loja/")
        for p in a.produtos:
            self.produto(p)

    def produto(self, p: dict):
        origem = f"loja/{p['slug']}.json"
        email = self.a.banda["contato"]["email"]
        fotos = "".join(f'<img src="{self.m.url(f["arquivo"], origem)}" alt="{esc(f["descricao"])}">' for f in p["fotos"])
        tem_preco = p.get("preco") is not None
        preco = f'<p class="preco">{esc(preco_br(p["preco"]))}<small>{esc(p.get("nota_preco", "frete calculado no pagamento"))}</small></p>' if tem_preco else '<p class="preco">Consulte</p>'
        assunto = f"Pedido: {p['titulo']}"
        mailto = f"mailto:{email}?subject={html.escape(assunto.replace(' ', '%20'), quote=True)}"
        compra = p.get("compra") or {}
        disponivel = p.get("disponivel", True)
        variacoes = ""
        if p.get("variacoes"):
            itens = []
            for v in p["variacoes"]:
                if not v.get("disponivel", True):
                    itens.append(f'<li><span class="esgotado" title="Esgotado">{esc(v["rotulo"])}</span></li>')
                elif v.get("url"):
                    itens.append(f'<li><a href="{esc(v["url"])}" target="_blank" rel="noopener">{esc(v["rotulo"])}</a></li>')
                else:
                    itens.append(f'<li><span>{esc(v["rotulo"])}</span></li>')
            variacoes = f'<p class="kicker">{esc(p.get("rotulo_variacao", "Tamanho"))}</p><ul class="variacoes">{"".join(itens)}</ul>'
        if not disponivel:
            botao = '<span class="btn primario" aria-disabled="true">Esgotado</span>'
        elif compra.get("url"):
            botao = f'<a class="btn primario" href="{esc(compra["url"])}" target="_blank" rel="noopener">{self.icone("cart")}Comprar</a>'
        elif p.get("variacoes") and any(v.get("url") for v in p["variacoes"]):
            botao = ""   # cada tamanho já leva ao pagamento
        else:
            botao = f'<a class="btn primario" href="{mailto}">{self.icone("cart")}Encomendar por e-mail</a>'
        corpo = f"""<section class="bloco"><div class="wrap produto">
  <div class="fotos">{fotos}</div>
  <div>
    <p class="kicker">{esc(p.get("categoria", "Merch"))}</p>
    <h1 class="pagina">{esc(p["titulo"])}</h1>
    <p class="lead dim">{esc(p["linha_fina"])}</p>
    {preco}{variacoes}
    <div class="btns">{botao}<a class="btn fantasma" href="/loja/">Voltar à loja</a></div>
    {f'<div class="prose" style="margin-top:36px">{p["descricao_html"]}</div>' if p.get("descricao_html") else ""}
  </div>
</div></section>"""
        ld = {"@context": "https://schema.org", "@type": "Product", "name": p["titulo"], "description": p["linha_fina"],
              "image": [DOMINIO + "/midias/" + f["arquivo"] for f in p["fotos"]], "brand": {"@type": "Brand", "name": "Demophobia"},
              "url": f"{DOMINIO}/loja/{p['slug']}/"}
        if tem_preco:
            ld["offers"] = {"@type": "Offer", "price": f'{p["preco"]:.2f}', "priceCurrency": "BRL",
                            "availability": "https://schema.org/InStock" if disponivel else "https://schema.org/OutOfStock"}
        og = self.og.gerar(f"loja-{p['slug']}", p["titulo"], "Loja Demophobia", p["fotos"][0]["arquivo"])
        self.pagina(f"/loja/{p['slug']}/", p["titulo"], p["linha_fina"], corpo, og=og, ld=[ld], ativo="/loja/")

    def falta(self, texto: str, inline: bool = False) -> str:
        """Marca o que falta. Na prévia aparece na página; em produção some, e o build lista no fim."""
        self.faltas.append(texto)
        if self.producao:
            return ""
        tag = "span" if inline else "div"
        return f'<{tag} class="falta">{esc(texto)}</{tag}>'

    def na_imprensa(self) -> str:
        """Bloco da página de contratação: veículos e matérias em destaque."""
        a = self.a
        if not a.midia:
            return ""
        destaques = [m for m in a.midia if m.get("destaque")] or a.midia[:5]
        veiculos = sorted({m["veiculo"] for m in a.midia})
        nomes = "".join(f"<span>{esc(v)}</span>" for v in veiculos)
        return f"""<div style="margin-top:48px">
  <h3 style="font-size:28px;margin-bottom:16px">Na imprensa</h3>
  <div class="nomes" style="margin-bottom:24px">{nomes}</div>
  {self.lista_midia(destaques)}
  <p style="margin-top:16px"><a class="ver-tudo" href="/noticias/">Todas as {len(a.midia)} matérias</a></p>
</div>"""

    def imprensa(self):
        """Imprensa e contratação. Página de bastidor: fora do menu, do sitemap, do llms.txt e do
        index.json, e sempre com noindex. Segue a ordem em que o produtor decide."""
        a, im, b = self.a, self.a.imprensa, self.a.banda
        o = "imprensa.json"
        ct, tec, mat = im.get("contato", {}), im.get("tecnico", {}), im.get("material", {})
        email = ct.get("email") or b["contato"]["email"]

        # 1. topo: foto ao vivo, frase e três botões
        wa = ct.get("whatsapp")
        botao_contato = (f'<a class="btn primario" href="https://wa.me/{esc(re.sub(r"[^0-9]", "", wa))}" target="_blank" rel="noopener">Contratar pelo WhatsApp</a>'
                         if wa else f'<a class="btn primario" href="mailto:{esc(email)}?subject=Contrata%C3%A7%C3%A3o%20Demophobia">Contratar</a>')
        botao_rider = (f'<a class="btn fantasma" href="{self.m.url(tec["rider_pdf"], o)}" download>{self.icone("dl")}Rider técnico</a>'
                       if tec.get("rider_pdf") else "")
        falta_topo = ""  # WhatsApp e rider são marcados nos blocos de contato e técnico
        foto = im["foto_topo"]
        gostos = im.get("para_quem_gosta") or []
        itens_rapidos = [b["origem"]["regiao"] + " · " + b["origem"]["uf"], "Desde " + b.get("fundacao", ""),
                         "Thrash · Death · Punk", "Letras em português", f'{len(b["formacao"])} integrantes']
        rapida = "".join(f"<li>{esc(x)}</li>" for x in itens_rapidos)
        if gostos:
            rapida += f'<li>Para quem gosta de {esc(", ".join(gostos))}</li>'
        corpo = f"""<header class="kit-hero">
  <img src="{self.m.url(foto["arquivo"], o)}" alt="{esc(foto["descricao"])}">
  <div class="wrap">
    <p class="kicker">Imprensa e contratação</p>
    <h1>Demophobia</h1>
    <p class="frase">{esc(im["frase"])}</p>
    <div class="btns">{botao_contato}<a class="btn fantasma" href="#ao-vivo">{self.icone("play")}Ver ao vivo</a>{botao_rider}</div>
    {falta_topo}
  </div>
</header>
<section class="bloco"><div class="wrap">
  <ul class="linha-rapida">{rapida}</ul>
  {"" if gostos else self.falta("2 ou 3 bandas de referência para “para quem gosta de…”: ajuda o produtor a encaixar a banda num line-up")}
</div></section>"""

        # 3. prova
        pv = im.get("prova", {})
        eventos = "".join(f'<li><strong>{esc(e["nome"])}</strong>{esc(e["detalhe"])}</li>' for e in pv.get("eventos", []))
        particip = "".join(f"<li>{esc(x)}</li>" for x in pv.get("participacoes", []))
        nomes = "".join(f"<span>{esc(n)}</span>" for n in pv.get("palco_com", []))
        numeros = ""
        if pv.get("numeros"):
            numeros = '<div class="dados" style="margin-top:28px"><dl>' + "".join(
                f'<div><dt>{esc(n["rotulo"])}</dt><dd>{esc(n["valor"])}</dd></div>' for n in pv["numeros"]) + "</dl></div>"
        else:
            numeros = self.falta("Números, se quisermos mostrar: quantos shows desde 2018, ouvintes mensais, seguidores, views")
        corpo += f"""<section class="bloco alt"><div class="wrap">
  <p class="kicker">Prova</p><h2>Onde já tocou</h2>
  <div class="prova">
    <div><h3>Eventos</h3><ul>{eventos}</ul></div>
    <div><h3>Dividiu palco com</h3><div class="nomes">{nomes}</div></div>
    <div><h3>Participações</h3><ul>{particip}</ul></div>
  </div>
  {numeros}
  {self.na_imprensa()}
</div></section>"""

        # 4. agora
        ag = im.get("agora", {})
        cards = ""
        for it in ag.get("itens", []):
            titulo = it.get("titulo")
            quando = it.get("data") or it["quando"]
            cards += f'<li><span class="quando">{esc(it["quando"])}</span><h3>{esc(it["o_que"])}{": " + esc(titulo) if titulo else ""}</h3>'
            if not titulo:
                cards += self.falta(f'nome e data: {it["o_que"]} de {it["quando"]}')
            cards += "</li>"
        corpo += f"""<section class="bloco"><div class="wrap">
  <p class="kicker">Agora</p><h2>O que vem</h2>
  <p class="lead" style="max-width:760px">{esc(ag.get("texto", ""))}</p>
  <ul class="agora">{cards}</ul>
</div></section>"""

        # 5. ao vivo
        vids = [a.video_por_slug[v] for v in im.get("videos_ao_vivo", []) if v in a.video_por_slug]
        falta_video = "" if im.get("video_ao_vivo_bom") else self.falta(
            "Um vídeo ao vivo de 1 a 3 minutos com público e som bom. O único registro hoje é o Roadie Crew Online Festival, que foi um festival online. Se não existir, filmar o próximo show pensando nisso")
        corpo += f"""<section class="bloco alt" id="ao-vivo"><div class="wrap">
  <p class="kicker">Ao vivo</p><h2>No palco</h2>
  {self.grade_videos(vids) if vids else ""}
  {falta_video}
  {self.galeria(im.get("fotos_palco", []), "fotos-palco", o).replace('class="galeria fotos-palco"', 'class="galeria fotos-palco" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr))"')}
</div></section>"""

        # 6. ouça
        ou = im.get("ouca", {})
        faixas = ou.get("faixas") or []
        lista = ""
        if faixas:
            lista = '<ol class="faixas">' + "".join(self.faixa(f) for f in faixas) + "</ol>"
        link = ou.get("link")
        corpo += f"""<section class="bloco"><div class="wrap cols">
  <div><p class="kicker">Ouça</p><h2>Comece por aqui</h2></div>
  <div>
    {lista}
    {"" if faixas else self.falta("As 3 músicas que abrem a página, na ordem de impacto (não na ordem do disco)")}
    {"" if ou.get("spotify_confirmado") else self.falta("Confirmar se o álbum está no Spotify. O CD Baby avisou em 2025 que ele foi removido; a busca de hoje ainda acha a página do álbum (open.spotify.com/album/2nF5AjsVZUFBGsT5VR7iUe), mas não consegui abrir para conferir")}
    <div class="btns" style="margin-top:20px">{f'<a class="btn primario" href="{esc(link["url"])}" target="_blank" rel="noopener">{self.icone("play")}{esc(link["rotulo"])}</a>' if link else ""}<a class="btn fantasma" href="/discografia/">Discografia completa</a></div>
  </div>
</div></section>"""

        # 7. técnico
        def campo(rotulo, valor, falta_txt):
            if valor:
                return f"<div><dt>{esc(rotulo)}</dt><dd>{esc(valor)}</dd></div>"
            return f"<div><dt>{esc(rotulo)}</dt><dd>{self.falta(falta_txt, inline=True) or '—'}</dd></div>"
        formacao = ", ".join(f'{p["nome"]} ({p["funcao"].lower()})' for p in b["formacao"])
        docs_tec = ""
        if tec.get("rider_pdf") and tec.get("rider_pdf") == tec.get("mapa_palco"):
            docs_tec = f'<a class="btn primario" href="{self.m.url(tec["rider_pdf"], o)}" download>{self.icone("dl")}Rider e mapa de palco (PDF)</a>'
        else:
            if tec.get("rider_pdf"):
                docs_tec += f'<a class="btn fantasma" href="{self.m.url(tec["rider_pdf"], o)}" download>{self.icone("dl")}Rider técnico</a>'
            if tec.get("mapa_palco"):
                docs_tec += f'<a class="btn fantasma" href="{self.m.url(tec["mapa_palco"], o)}" download>{self.icone("dl")}Mapa de palco</a>'
        corpo += f"""<section class="bloco alt"><div class="wrap">
  <p class="kicker">Técnico</p><h2>Para montar o show</h2>
  <dl class="ficha">
    <div><dt>Formação</dt><dd>{esc(formacao)}{"" if tec.get("formacao_confirmada") else self.falta("confirmar a formação atual e a grafia dos nomes (Arthur Patroc ou Arthur Henrique?)")}</dd></div>
    {campo("Cidade-base", tec.get("cidade_base"), "cidade-base")}
    {campo("Tempo de set", tec.get("tempo_set"), "30, 45 ou 60 minutos?")}
    {campo("Pessoas na viagem", tec.get("pessoas_viajam"), "quantas pessoas viajam (banda + equipe)")}
    {campo("Backline", tec.get("backline"), "o que a banda leva e o que precisa da casa")}
    {campo("Disponibilidade", tec.get("raio"), "raio de viagem")}
  </dl>
  <div class="btns" style="margin-top:24px">{docs_tec}</div>
  {"" if tec.get("rider_pdf") and tec.get("mapa_palco") else self.falta("Rider técnico e mapa de palco em PDF. Se não existirem, mandar a lista de equipamento e eu monto")}
  {"" if not tec.get("rider_pdf") or tec.get("rider_confirmado") else self.falta("O rider é de 2022 (enviado ao Coletivo Rock ABC). Confirmar se o equipamento continua o mesmo")}
</div></section>"""

        # 8. material
        downloads = ""
        for d in mat.get("downloads", []):
            arq = MIDIAS / d["arquivo"]
            self.m.url(d["arquivo"], o)
            with self.og.Image.open(arq) as i:
                dims = f"{i.width} × {i.height}"
            downloads += f"""<a class="dl" href="/midias/{esc(d["arquivo"])}" download>
  <img src="{self.m.url(d["miniatura"], o)}" alt="" loading="lazy">
  <div class="corpo"><div><strong>{esc(d["rotulo"])}</strong><small>JPG · {dims} · {arq.stat().st_size / 1_000_000:.1f} MB</small></div>{self.icone("dl")}</div>
</a>"""
        for lg in mat.get("logos", []):
            previa = lg.get("previa") or lg["arquivo"]
            fundo = "#e9e1d0" if lg.get("fundo") == "claro" else "#0b0a09"
            downloads += f"""<a class="dl" href="{self.m.url(lg["arquivo"], o)}" download>
  <img src="{self.m.url(previa, o)}" alt="" loading="lazy" style="object-fit:contain;padding:28px;background:{fundo}">
  <div class="corpo"><div><strong>{esc(lg["rotulo"])}</strong><small>{esc(lg["formato"])}</small></div>{self.icone("dl")}</div>
</a>"""
        credito = f'<p class="dim">Fotos: {esc(mat["credito_fotos"])}</p>' if mat.get("credito_fotos") else self.falta("Crédito das fotos (quem fotografou?)")
        corpo += f"""<section class="bloco"><div class="wrap">
  <p class="kicker">Material</p><h2>Para divulgar</h2>
  <h3 style="margin-bottom:12px">Release curto</h3>
  <p class="release" id="release-curto">{esc(mat.get("release_curto", ""))}</p>
  <button class="copiar" data-copiar="release-curto">Copiar release</button>
  {"" if mat.get("release_atualizado") else self.falta("Atualizar o release com os singles de 2026 e o EP de 2027 quando tiverem nome")}
  <div class="downloads" style="margin-top:40px">{downloads}</div>
  {"" if mat.get("logos") else self.falta("Logo em PNG transparente (branco e preto)")}
  {credito}
</div></section>"""

        # 9. contato
        linhas = []
        if ct.get("responsavel"):
            linhas.append(f'<p class="lead" style="margin:0 0 8px">{esc(ct["responsavel"])}</p>')
        else:
            linhas.append(self.falta("Nome de quem responde pela contratação"))
        if wa:
            linhas.append(f'<p><a class="email" href="https://wa.me/{esc(re.sub(r"[^0-9]", "", wa))}" target="_blank" rel="noopener">WhatsApp {esc(wa)}</a></p>')
        else:
            linhas.append(self.falta("WhatsApp de contratação"))
        linhas.append(f'<p><a class="email" href="mailto:{esc(email)}">{esc(email)}</a></p>')
        ass = im.get("assessoria")
        assessoria = ""
        if ass:
            assessoria = f"""<div class="contato"><h3>Assessoria</h3><div class="assessoria">
  <img src="{self.m.url(ass["logo"], o)}" alt="Logotipo da {esc(ass["nome"])}" loading="lazy"><p>{esc(ass["texto"])}</p></div>
  {"" if ass.get("confirmada") else self.falta("A Agência 1a1 ainda é a assessoria? Se não, este bloco sai")}</div>"""
        corpo += f"""<section class="bloco alt"><div class="wrap">
  <p class="kicker">Contato</p><h2>Vamos marcar</h2>
  <div class="contatos">
    <div class="contato"><h3>Contratação</h3>{"".join(linhas)}{self.redes()}</div>
    {assessoria}
  </div>
</div></section>"""

        if not self.producao and self.faltas:
            corpo = (f'<p class="aviso-previa">Prévia interna: {len(self.faltas)} itens marcados em vermelho ainda faltam. '
                     'Em produção essas marcas somem.</p>') + corpo
        og = self.og.gerar("imprensa", "Imprensa e contratação", "Demophobia", foto["arquivo"])
        self.pagina("/imprensa/", im["titulo"], im["linha_fina"], corpo, og=og, indexavel=False)

    def nao_encontrada(self):
        corpo = f"""{self.cabeca("Erro 404", "Página não encontrada", "O endereço pode ter mudado. Tente pela discografia ou pela página inicial.")}
<section class="bloco"><div class="wrap"><div class="btns"><a class="btn primario" href="/">Página inicial</a><a class="btn fantasma" href="/discografia/">Discografia</a></div></div></section>"""
        og = self.og.gerar("404", "Página não encontrada", "Demophobia", None)
        self.pagina("/404.html", "Página não encontrada", "Página não encontrada.", corpo, og=og, indexavel=False)

    # ---- arquivos técnicos

    def indice(self):
        a = self.a
        itens = [{"tipo": "lancamento", "slug": l["slug"], "titulo": l["titulo"], "data": l["data"],
                  "subtipo": l["tipo"], "url": f"/discografia/{l['slug']}/", "linha_fina": l["linha_fina"]} for l in a.lancamentos]
        itens += [{"tipo": "noticia", "slug": n["slug"], "titulo": n["titulo"], "data": n["data"],
                   "url": f"/noticias/{n['slug']}/", "linha_fina": n["linha_fina"]} for n in a.noticias]
        itens += [{"tipo": "show", "slug": s["_slug"], "titulo": s.get("evento") or "Show", "data": s["data"],
                   "url": "/shows/", "cidade": s.get("cidade")} for s in a.shows]
        itens += [{"tipo": "video", "slug": v["slug"], "titulo": v["titulo"], "data": str(v["ano"]),
                   "url": "/videos/", "youtube": v["youtube"]} for v in a.videos]
        itens += [{"tipo": "midia", "slug": m["slug"], "titulo": m["titulo"], "veiculo": m["veiculo"], "data": m["data"],
                   "url": "/noticias/", "externa": m["url"], "categoria": m["tipo"]} for m in a.midia]
        itens += [{"tipo": "produto", "slug": p["slug"], "titulo": p["titulo"], "url": f"/loja/{p['slug']}/",
                   "preco": p.get("preco"), "disponivel": p.get("disponivel", True)} for p in a.produtos]
        doc = {"contrato": CONTRATO_INDEX, "gerado_em": dt.datetime.now(FUSO).isoformat(timespec="seconds"),
               "site": DOMINIO, "total": len(itens), "itens": itens}
        # trava: o contrato é público, então quebrar o formato quebra o build
        assert doc["total"] == len(doc["itens"]), "index.json: total diferente da lista"
        for it in itens:
            assert it["url"].startswith("/") and not it["url"].startswith("/imprensa"), f"index.json: url inválida {it}"
            assert it["tipo"] in {"lancamento", "noticia", "show", "video", "produto", "midia"}, f"index.json: tipo inválido {it}"
        self.escrever("/index.json", json.dumps(doc, ensure_ascii=False, indent=1) + "\n")

    def sitemap(self):
        linhas = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for r in self.rotas:
            if not r["sitemap"]:
                continue
            lm = f"<lastmod>{r['lastmod'].isoformat()}</lastmod>" if r["lastmod"] else ""
            linhas.append(f"  <url><loc>{DOMINIO}{r['rota']}</loc>{lm}</url>")
        linhas.append("</urlset>")
        self.escrever("/sitemap.xml", "\n".join(linhas) + "\n")

    def robots(self):
        # rastreio sempre liberado: quem tira do índice é o X-Robots-Tag do _headers
        self.escrever("/robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {DOMINIO}/sitemap.xml\n")

    def llms(self):
        a, b = self.a, self.a.banda
        l = [f"# Demophobia", "", f"> {b['linha_fina']}", "",
             f"Formação: " + ", ".join(f"{p['nome']} ({p['funcao']})" for p in b["formacao"]) + ".",
             f"Contato: {b['contato']['email']}", "", "## Páginas", "",
             f"- [A banda]({DOMINIO}/banda/): bio, formação e trajetória",
             f"- [Discografia]({DOMINIO}/discografia/)", f"- [Shows]({DOMINIO}/shows/)",
             f"- [Vídeos]({DOMINIO}/videos/)", f"- [Notícias]({DOMINIO}/noticias/)", f"- [Loja]({DOMINIO}/loja/)",
             "", "## Discografia", ""]
        l += [f"- [{x['titulo']}]({DOMINIO}/discografia/{x['slug']}/): {TIPOS_LANCAMENTO[x['tipo']]}, {x['data']}. {x['linha_fina']}" for x in a.lancamentos]
        if a.noticias:
            l += ["", "## Notícias", ""]
            l += [f"- [{n['titulo']}]({DOMINIO}/noticias/{n['slug']}/): {n['data']}. {n['linha_fina']}" for n in a.noticias]
        if a.midia:
            l += ["", "## Na mídia", ""]
            l += [f"- [{m['titulo']}]({m['url']}): {m['veiculo']}, {m['data']}." for m in a.midia]
        l += ["", f"Índice completo em JSON: {DOMINIO}/index.json", ""]
        self.escrever("/llms.txt", "\n".join(l))

    def headers(self):
        comum = """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()

/fontes/*
  Cache-Control: public, max-age=31536000, immutable

/midias/*
  Cache-Control: public, max-age=86400

/og/*
  Cache-Control: public, max-age=86400
"""
        if self.producao:
            gate = """
# produção: só a imprensa fica fora do índice (rastreio liberado, para o buscador ler este noindex)
/imprensa/*
  X-Robots-Tag: noindex, nofollow

/midias/imprensa/*
  X-Robots-Tag: noindex, nofollow
"""
        else:
            gate = """
# prévia: nada indexa. Para indexar, o build precisa de --producao.
/*
  X-Robots-Tag: noindex, nofollow
"""
        self.escrever("/_headers", comum + gate)

    def redirects(self):
        self.escrever("/_redirects", "# origem  destino  código\n/press  /imprensa/  301\n/release  /imprensa/  301\n")

    def estaticos(self):
        destino = DIST / "fontes"
        destino.mkdir(parents=True, exist_ok=True)
        for f in (ASSETS / "fontes").glob("*.woff2"):
            shutil.copy2(f, destino / f.name)
        # favicon a partir da capa em destaque
        l = self.a.lanc_por_slug[self.a.banda["destaque"]]
        origem = l["capa"]["arquivo"] if l.get("capa") else self.a.banda["foto"]["arquivo"]
        with self.og.Image.open(MIDIAS / origem) as im:
            im.convert("RGB").resize((192, 192), self.og.Image.LANCZOS).save(DIST / "favicon.png", optimize=True)

    # ---- travas finais

    def conferir(self):
        """Todo href/src interno precisa existir em dist/. Asset faltante é sempre defeito."""
        erros = []
        padrao = re.compile(r'(?:href|src|data-grande)="(/[^"#?]*)')
        for pagina in DIST.rglob("*.html"):
            for alvo in padrao.findall(pagina.read_text(encoding="utf-8")):
                caminho = DIST / alvo.lstrip("/")
                if alvo.endswith("/"):
                    caminho = caminho / "index.html"
                if not caminho.exists():
                    erros.append(f"{pagina.relative_to(DIST)} aponta para {alvo}, que não existe")
        imprensa = DIST / "imprensa" / "index.html"
        if 'content="noindex' not in imprensa.read_text(encoding="utf-8"):
            erros.append("imprensa/index.html sem meta noindex")
        for arq in ("sitemap.xml", "llms.txt", "index.json"):
            if "/imprensa" in (DIST / arq).read_text(encoding="utf-8"):
                erros.append(f"{arq} menciona /imprensa")
        for pagina in DIST.rglob("*.html"):
            if 'href="/imprensa/' in pagina.read_text(encoding="utf-8") and pagina != imprensa:
                erros.append(f"{pagina.relative_to(DIST)} tem link para /imprensa/")
        if erros:
            raise ErroDeConteudo("travas do build:\n  " + "\n  ".join(erros))

    def construir(self):
        if DIST.exists():
            shutil.rmtree(DIST)
        DIST.mkdir(parents=True)
        self.home()
        self.banda()
        self.discografia()
        self.shows()
        self.videos()
        self.noticias()
        self.loja()
        self.imprensa()
        self.nao_encontrada()
        self.m.copiar()
        self.estaticos()
        self.indice()
        self.sitemap()
        self.robots()
        self.llms()
        self.headers()
        self.redirects()
        self.conferir()


def servir(porta: int):
    manip = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DIST))
    print(f"servindo site/dist em http://localhost:{porta}/  (Ctrl+C para parar)")
    http.server.ThreadingHTTPServer(("", porta), manip).serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--producao", action="store_true", help="gera o site indexável (sem noindex global)")
    ap.add_argument("--servir", type=int, metavar="PORTA", help="depois do build, serve dist/ nesta porta")
    ap.add_argument("--hoje", help="força a data de hoje (AAAA-MM-DD), para testar agenda e agendamento")
    args = ap.parse_args()
    hoje = dt.date.fromisoformat(args.hoje) if args.hoje else dt.datetime.now(FUSO).date()
    try:
        site = Site(Acervo(hoje), producao=args.producao)
        site.construir()
    except ErroDeConteudo as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)
    paginas = sum(1 for _ in DIST.rglob("*.html"))
    arquivos = sum(1 for p in DIST.rglob("*") if p.is_file())
    modo = "PRODUÇÃO (indexável)" if args.producao else "PRÉVIA (noindex em tudo)"
    print(f"ok: {paginas} páginas, {arquivos} arquivos em site/dist · modo {modo}")
    if site.faltas:
        print(f"\n/imprensa/ tem {len(site.faltas)} pendências" + (" (escondidas em produção):" if args.producao else " (marcadas na prévia):"))
        for f in site.faltas:
            print(f"  - {f}")
    if args.servir:
        servir(args.servir)


if __name__ == "__main__":
    main()
