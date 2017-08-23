import time

from api.web.request import AccessType


# NOTE these tests assume they are not running in parallel w/ other tests
# by relying on the last entry in the logs

def test_access_log_fails(data_builder, as_admin, log_db):
    project = data_builder.create_project()
    file_name = 'one.csv'

    log_db.command('collMod', 'access_log', validator={'$and': [{'foo': {'$exists': True}}]}, validationLevel='strict')

    # Upload files
    r = as_admin.post('/projects/' + project + '/files', files={
        'file': (file_name, 'test-content')
    })
    assert r.ok

    ###
    # Test file delete request fails and does not delete file
    ###

    r = as_admin.delete('/projects/' + project + '/files/' + file_name)
    assert r.status_code == 500

    r = as_admin.get('/projects/' + project)
    assert r.ok
    assert r.json()['files']

    log_db.command('collMod', 'access_log', validator={}, validationLevel='strict')
