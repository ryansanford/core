import os
import copy
import glob
import json
import logging
import pymongo
import datetime
import elasticsearch

from . import util

logging.basicConfig(
    format='%(asctime)s %(name)16.16s %(filename)24.24s %(lineno)5d:%(levelname)4.4s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.DEBUG,
)
log = logging.getLogger('scitran.api')

logging.getLogger('MARKDOWN').setLevel(logging.WARNING) # silence Markdown library
logging.getLogger('requests').setLevel(logging.WARNING) # silence Requests library
logging.getLogger('paste.httpserver').setLevel(logging.WARNING) # silence Paste library
logging.getLogger('elasticsearch').setLevel(logging.WARNING) # silence Elastic library


# NOTE: Keep in sync with environment variables in sample.config file.
DEFAULT_CONFIG = {
    'core': {
        'debug': False,
        'log_level': 'info',
        'access_log_enabled': False,
        'newrelic': None,
        'drone_secret': None,
    },
    'site': {
        'id': 'local',
        'name': 'Local',
        'api_url': 'https://localhost/api',
        'redirect_url': 'https://localhost',
        'central_url': 'https://sdmc.scitran.io/api',
        'registered': False,
        'ssl_cert': None
    },
    'queue': {
        'max_retries': 3,
        'retry_on_fail': False,
        'prefetch': False
    },
    'auth': {
        'google': {
            "id_endpoint" : "https://www.googleapis.com/plus/v1/people/me/openIdConnect",
            "client_id" : "979703271380-q85tbsupddmb7996q30244368r7e54lr.apps.googleusercontent.com",
            "token_endpoint" : "https://accounts.google.com/o/oauth2/token",
            "verify_endpoint" : "https://www.googleapis.com/oauth2/v1/tokeninfo",
            "refresh_endpoint" : "https://www.googleapis.com/oauth2/v4/token",
            "auth_endpoint" : "https://accounts.google.com/o/oauth2/auth"
        }
    },
    'persistent': {
        'db_uri':     'mongodb://localhost:27017/scitran',
        'db_log_uri': 'mongodb://localhost:27017/logs',
        'db_connect_timeout': '2000',
        'db_server_selection_timeout': '3000',
        'data_path': os.path.join(os.path.dirname(__file__), '../persistent/data'),
        'elasticsearch_host': 'localhost:9200',
    },
}

def apply_env_variables(config):
    # Overwrite default config values with SCITRAN env variables if available

    # Load auth config from file if available
    if 'SCITRAN_AUTH_CONFIG_FILE' in os.environ:
        auth_config = config['auth']
        file_path = os.environ['SCITRAN_AUTH_CONFIG_FILE']
        with open(file_path) as config_file:
            environ_config = json.load(config_file)
        auth_config.update(environ_config['auth'])
        config['auth'] = auth_config

    for outer_key, scoped_config in config.iteritems():
        if outer_key == 'auth':
            # Auth is loaded via file
            continue
        try:
            for inner_key in scoped_config:
                key = 'SCITRAN_' + outer_key.upper() + '_' + inner_key.upper()
                if key in os.environ:
                    value = os.environ[key]
                    if value.lower() == 'true':
                        value = True
                    elif value.lower() == 'false':
                        value = False
                    elif value.lower() == 'none':
                        value = None
                    config[outer_key][inner_key] = value
        except Exception: # pylint: disable=broad-except
            # ignore uniterable keys like `created` and `modified`
            pass
    return config

# Create config for startup, will be merged with db config when db is available
__config = apply_env_variables(copy.deepcopy(DEFAULT_CONFIG))
__config_persisted = False
__last_update = datetime.datetime.utcfromtimestamp(0)

if not os.path.exists(__config['persistent']['data_path']):
    os.makedirs(__config['persistent']['data_path'])

log.setLevel(getattr(logging, __config['core']['log_level'].upper()))

db = pymongo.MongoClient(
    __config['persistent']['db_uri'],
    j=True, # Requests only return once write has hit the DB journal
    connectTimeoutMS=__config['persistent']['db_connect_timeout'],
    serverSelectionTimeoutMS=__config['persistent']['db_server_selection_timeout'],
    connect=False, # Connect on first operation to avoid multi-threading related errors
).get_default_database()
log.debug(str(db))

log_db = pymongo.MongoClient(
    __config['persistent']['db_log_uri'],
    j=True, # Requests only return once write has hit the DB journal
    connectTimeoutMS=__config['persistent']['db_connect_timeout'],
    serverSelectionTimeoutMS=__config['persistent']['db_server_selection_timeout'],
    connect=False, # Connect on first operation to avoid multi-threading related errors
).get_default_database()
log.debug(str(log_db))

es = elasticsearch.Elasticsearch([__config['persistent']['elasticsearch_host']])

# validate the lists of json schemas
schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../raml/schemas')

expected_mongo_schemas = set([
    'acquisition.json',
    'analysis.json',
    'collection.json',
    'container.json',
    'file.json',
    'group.json',
    'note.json',
    'permission.json',
    'project.json',
    'session.json',
    'subject.json',
    'user.json',
    'avatars.json',
    'tag.json'
])
expected_input_schemas = set([
    'acquisition.json',
    'acquisition-update.json',
    'analysis.json',
    'analysis-job.json',
    'analysis-update.json',
    'avatars.json',
    'collection.json',
    'collection-update.json',
    'container.json',
    'device.json',
    'file.json',
    'file-update.json',
    'group-new.json',
    'group-update.json',
    'info_update.json',
    'note.json',
    'packfile.json',
    'permission.json',
    'project.json',
    'project-template.json',
    'project-update.json',
    'rule-add.json',
    'rule-update.json',
    'session.json',
    'session-update.json',
    'subject.json',
    'user-new.json',
    'user-update.json',
    'download.json',
    'tag.json',
    'enginemetadata.json',
    'labelupload.json',
    'uidupload.json',
    'uidmatchupload.json',
    'search.json'
])
mongo_schemas = set()
input_schemas = set()

# check that the lists of schemas are correct
for schema_filepath in glob.glob(schema_path + '/mongo/*.json'):
    schema_file = os.path.basename(schema_filepath)
    mongo_schemas.add(schema_file)
    with open(schema_filepath, 'rU') as f:
        pass

assert mongo_schemas == expected_mongo_schemas, '{} is different from {}'.format(mongo_schemas, expected_mongo_schemas)

for schema_filepath in glob.glob(schema_path + '/input/*.json'):
    schema_file = os.path.basename(schema_filepath)
    input_schemas.add(schema_file)
    with open(schema_filepath, 'rU') as f:
        pass

assert input_schemas == expected_input_schemas, '{} is different from {}'.format(input_schemas, expected_input_schemas)

def create_or_recreate_ttl_index(coll_name, index_name, ttl):
    if coll_name in db.collection_names():
        index_list = db[coll_name].index_information()
        if index_list:
            for index in index_list:
                # search for index by given name
                # example: "timestamp_1": {"key": [["timestamp", 1]], ...}
                if index_list[index]['key'][0][0] == index_name:
                    if index_list[index].get('expireAfterSeconds', None) != ttl:
                        # drop existing, recreate below
                        db[coll_name].drop_index(index)
                        break
                    else:
                        # index exists with proper ttl, bail
                        return
    db[coll_name].create_index(index_name, expireAfterSeconds=ttl)


def initialize_db():
    log.info('Initializing database, creating indexes')
    # TODO review all indexes
    db.users.create_index('api_key.key')
    db.projects.create_index([('gid', 1), ('name', 1)])
    db.sessions.create_index('project')
    db.sessions.create_index('uid')
    db.sessions.create_index('created')
    db.acquisitions.create_index('session')
    db.acquisitions.create_index('uid')
    db.acquisitions.create_index('collections')
    db.analyses.create_index([('parent.type', 1), ('parent.id', 1)])
    db.jobs.create_index([('inputs.id', 1), ('inputs.type', 1)])
    db.jobs.create_index([('state', 1), ('now', 1), ('modified', 1)])
    db.gears.create_index('name')
    db.batch.create_index('jobs')
    db.project_rules.create_index('project_id')

    if __config['core']['access_log_enabled']:
        log_db.access_log.create_index('context.ticket_id')
        log_db.access_log.create_index([('timestamp', pymongo.DESCENDING)])

    create_or_recreate_ttl_index('authtokens', 'timestamp', 2592000)
    create_or_recreate_ttl_index('uploads', 'timestamp', 60)
    create_or_recreate_ttl_index('downloads', 'timestamp', 60)

    now = datetime.datetime.utcnow()
    db.groups.update_one({'_id': 'unknown'}, {'$setOnInsert': { 'created': now, 'modified': now, 'label': 'Unknown', 'permissions': []}}, upsert=True)

def get_config():
    global __last_update, __config, __config_persisted #pylint: disable=global-statement
    now = datetime.datetime.utcnow()
    if not __config_persisted:
        initialize_db()
        log.info('Persisting configuration')

        db_config = db.singletons.find_one({'_id': 'config'})
        if db_config is not None:
            startup_config = copy.deepcopy(__config)
            startup_config = util.deep_update(startup_config, db_config)
            # Precedence order for config is env vars -> db values -> default
            __config = apply_env_variables(startup_config)
        else:
            __config['created'] = now
        __config['modified'] = now

        db.singletons.replace_one({'_id': 'config'}, __config, upsert=True)
        __config_persisted = True
        __last_update = now
    elif now - __last_update > datetime.timedelta(seconds=120):
        log.debug('Refreshing configuration from database')
        __config = db.singletons.find_one({'_id': 'config'})
        __last_update = now
        log.setLevel(getattr(logging, __config['core']['log_level'].upper()))
    return __config

def get_public_config():
    auth = copy.deepcopy(__config.get('auth'))
    for values in auth.itervalues():
        values.pop('client_secret', None)
    return {
        'created': __config.get('created'),
        'modified': __config.get('modified'),
        'site': __config.get('site'),
        'auth': auth,
    }

def get_version():
    return db.singletons.find_one({'_id': 'version'})

def get_item(outer, inner):
    return get_config()[outer][inner]

def mongo_pipeline(table, pipeline):
    """
    Temporary philosophical dupe with reporthandler.py.
    Execute a mongo pipeline, check status, return results.
    A workaround for wonky pymongo aggregation behavior.
    """

    output = db.command('aggregate', table, pipeline=pipeline)
    result = output.get('result')

    if output.get('ok') != 1.0 or result is None:
        raise Exception()

    return result

def get_auth(auth_type):
    return get_config()['auth'][auth_type]
