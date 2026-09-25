#!/usr/bin/env python3
"""Baixa a miniatura de cada vídeo do YouTube que ainda não está em conteudo/midias/videos/.

Roda no CI antes do build (a rede do CI alcança o YouTube) e pode rodar na sua máquina;
as miniaturas baixadas podem ser commitadas. Se falhar, o build usa a capa do lançamento.
"""
import json
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "conteudo" / "midias" / "videos"


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    falhas = 0
    for arq in sorted((RAIZ / "conteudo" / "videos").glob("*.json")):
        yt = json.loads(arq.read_text(encoding="utf-8"))["youtube"]
        alvo = DESTINO / f"{yt}.jpg"
        if alvo.exists():
            continue
        try:
            with urllib.request.urlopen(f"https://i.ytimg.com/vi/{yt}/hqdefault.jpg", timeout=15) as r:
                alvo.write_bytes(r.read())
            print(f"baixada: {yt}")
        except Exception as e:  # sem miniatura o build usa a capa; não é motivo para parar o deploy
            falhas += 1
            print(f"aviso: miniatura de {yt} não baixou ({e})", file=sys.stderr)
    print(f"miniaturas ok, {falhas} falhas")


if __name__ == "__main__":
    main()
