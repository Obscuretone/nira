import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app, digest, password_hash
from backend.store import Issue, Project, Store, Token


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'nira.sqlite'
        self.config = dict(database_url=f'sqlite:///{path}', origin='http://localhost',
                           username='owner', password_hash=password_hash('workspace-fixture'),
                           path_prefix='', secure_cookie=False)

    def test_upgrade_groups_legacy_projects_and_preserves_explicit_moves(self):
        store = Store(self.config['database_url'])
        self.addCleanup(store.engine.dispose)
        Project.__table__.create(store.engine)
        Issue.__table__.create(store.engine)
        with store.tx() as database:
            personal = Project(key='HOME', name='Household')
            plugin = Project(key='FIELD', name='Field Scope', category='Plugin business')
            database.add_all([personal, plugin])
            database.flush()
            issue = Issue(project_id=plugin.id, number=1, title='Agency pilot')
            database.add(issue)
            database.flush()
            issue_id = issue.id

        store.initialize()
        self.assertEqual([p['key'] for p in store.list_projects('personal')], ['HOME'])
        self.assertEqual([p['key'] for p in store.list_projects('plugin-company')], ['FIELD'])
        self.assertEqual(store.list_issues(workspace='personal')['total'], 0)
        self.assertEqual(store.list_issues(workspace='plugin-company')['items'][0]['id'], issue_id)
        project = store.get_project('FIELD')
        store.update_project('FIELD', {'workspace_key': 'personal'}, project['version'])
        store.initialize()
        self.assertEqual(store.get_issue('FIELD-1')['workspace_key'], 'personal')
        self.assertEqual(store.get_issue('FIELD-1')['id'], issue_id)
        self.assertEqual(store.list_projects('plugin-company'), [])

    def test_direct_issue_uses_workspace_shell_after_sign_in(self):
        self.config['path_prefix'] = '/nira'
        app = create_app(self.config)
        store = app.state.store
        self.addCleanup(store.engine.dispose)
        raw_token = 'workspace-read-fixture'
        with store.tx() as database:
            database.add(Token(name='Preview', digest=digest(raw_token), scope='read'))

        with TestClient(app, base_url='http://localhost/nira', root_path='/nira') as client:
            path = '/browse/issue/FIELD-1'
            signed_out = client.get(path)
            self.assertEqual(signed_out.status_code, 401)
            self.assertIn('data-next="/nira/browse/issue/FIELD-1"', signed_out.text)
            token_headers = {'Authorization': f'Bearer {raw_token}'}
            self.assertEqual(client.post('/api/workspaces', headers=token_headers,
                                         json={'name': 'Agency'}).status_code, 403)

            login = client.post('/api/login', json={'username': 'owner', 'password': 'workspace-fixture'})
            self.assertEqual(login.status_code, 200)
            headers = {'X-CSRF-Token': login.json()['csrf']}
            self.assertEqual(client.post('/api/workspaces', json={'name': 'Agency'}).status_code, 403)
            workspace = client.post('/api/workspaces', headers=headers, json={'name': 'Agency'}).json()
            project = client.post('/api/projects', headers=headers, json={
                'key': 'FIELD', 'name': 'Field Scope', 'workspace_key': workspace['key'],
            }).json()
            issue = client.post('/api/projects/FIELD/issues', headers=headers,
                                json={'title': 'Agency pilot'}).json()
            direct = client.get(path)
            self.assertEqual(direct.status_code, 200)
            self.assertIn('src="/nira/assets/workspace-app.js?v=2"', direct.text)
            self.assertIn('content="/nira" name="nira-base"', direct.text)
            self.assertIn("script-src 'self'", direct.headers['content-security-policy'])
            self.assertEqual(client.get('/assets/workspace-app.js').status_code, 200)
            self.assertEqual(client.get('/api/issues/FIELD-1').json()['workspace_key'], 'agency')
            self.assertEqual(client.get(f'/browse/issue/{issue["id"]}').status_code, 200)
            self.assertEqual(client.get('/browse/issue/FIELD-999').status_code, 404)
            self.assertIn('Agency pilot', client.get(path, headers=token_headers).text)
            moved = client.patch('/api/projects/FIELD', headers=headers, json={
                'workspace_key': 'personal', 'expected_version': project['version'],
            })
            self.assertEqual(moved.status_code, 200)
            self.assertEqual(client.get('/api/issues/FIELD-1').json()['id'], issue['id'])
