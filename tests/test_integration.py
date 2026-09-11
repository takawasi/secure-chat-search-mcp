import pytest
from fastapi.testclient import TestClient
from secure_chat_search.app import create_app
from secure_chat_search.auth import DemoMemberships
from secure_chat_search.core import Database, DomainError, Settings
from secure_chat_search.integration import ExistingPoCAuthorizer
from secure_chat_search.search import SearchService


def test_existing_allowlist_is_checked_each_call(env):
    state = {'enabled': True}
    auth = ExistingPoCAuthorizer(DemoMemberships(), lambda sub: state['enabled'])
    service = SearchService(env.db, auth, env.settings)
    key = service.search(env.alice, '納期')['results'][0]['id']
    assert service.fetch(env.alice, key)['text']
    state['enabled'] = False
    with pytest.raises(DomainError) as exc:
        service.fetch(env.alice, key)
    assert exc.value.status == 403


def test_existing_allowlist_failure_is_closed(env):
    def unavailable(sub):
        raise RuntimeError('private error')
    service = SearchService(env.db, ExistingPoCAuthorizer(DemoMemberships(), unavailable), env.settings)
    with pytest.raises(DomainError) as exc:
        service.search(env.alice, '納期')
    assert exc.value.code == 'allowlist_unavailable' and 'private' not in str(exc.value)


def test_embedded_private_db_requires_no_dummy_credentials(tmp_path):
    settings = Settings(_env_file=None, mode='embedded', database_url='sqlite:///' + str(tmp_path/'embedded.sqlite3'))
    db = Database(settings)
    db.initialize()
    with pytest.raises(DomainError) as exc:
        create_app(settings, db=db)
    assert exc.value.code == 'embedded_only'
    db.close()


def test_demo_cannot_accept_external_webhook_even_with_token(env):
    settings = env.settings.model_copy(update={'webhook_token':'c3ludGhldGlj'})
    with TestClient(create_app(settings, db=env.db)) as client:
        assert client.post('/webhooks/chatwork', content=b'{}').status_code == 404


def test_embedded_authorizer_cannot_authenticate_http():
    auth = ExistingPoCAuthorizer(DemoMemberships(), lambda sub: True)
    with pytest.raises(DomainError):
        auth.authenticate('Bearer arbitrary')
