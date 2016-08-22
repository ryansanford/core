import bson
import copy
import datetime
import dateutil.parser
import difflib
import pymongo
import re

from .. import files
from .. import util
from .. import config
from . import APIStorageException, APINotFoundException, containerutil

log = config.log

PROJECTION_FIELDS = ['group', 'name', 'label', 'timestamp', 'permissions', 'public']

class TargetContainer(object):

    def __init__(self, container, level):
        self.container = container
        self.level = level
        self.dbc = config.db[level]
        self.id_ = container['_id']

    def find(self, filename):
        for f in self.container.get('files', []):
            if f['name'] == filename:
                return f
        return None

    def update_file(self, fileinfo):

        update_set = {'files.$.modified': datetime.datetime.utcnow()}
        # in this method, we are overriding an existing file.
        # update_set allows to update all the fileinfo like size, hash, etc.
        for k,v in fileinfo.iteritems():
            update_set['files.$.' + k] = v
        return self.dbc.find_one_and_update(
            {'_id': self.id_, 'files.name': fileinfo['name']},
            {'$set': update_set},
            return_document=pymongo.collection.ReturnDocument.AFTER
        )

    def add_file(self, fileinfo):
        return self.dbc.find_one_and_update(
            {'_id': self.id_},
            {'$push': {'files': fileinfo}},
            return_document=pymongo.collection.ReturnDocument.AFTER
        )

# TODO: already in code elsewhere? Location?
def get_container(cont_name, _id):
    cont_name += 's'
    _id = bson.ObjectId(_id)

    return config.db[cont_name].find_one({
        '_id': _id,
    })

def get_children(cont_name, _id):
    """
    Given a container name and id, return all children of that object in the hierarchy
    """
    cid = bson.ObjectId(_id)
    if cont_name == 'session':
        return config.db.acquisitions.find({'session': cid})
    elif cont_name == 'project':
        return config.db.sessions.find({'project': cid})
    elif cont_name == 'group':
        # groups do not use ObjectIds
        return config.db.projects.find({'group':_id})
    else:
        raise ValueError('Children can only be listed from group, project or session level')

def propagate_changes(cont_name, _id, query, update):
    """
    Propagates changes down the heirarchy tree.

    cont_name and _id refer to top level container (which will not be modified here)
    """

    if cont_name == 'groups':
        project_ids = [p['_id'] for p in config.db.projects.find({'group': _id}, [])]
        session_ids = [s['_id'] for s in config.db.sessions.find({'project': {'$in': project_ids}}, [])]

        project_q = copy.deepcopy(query)
        project_q['_id'] = {'$in': project_ids}
        session_q = copy.deepcopy(query)
        session_q['_id'] = {'$in': session_ids}
        acquisition_q = copy.deepcopy(query)
        acquisition_q['session'] = {'$in': session_ids}

        config.db.projects.update_many(project_q, update)
        config.db.sessions.update_many(session_q, update)
        config.db.acquisitions.update_many(acquisition_q, update)

    elif cont_name == 'projects':
        session_ids = [s['_id'] for s in config.db.sessions.find({'project': _id}, [])]

        session_q = copy.deepcopy(query)
        session_q['project'] = _id
        acquisition_q = copy.deepcopy(query)
        acquisition_q['session'] = {'$in': session_ids}

        config.db.sessions.update_many(session_q, update)
        config.db.acquisitions.update_many(acquisition_q, update)

    elif cont_name == 'sessions':
        query['session'] = _id
        config.db.acquisitions.update_many(query, update)
    else:
        raise ValueError('changes can only be propagated from group, project or session level')

def upsert_fileinfo(cont_name, _id, fileinfo):
    # TODO: make all functions take singular noun
    cont_name += 's'

    # TODO: make all functions consume strings
    _id = bson.ObjectId(_id)

    # OPPORTUNITY: could potentially be atomic if we pass a closure to perform the modification
    result = config.db[cont_name].find_one({
        '_id': _id,
        'files.name': fileinfo['name'],
    })

    if result is None:
        fileinfo['created'] = fileinfo['modified']
        return add_fileinfo(cont_name, _id, fileinfo)
    else:
        return update_fileinfo(cont_name, _id, fileinfo)

def update_fileinfo(cont_name, _id, fileinfo):
    update_set = {'files.$.modified': datetime.datetime.utcnow()}
    # in this method, we are overriding an existing file.
    # update_set allows to update all the fileinfo like size, hash, etc.
    for k,v in fileinfo.iteritems():
        update_set['files.$.' + k] = v
    return config.db[cont_name].find_one_and_update(
        {'_id': _id, 'files.name': fileinfo['name']},
        {'$set': update_set},
        return_document=pymongo.collection.ReturnDocument.AFTER
    )

def add_fileinfo(cont_name, _id, fileinfo):
    return config.db[cont_name].find_one_and_update(
        {'_id': _id},
        {'$push': {'files': fileinfo}},
        return_document=pymongo.collection.ReturnDocument.AFTER
    )

def _group_id_fuzzy_match(group_id, project_label):
    existing_group_ids = [g['_id'] for g in config.db.groups.find(None, ['_id'])]
    if group_id.lower() in existing_group_ids:
        return group_id.lower(), project_label
    group_id_matches = difflib.get_close_matches(group_id, existing_group_ids, cutoff=0.8)
    if len(group_id_matches) == 1:
        group_id = group_id_matches[0]
    else:
        project_label = group_id + '_' + project_label
        group_id = 'unknown'
    return group_id, project_label

def _find_or_create_destination_project(group_id, project_label, timestamp):
    group_id, project_label = _group_id_fuzzy_match(group_id, project_label)
    group = config.db.groups.find_one({'_id': group_id})
    project = config.db.projects.find_one_and_update(
        {'group': group['_id'],
         'label': {'$regex': re.escape(project_label), '$options': 'i'}
        },
        {
            '$setOnInsert': {
                'label': project_label,
                'permissions': group['roles'], 'public': False,
                'created': timestamp, 'modified': timestamp
            }
        },
        PROJECTION_FIELDS,
        upsert=True,
        return_document=pymongo.collection.ReturnDocument.AFTER,
        )
    return project

def _create_query(cont, parent_type, parent_id, upload_type):
    if upload_type == 'label':
        return {
            'label':        cont['label'],
            parent_type:    bson.ObjectId(parent_id)
        }
    elif upload_type == 'uid':
        return {
            'uid': cont['uid']
        }
    else:
        raise NotImplementedError('upload type is not handled by _create_query')

def _upsert_container(cont, cont_type, parent, parent_type, upload_type, timestamp):
    cont['modified'] = timestamp

    if cont.get('timestamp'):
        cont['timestamp'] = dateutil.parser.parse(cont['timestamp'])

        if cont_type == 'acquisition':
            session_operations = {'$min': dict(timestamp=cont['timestamp'])}
            if cont.get('timezone'):
                session_operations['$set'] = {'timezone': cont['timezone']}
            config.db.sessions.update_one({'_id': parent['_id']}, session_operations)

    if cont_type == 'session':
        cont['subject'] = containerutil.add_id_to_subject(cont.get('subject'), parent['_id'])

    query = _create_query(cont, parent_type, parent['_id'], upload_type)

    if config.db[cont_type+'s'].find_one(query) is not None:
        return _update_container_nulls(query, cont, cont_type)

    else:
        insert_vals = {
            parent_type:    parent['_id'],
            'permissions':  parent['permissions'],
            'public':       parent.get('public', False),
            'created':      timestamp
        }
        if cont_type == 'session':
            insert_vals['group'] = parent['group']
        cont.update(insert_vals)
        insert_id = config.db[cont_type+'s'].insert(cont)
        cont['_id'] = insert_id
        return cont


def _get_targets(project_obj, session, acquisition, type_, timestamp):
    target_containers = []
    if not session:
        return target_containers
    session_files = dict_fileinfos(session.pop('files', []))
    session_obj = _upsert_container(session, 'session', project_obj, 'project', type_, timestamp)
    target_containers.append(
        (TargetContainer(session_obj, 'session'), session_files)
    )
    if not acquisition:
        return target_containers
    acquisition_files = dict_fileinfos(acquisition.pop('files', []))
    acquisition_obj = _upsert_container(acquisition, 'acquisition', session_obj, 'session', type_, timestamp)
    target_containers.append(
        (TargetContainer(acquisition_obj, 'acquisition'), acquisition_files)
    )
    return target_containers


def find_existing_hierarchy(metadata):
    project = metadata.get('project', {})
    session = metadata.get('session', {})
    acquisition = metadata.get('acquisition', {})

    # Fail if some fields are missing
    try:
        acquisition_uid = acquisition['uid']
        session_uid = session['uid']
    except Exception as e:
        log.error(metadata)
        raise APIStorageException(str(e))

    # Confirm session and acquisition exist
    session_obj = config.db.sessions.find_one({'uid': session_uid}, ['project'])
    if session_obj is None:
        raise APINotFoundException('Session with uid {} does not exist'.format(session_uid))
    a = config.db.acquisitions.find_one({'uid': acquisition_uid}, ['_id'])
    if a is None:
        raise APINotFoundException('Acquisition with uid {} does not exist'.format(acquisition_uid))

    now = datetime.datetime.utcnow()
    project_files = dict_fileinfos(project.pop('files', []))
    project_obj = config.db.projects.find_one({'_id': session_obj['project']}, projection=PROJECTION_FIELDS + ['name'])
    target_containers = _get_targets(project_obj, session, acquisition, 'uid', now)
    target_containers.append(
        (TargetContainer(project_obj, 'project'), project_files)
    )
    return target_containers


def upsert_bottom_up_hierarchy(metadata):
    group = metadata.get('group', {})
    project = metadata.get('project', {})
    session = metadata.get('session', {})
    acquisition = metadata.get('acquisition', {})

    # Fail if some fields are missing
    try:
        _ = group['_id']
        _ = project['label']
        _ = acquisition['uid']
        session_uid = session['uid']
    except Exception as e:
        log.error(metadata)
        raise APIStorageException(str(e))

    session_obj = config.db.sessions.find_one({'uid': session_uid}, ['project'])
    if session_obj: # skip project creation, if session exists
        now = datetime.datetime.utcnow()
        project_files = dict_fileinfos(project.pop('files', []))
        project_obj = config.db.projects.find_one({'_id': session_obj['project']}, projection=PROJECTION_FIELDS + ['name'])
        target_containers = _get_targets(project_obj, session, acquisition, 'uid', now)
        target_containers.append(
            (TargetContainer(project_obj, 'project'), project_files)
        )
        return target_containers
    else:
        return upsert_top_down_hierarchy(metadata, 'uid')


def upsert_top_down_hierarchy(metadata, type_='label'):
    group = metadata['group']
    project = metadata['project']
    session = metadata.get('session')
    acquisition = metadata.get('acquisition')

    now = datetime.datetime.utcnow()
    project_files = dict_fileinfos(project.pop('files', []))
    project_obj = _find_or_create_destination_project(group['_id'], project['label'], now)
    target_containers = _get_targets(project_obj, session, acquisition, type_, now)
    target_containers.append(
        (TargetContainer(project_obj, 'project'), project_files)
    )
    return target_containers


def dict_fileinfos(infos):
    dict_infos = {}
    for info in infos:
        dict_infos[info['name']] = info
    return dict_infos


def update_container_hierarchy(metadata, cid, container_type):
    c_metadata = metadata.get(container_type)
    now = datetime.datetime.utcnow()
    if c_metadata.get('timestamp'):
        c_metadata['timestamp'] = dateutil.parser.parse(c_metadata['timestamp'])
    c_metadata['modified'] = now
    c_obj = _update_container_nulls({'_id': cid}, c_metadata, container_type)
    if c_obj is None:
        raise APIStorageException('container does not exist')
    if container_type in ['session', 'acquisition']:
        _update_hierarchy(c_obj, container_type, metadata)
    return c_obj

def _update_hierarchy(container, container_type, metadata):
    project_id = container.get('project') # for sessions
    now = datetime.datetime.utcnow()

    if container_type == 'acquisition':
        session = metadata.get('session', {})
        session_obj = None
        if session.keys():
            session['modified'] = now
            if session.get('timestamp'):
                session['timestamp'] = dateutil.parser.parse(session['timestamp'])
            session_obj = _update_container_nulls({'_id': container['session']},  session, 'sessions')
        if session_obj is None:
            session_obj = get_container('session', container['session'])
        project_id = session_obj['project']

    if project_id is None:
        raise APIStorageException('Failed to find project id in session obj')
    project = metadata.get('project', {})
    if project.keys():
        project['modified'] = now
        _update_container_nulls({'_id': project_id}, project, 'projects')

def _update_container_nulls(base_query, update, container_type):
    coll_name = container_type if container_type.endswith('s') else container_type+'s'
    cont = config.db[coll_name].find_one(base_query)
    if cont is None:
        raise APIStorageException('Failed to find {} object using the query: {}'.format(container_type, base_query))

    bulk = config.db[coll_name].initialize_unordered_bulk_op()

    if update.get('metadata') and not cont.get('metadata'):
        # If we are trying to update metadata fields and the container metadata does not exist or is empty,
        # metadata can all be updated at once for efficiency
        m_update = util.mongo_sanitize_fields(update.pop('metadata'))
        bulk.find(base_query).update_one({'$set': {'metadata': m_update}})

    update_dict = util.mongo_dict(update)
    for k,v in update_dict.items():
        q = {}
        q.update(base_query)
        q['$or'] = [{k: {'$exists': False}}, {k: None}]
        u = {'$set': {k: v}}
        log.debug('the query is {} and the update is {}'.format(q,u))
        bulk.find(q).update_one(u)
    bulk.execute()
    return config.db[coll_name].find_one(base_query)


def merge_fileinfos(parsed_files, infos):
    """it takes a dictionary of "hard_infos" (file size, hash)
    merging them with infos derived from a list of infos on the same or on other files
    """
    merged_files = {}
    for info in infos:
        parsed = parsed_files.get(info['name'])
        if parsed:
            path = parsed.path
            new_infos = copy.deepcopy(parsed.info)
        else:
            path = None
            new_infos = {}
        new_infos.update(info)
        merged_files[info['name']] = files.ParsedFile(new_infos, path)
    return merged_files
