# Demophobia · site

Site de demophobia.band. O conteúdo mora em `conteudo/` (JSON), `site/build.py` gera o HTML
em `site/dist/` e o GitHub Actions publica no Cloudflare Pages. Publicar é dar push.

## Dia a dia

- **Notícia:** crie `conteudo/noticias/<slug>.json` (copie uma existente). Data futura = agendada;
  o build diário publica no dia. `"publicado": false` = rascunho.
- **Show:** `conteudo/shows/AAAA-MM-DD-cidade.json`. Datas futuras vão para a agenda, passadas para o histórico.
- **Lançamento:** `conteudo/lancamentos/<slug>.json`. O destaque da home é `destaque` em `banda.json`.
- **Produto da loja:** `conteudo/loja/<slug>.json`. `compra.url` = link de pagamento (Mercado Pago);
  sem link, o botão vira "Encomendar por e-mail". `"publicado": false` esconde.
- **Imprensa e contratação:** `conteudo/imprensa.json`. A página `/imprensa/` não aparece no menu
  nem no sitemap e tem noindex: só abre por link direto. Campos vazios aparecem marcados como
  FALTA na prévia e somem em produção.
- **Imagens:** em `conteudo/midias/`. Só as que o site usa são publicadas; mídia citada e ausente trava o build.

## Rodar local

```
pip install pillow
python3 site/build.py --servir 8765     # prévia em http://localhost:8765
python3 site/build.py --producao        # como vai ao ar
```

## Deploy (uma vez)

1. Cloudflare → Workers & Pages → Create → Pages → **Direct Upload**, projeto `demophobia`
   (sem conectar ao GitHub: quem publica é o CI).
2. Token de API com **Account · Cloudflare Pages · Edit**.
3. No GitHub, Settings → Secrets → Actions: `CLOUDFLARE_API_TOKEN` e `CLOUDFLARE_ACCOUNT_ID`.
4. Push na `main` publica a prévia em demophobia.pages.dev, com noindex.
5. Domínio por último: Custom domains → demophobia.band. No mesmo dia, inverter o padrão do
   workflow para produção (comentário no topo de `.github/workflows/build.yml`) e conferir o
   `robots.txt` servido.
