"""Consultas paralelas somente de leitura, com cache efêmero em memória.

O cache nunca é salvo em disco e é invalidado após alterações de pacotes.
As exceções não são armazenadas, para permitir retentativa imediatamente.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time
from typing import Callable

from .backend import Package, search_aur, search_command, search_repositories

SOURCE_KEYS = ('Repositório', 'AUR', 'Comandos')
SOURCE_FUNCS = {
    'Repositório': search_repositories,
    'AUR': search_aur,
    'Comandos': search_command,
}
CACHE_TTL_SECONDS = 300  # 5 minutos; um resultado novo pode ser solicitado após o prazo.
CACHE_MAX_ENTRIES = 90


class SearchCache:
    """Cache LRU, isolado por fonte e termo, sem cache de falhas."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._lock = Lock()
        self._items: OrderedDict[tuple[str, str], tuple[float, tuple[Package, ...]]] = OrderedDict()

    def get(self, key: tuple[str, str]) -> list[Package] | None:
        with self._lock:
            found = self._items.get(key)
            if found is None:
                return None
            when, items = found
            if self.clock() - when >= self.ttl:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return list(items)

    def put(self, key: tuple[str, str], items: list[Package]) -> None:
        with self._lock:
            self._items[key] = (self.clock(), tuple(items))
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_cache = SearchCache()


def invalidate_search_cache() -> None:
    """Descarta versões/estados de pacotes após instalação ou atualização."""
    _cache.clear()


def search_parallel(query: str, scope: str,
                    on_progress: Callable[[str, int, int, bool], None] | None = None,
                    *, cache: SearchCache | None = None,
                    sources: dict[str, Callable[[str], list[Package]]] | None = None,
                    ) -> tuple[list[Package], dict[str, str]]:
    """Pesquisa simultaneamente nas fontes ativas.  Falha de uma não cancela as outras.

    on_progress(fonte, concluídas, total, cache_hit) indica avanço, sem bloquear Qt.
    """
    query = query.strip()
    if len(query) < 2:
        raise ValueError('Digite pelo menos dois caracteres.')
    if scope not in ('Todos',) + SOURCE_KEYS:
        raise ValueError('Filtro de pesquisa desconhecido.')
    selected = SOURCE_KEYS if scope == 'Todos' else (scope,)
    cache = _cache if cache is None else cache
    sources = SOURCE_FUNCS if sources is None else sources
    key_query = query  # Não alterar regex ou maiúsculas fornecidas ao pacman.
    completed = 0
    results: dict[str, list[Package]] = {}
    errors: dict[str, str] = {}
    futures = {}

    # Apenas buscas ainda não presentes no cache abrem processos pacman/yay.
    with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix='wolf-search') as pool:
        for source in selected:
            key = (source, key_query)
            cached = cache.get(key)
            if cached is not None:
                results[source] = cached
                completed += 1
                if on_progress:
                    on_progress(source, completed, len(selected), True)
            else:
                futures[pool.submit(sources[source], query)] = (source, key)
        for future in as_completed(futures):
            source, key = futures[future]
            try:
                packages = future.result()
                results[source] = packages
                cache.put(key, packages)
            except Exception as exc:
                errors[source] = str(exc)
            completed += 1
            if on_progress:
                on_progress(source, completed, len(selected), False)

    # Ordem fixa e previsível: pacotes de repositório, AUR, comandos.
    return [p for source in selected for p in results.get(source, [])], errors
