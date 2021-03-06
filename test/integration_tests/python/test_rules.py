def test_rules(randstr, data_builder, file_form, as_root, as_admin, with_user, api_db):
    # create versioned gear to cover code selecting latest gear
    gear_name = randstr()
    gear_1 = data_builder.create_gear(gear={'name': gear_name, 'version': '0.0.1'})
    gear_2 = data_builder.create_gear(gear={'name': gear_name, 'version': '0.0.2'})
    project = data_builder.create_project()

    # try to get all project rules of non-existent project
    r = as_admin.get('/projects/000000000000000000000000/rules')
    assert r.status_code == 404

    # try to get single project rule of non-existent project
    r = as_admin.get('/projects/000000000000000000000000/rules/000000000000000000000000')
    assert r.status_code == 404

    # try to get project rules w/o permissions
    r = with_user.session.get('/projects/' + project + '/rules')
    assert r.status_code == 404

    # get project rules (yet empty list)
    r = as_admin.get('/projects/' + project + '/rules')
    assert r.ok
    assert r.json() == []

    # upload file w/o any rules
    r = as_admin.post('/projects/' + project + '/files', files=file_form('test1.csv'))
    assert r.ok

    # try to add rule to non-existent project
    r = as_admin.post('/projects/000000000000000000000000/rules')
    assert r.status_code == 404

    # add read-only perms for user
    r = as_admin.post('/projects/' + project + '/permissions', json={
        '_id': with_user.user, 'access': 'ro'})
    assert r.ok

    # try to add rule w/ read-only project perms
    r = with_user.session.post('/projects/' + project + '/rules')
    assert r.status_code == 403

    # add invalid project rule w/ non-existent gear
    # NOTE this is a legacy rule
    r = as_admin.post('/projects/' + project + '/rules', json={
        'alg': 'non-existent-gear-name',
        'name': 'csv-job-trigger-rule',
        'any': [],
        'all': [
            {'type': 'file.type', 'value': 'tabular data'},
        ]
    })
    assert r.ok
    rule = r.json()['_id']

    # get project rules (verify rule was added)
    r = as_admin.get('/projects/' + project + '/rules')
    assert r.ok
    assert r.json()[0]['alg'] == 'non-existent-gear-name'

    # try to get single project rule using non-existent rule id
    r = as_admin.get('/projects/' + project + '/rules/000000000000000000000000')
    assert r.status_code == 404

    # try to upload file that matches invalid rule (500, Unknown gear)
    # NOTE with an invalid and some valid rules an upload could conceivably return
    #      a 500 after already having created jobs for the valid rules
    r = as_admin.post('/projects/' + project + '/files', files=file_form('test.csv'))
    assert r.status_code == 500

    # try to update rule of non-existent project
    r = as_admin.put('/projects/000000000000000000000000/rules/000000000000000000000000')
    assert r.status_code == 404

    # try to update non-existent rule
    r = as_admin.put('/projects/' + project + '/rules/000000000000000000000000')
    assert r.status_code == 404

    # try to update rule w/ read-only project perms
    r = with_user.session.put('/projects/' + project + '/rules/' + rule, json={'alg': gear_name})
    assert r.status_code == 403

    # update rule to use a valid gear
    r = as_admin.put('/projects/' + project + '/rules/' + rule, json={'alg': gear_name})
    assert r.ok

    # verify rule was updated
    r = as_admin.get('/projects/' + project + '/rules/' + rule)
    assert r.ok
    assert r.json()['alg'] == gear_name

    # upload file that matches rule
    r = as_admin.post('/projects/' + project + '/files', files=file_form('test2.csv'))
    assert r.ok

    # test that job was created via rule
    gear_jobs = [job for job in api_db.jobs.find({'gear_id': gear_2})]
    assert len(gear_jobs) == 1
    assert len(gear_jobs[0]['inputs']) == 1
    assert gear_jobs[0]['inputs'][0]['name'] == 'test2.csv'

    # try to delete rule of non-existent project
    r = as_admin.delete('/projects/000000000000000000000000/rules/000000000000000000000000')
    assert r.status_code == 404

    # try to delete non-existent rule
    r = as_admin.delete('/projects/' + project + '/rules/000000000000000000000000')
    assert r.status_code == 404

    # try to delete rule w/ read-only project perms
    r = with_user.session.delete('/projects/' + project + '/rules/' + rule)
    assert r.status_code == 403

    # delete rule
    r = as_admin.delete('/projects/' + project + '/rules/' + rule)
    assert r.ok


    # add valid container.has-<something> project rule w/ non-existent gear
    # NOTE this is a legacy rule
    r = as_admin.post('/projects/' + project + '/rules', json={
        'alg': gear_name,
        'name': 'txt-job-trigger-rule-with-measurement',
        'any': [
            {'type': 'container.has-measurement', 'value': 'functional'},
            {'type': 'container.has-measurement', 'value': 'anatomical'}
        ],
        'all': [
            {'type': 'file.type', 'value': 'text'},
        ]
    })
    assert r.ok
    rule2 = r.json()['_id']

    # upload file that matches only part of rule
    r = as_admin.post('/projects/' + project + '/files', files=file_form('test3.txt'))
    assert r.ok

    # test that job was not created via rule
    gear_jobs = [job for job in api_db.jobs.find({'gear_id': gear_2})]
    assert len(gear_jobs) == 1 # still 1 from before

    # update test2.csv's metadata to include a valid measurement to spawn job
    metadata = {
        'project':{
            'label': 'rule project',
            'files': [
                {
                    'name': 'test2.csv',
                    'measurements': ['functional']
                }
            ]
        }
    }

    r = as_root.post('/engine',
        params={'level': 'project', 'id': project},
        files=file_form(meta=metadata)
    )
    assert r.ok

    # test that only one job was created via rule
    gear_jobs = [job for job in api_db.jobs.find({'gear_id': gear_2})]
    assert len(gear_jobs) == 2
    assert len(gear_jobs[1]['inputs']) == 1
    assert gear_jobs[1]['inputs'][0]['name'] == 'test3.txt'

    # delete rule
    r = as_admin.delete('/projects/' + project + '/rules/' + rule2)
    assert r.ok

    # TODO add and test 'new-style' rules
