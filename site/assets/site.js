// menu no celular
(() => {
  const btn = document.querySelector('.menu-btn'), menu = document.getElementById('menu');
  if (!btn || !menu) return;
  btn.addEventListener('click', () => btn.setAttribute('aria-expanded', menu.classList.toggle('aberto')));
})();

// vídeo só carrega o player do YouTube quando a pessoa clica
document.querySelectorAll('[data-youtube]').forEach(b => b.addEventListener('click', () => {
  const f = document.createElement('iframe');
  f.src = 'https://www.youtube-nocookie.com/embed/' + b.dataset.youtube + '?autoplay=1';
  f.title = b.getAttribute('aria-label');
  f.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
  f.allowFullscreen = true;
  b.replaceWith(f);
}));

// galeria ampliada
(() => {
  const lb = document.querySelector('.lightbox');
  if (!lb) return;
  const img = lb.querySelector('img');
  let itens = [], i = 0, origem = null;
  const mostrar = n => { i = (n + itens.length) % itens.length; img.src = itens[i].dataset.grande; img.alt = itens[i].alt; };
  document.querySelectorAll('[data-galeria]').forEach(g => {
    const imgs = [...g.querySelectorAll('img')];
    g.querySelectorAll('button').forEach((b, n) => b.addEventListener('click', () => {
      itens = imgs; origem = b; mostrar(n); lb.classList.add('aberto'); lb.querySelector('.lb-fechar').focus();
    }));
  });
  const fechar = () => { lb.classList.remove('aberto'); origem && origem.focus(); };
  lb.querySelector('.lb-fechar').onclick = fechar;
  lb.querySelector('.lb-ant').onclick = () => mostrar(i - 1);
  lb.querySelector('.lb-prox').onclick = () => mostrar(i + 1);
  lb.addEventListener('click', e => { if (e.target === lb) fechar(); });
  document.addEventListener('keydown', e => {
    if (!lb.classList.contains('aberto')) return;
    if (e.key === 'Escape') fechar();
    if (e.key === 'ArrowLeft') mostrar(i - 1);
    if (e.key === 'ArrowRight') mostrar(i + 1);
  });
})();
