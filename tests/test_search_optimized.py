"""Regressões sem pacman, AUR, rede ou escrita no host."""
from pathlib import Path
import sys
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wolfpkg.backend import Package
from wolfpkg.search_service import SearchCache, search_parallel


class ParallelSearchTests(unittest.TestCase):
    def setUp(self):
        self.now = [10.0]
        self.cache = SearchCache(ttl=300, max_entries=10, clock=lambda: self.now[0])
        self.count = {'Repositório': 0, 'AUR': 0, 'Comandos': 0}
        def source(name):
            def fn(term):
                self.count[name] += 1
                return [Package('AUR' if name == 'AUR' else 'Repositório',
                                'aur' if name == 'AUR' else 'extra',
                                f'{name.lower()}-{term}', '1', '')]
            return fn
        self.sources = {name: source(name) for name in self.count}

    def test_all_three_sources_and_cache(self):
        progress = []
        out, errors = search_parallel('nc', 'Todos',
            lambda *args: progress.append(args), cache=self.cache, sources=self.sources)
        self.assertFalse(errors)
        self.assertEqual([p.name for p in out], ['repositório-nc', 'aur-nc', 'comandos-nc'])
        self.assertEqual(len(progress), 3)
        self.assertEqual(set(self.count.values()), {1})
        out2, errors2 = search_parallel('nc', 'Todos',
            lambda *args: progress.append(args), cache=self.cache, sources=self.sources)
        self.assertFalse(errors2)
        self.assertEqual([p.name for p in out2], [p.name for p in out])
        self.assertEqual(set(self.count.values()), {1})
        self.assertTrue(all(item[3] for item in progress[-3:]))

    def test_cache_expires_and_invalidation(self):
        search_parallel('nc', 'AUR', cache=self.cache, sources=self.sources)
        self.now[0] += 299
        search_parallel('nc', 'AUR', cache=self.cache, sources=self.sources)
        self.assertEqual(self.count['AUR'], 1)
        self.now[0] += 2
        search_parallel('nc', 'AUR', cache=self.cache, sources=self.sources)
        self.assertEqual(self.count['AUR'], 2)
        self.cache.clear()
        search_parallel('nc', 'AUR', cache=self.cache, sources=self.sources)
        self.assertEqual(self.count['AUR'], 3)

    def test_repo_filter_avoids_aur_network_and_file_lookup(self):
        out, errors = search_parallel('nc', 'Repositório', cache=self.cache, sources=self.sources)
        self.assertEqual(len(out), 1)
        self.assertFalse(errors)
        self.assertEqual(self.count, {'Repositório': 1, 'AUR': 0, 'Comandos': 0})

    def test_one_source_error_does_not_hide_other_results_or_get_cached(self):
        def broken(q):
            self.count['AUR'] += 1
            raise OSError('servidor AUR indisponível')
        self.sources['AUR'] = broken
        out, errors = search_parallel('nc', 'Todos', cache=self.cache, sources=self.sources)
        self.assertEqual(len(out), 2)
        self.assertIn('AUR', errors)
        search_parallel('nc', 'Todos', cache=self.cache, sources=self.sources)
        self.assertEqual(self.count['AUR'], 2)
        self.assertEqual(self.count['Repositório'], 1)
        self.assertEqual(self.count['Comandos'], 1)

    def test_three_queries_do_run_concurrently(self):
        barrier = threading.Barrier(3, timeout=2)
        def source(label):
            def fn(q):
                barrier.wait()
                return [Package('AUR' if label == 'AUR' else 'Repositório',
                                'aur' if label == 'AUR' else 'extra', label, '1', '')]
            return fn
        funcs = {name: source(name) for name in self.count}
        out, errors = search_parallel('nc', 'Todos', cache=self.cache, sources=funcs)
        self.assertFalse(errors)
        self.assertEqual(len(out), 3)

    def test_invalid_scope(self):
        with self.assertRaises(ValueError):
            search_parallel('nc', 'uma fonte inexistente', cache=self.cache, sources=self.sources)

if __name__ == '__main__':
    unittest.main()
