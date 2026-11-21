import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app, digest, password_hash
from backend.markdown import render_inline
from backend.store import DomainError, Token


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        database = Path(self.directory.name) / 'nira.sqlite'
        self.app = create_app(dict(database_url=f'sqlite:///{database}', origin='http://localhost',
                                   username='owner', password_hash=password_hash('synthetic-fixture'),
                                   path_prefix='', secure_cookie=False))
        self.store = self.app.state.store

    def test_token_management_requires_browser_session(self):
        raw = 'synthetic-delegated-token'
        with self.store.tx() as database:
            database.add(Token(name='Delegated', digest=digest(raw), scope='write'))
        with TestClient(self.app, base_url='http://localhost') as client:
            delegated = {'Authorization': f'Bearer {raw}'}
            self.assertEqual(client.get('/api/projects', headers=delegated).status_code, 200)
            self.assertEqual(client.get('/api/tokens', headers=delegated).status_code, 403)
            self.assertEqual(client.post('/api/tokens', headers=delegated, json={'name':'New'}).status_code, 403)
            self.assertEqual(client.post('/api/login', json={'username':'owner','password':'synthetic-fixture'}).status_code, 200)
            self.assertEqual(client.get('/api/tokens').status_code, 200)
            self.assertEqual(client.post('/api/tokens', json={'name':'New'}).status_code, 403)
            csrf = client.get('/api/me').json()['csrf']
            self.assertEqual(client.post('/api/tokens', headers={'X-CSRF-Token':csrf}, json={'name':'Owner issued'}).status_code, 201)

    def test_parallel_blocking_links_cannot_create_a_cycle(self):
        project = self.store.create_project({'name':'Synthetic','key':'SYN'})
        first = self.store.create_issue(project['id'], {'title':'One'})['id']
        second = self.store.create_issue(project['id'], {'title':'Two'})['id']

        def link(pair):
            try:
                self.store.link_issues(*pair, kind='blocks')
            except DomainError as error:
                return error.message
            return 'created'

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(link, [(first, second), (second, first)]))
        self.assertCountEqual(results, ['created', 'Blocking links cannot form a cycle'])

    def test_markdown_uses_text_and_link_contexts(self):
        self.assertEqual(render_inline('@@CODE0@@ `&`'), '@@CODE0@@ <code>&amp;</code>')
        self.assertEqual(render_inline('[`Example`](https://example.com?a=1&b=2)'),
                         '<a href="https://example.com?a=1&amp;b=2"><code>Example</code></a>')


if __name__ == '__main__':
    unittest.main()
