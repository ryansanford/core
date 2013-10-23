#!/usr/bin/env python
#
# @author:  Gunnar Schaefer

import os
import json
import uuid
import hashlib
import logging
import pymongo
import tarfile
import webapp2
import zipfile
import argparse
import bson.json_util

import nimsutil

import epochs
import sessions
import experiments
import nimsapiutil

log = logging.getLogger('nimsapi')


class NIMSAPI(nimsapiutil.NIMSRequestHandler):

    def get(self):
        self.response.write('nimsapi\n')

    def upload(self, filename):
        hash_ = hashlib.md5()
        stage_path = self.app.config['stage_path']
        with nimsutil.TempDir(prefix='.tmp', dir=stage_path) as tempdir_path:
            upload_filepath = os.path.join(tempdir_path, filename)
            log.info(os.path.basename(upload_filepath))
            with open(upload_filepath, 'wb') as upload_file:
                for chunk in iter(lambda: self.request.body_file.read(2**20), ''):
                    hash_.update(chunk)
                    upload_file.write(chunk)
            if hash_.hexdigest() != self.request.get('md5'):
                self.abort(406)
            if not tarfile.is_tarfile(upload_filepath) and not zipfile.is_zipfile(upload_filepath):
                self.abort(415)
            os.rename(upload_filepath, os.path.join(stage_path, str(uuid.uuid1()) + '_' + filename)) # add UUID to prevent clobbering files

    def download(self):
        paths = []
        symlinks = []
        for js_id in self.request.get('id', allow_multiple=True):
            type_, _id = js_id.split('_')
            _idpaths, _idsymlinks = resource_types[type_].download_info(_id)
            paths += _idpaths
            symlinks += _idsymlinks

    def dump(self):
        self.response.write(json.dumps(list(self.app.db.sessions.find()), default=bson.json_util.default))


class Users(nimsapiutil.NIMSRequestHandler):

    def count(self):
        """Return the number of Users."""
        self.response.write('%d users\n' % self.app.db.users.count())

    def post(self):
        """Create a new User"""
        self.response.write('users post\n')

    def get(self):
        """Return the list of Users."""
        projection = ['firstname', 'lastname', 'email_hash']
        users = list(self.app.db.users.find({}, projection))
        self.response.write(json.dumps(users, default=bson.json_util.default))

    def put(self):
        """Update many Users."""
        self.response.write('users put\n')


class User(nimsapiutil.NIMSRequestHandler):

    def get(self, uid):
        """Return User details."""
        user = self.app.db.users.find_one({'_id': uid})
        self.response.write(json.dumps(user, default=bson.json_util.default))

    def put(self, uid):
        """Update an existing User."""
        user = self.app.db.users.find_one({'_id': uid})
        if not user:
            self.abort(404)
        if uid == self.userid or self.user_is_superuser: # users can only update their own info
            updates = {'$set': {}, '$unset': {}}
            for k, v in self.request.params.iteritems():
                if k != 'superuser' and k in user_fields:
                    updates['$set'][k] = v # FIXME: do appropriate type conversion
                elif k == 'superuser' and uid == self.userid and self.user_is_superuser is not None: # toggle superuser for requesting user
                    updates['$set'][k] = v.lower() in ('1', 'true')
                elif k == 'superuser' and uid != self.userid and self.user_is_superuser:             # enable/disable superuser for other user
                    if v.lower() in ('1', 'true') and user.get('superuser') is None:
                        updates['$set'][k] = False # superuser is tri-state: False indicates granted, but disabled, superuser privileges
                    elif v.lower() not in ('1', 'true'):
                        updates['$unset'][k] = ''
            user = self.app.db.users.find_and_modify({'_id': uid}, updates, new=True)
        else:
            self.abort(403)
        self.response.write(json.dumps(user, default=bson.json_util.default) + '\n')

    def delete(self, uid):
        """Delete an User."""
        self.response.write('user %s delete, %s\n' % (uid, self.request.params))


class Groups(nimsapiutil.NIMSRequestHandler):

    def count(self):
        """Return the number of Groups."""
        self.response.write('%d groups\n' % self.app.db.groups.count())

    def post(self):
        """Create a new Group"""
        self.response.write('groups post\n')

    def get(self):
        """Return the list of Groups."""
        projection = ['_id']
        groups = list(self.app.db.groups.find({}, projection))
        self.response.write(json.dumps(groups, default=bson.json_util.default))

    def put(self):
        """Update many Groups."""
        self.response.write('groups put\n')


class Group(nimsapiutil.NIMSRequestHandler):

    def get(self, gid):
        """Return Group details."""
        group = self.app.db.groups.find_one({'_id': gid})
        self.response.write(json.dumps(group, default=bson.json_util.default))

    def put(self, gid):
        """Update an existing Group."""
        self.response.write('group %s put, %s\n' % (gid, self.request.params))

    def delete(self, gid):
        """Delete an Group."""


class ArgumentParser(argparse.ArgumentParser):

    def __init__(self):
        super(ArgumentParser, self).__init__()
        self.add_argument('uri', help='NIMS DB URI')
        self.add_argument('stage_path', help='path to staging area')
        self.add_argument('-f', '--logfile', help='path to log file')
        self.add_argument('-l', '--loglevel', default='info', help='path to log file')
        self.add_argument('-q', '--quiet', action='store_true', default=False, help='disable console logging')


routes = [
        webapp2.Route(r'/nimsapi',                                          NIMSAPI),
        webapp2.Route(r'/nimsapi/upload/<:.+>',                             NIMSAPI, handler_method='upload', methods=['PUT']),
        webapp2.Route(r'/nimsapi/download',                                 NIMSAPI, handler_method='download', methods=['GET']),
        webapp2.Route(r'/nimsapi/dump',                                     NIMSAPI, handler_method='dump', methods=['GET']),
        webapp2.Route(r'/nimsapi/users',                                    Users),
        webapp2.Route(r'/nimsapi/users/count',                              Users, handler_method='count', methods=['GET']),
        webapp2.Route(r'/nimsapi/users/<:.+>',                              User),
        webapp2.Route(r'/nimsapi/groups',                                   Groups),
        webapp2.Route(r'/nimsapi/groups/count',                             Groups, handler_method='count', methods=['GET']),
        webapp2.Route(r'/nimsapi/groups/<:.+>',                             Group),
        webapp2.Route(r'/nimsapi/experiments',                              experiments.Experiments),
        webapp2.Route(r'/nimsapi/experiments/count',                        experiments.Experiments, handler_method='count', methods=['GET']),
        webapp2.Route(r'/nimsapi/experiments/<:[0-9a-f]+>',                 experiments.Experiment),
        webapp2.Route(r'/nimsapi/experiments/<:[0-9a-f]+>/sessions',        sessions.Sessions),
        webapp2.Route(r'/nimsapi/sessions/count',                           sessions.Sessions, handler_method='count', methods=['GET']),
        webapp2.Route(r'/nimsapi/sessions/<:[0-9a-f]+>',                    sessions.Session),
        webapp2.Route(r'/nimsapi/sessions/<:[0-9a-f]+>/move',               sessions.Session, handler_method='move'),
        webapp2.Route(r'/nimsapi/sessions/<:[0-9a-f]+>/epochs',             epochs.Epochs),
        webapp2.Route(r'/nimsapi/epochs/count',                             epochs.Epochs, handler_method='count', methods=['GET']),
        webapp2.Route(r'/nimsapi/epochs/<:[0-9a-f]+>',                      epochs.Epoch),
        ]


if __name__ == '__main__':
    args = ArgumentParser().parse_args()
    nimsutil.configure_log(args.logfile, not args.quiet, args.loglevel)

    from paste import httpserver
    app = webapp2.WSGIApplication(routes, debug=True, config=dict(stage_path=args.stage_path))
    app.db = (pymongo.MongoReplicaSetClient(args.uri) if 'replicaSet' in args.uri else pymongo.MongoClient(args.uri)).get_default_database()
    httpserver.serve(app, host=httpserver.socket.gethostname(), port='8080')


#API = NIMSAPI
#APIResource = experiments.Experiments
#routes = [
#        webapp2.Route(r'/', API),
#        webapp2.Route(r'/resource', APIResource),
#        ]
#
#from webapp2_extras.routes import PathPrefixRoute
#
#if __name__ == '__main__':
#    from paste import httpserver
#    nimsapi = webapp2.WSGIApplication([PathPrefixRoute('/', routes)], debug=True)
#    httpserver.serve(nimsapi, host='127.0.0.1', port='8080')
#else:
#    from webapp2_extras.routes import PathPrefixRoute
#    nimsapi = webapp2.WSGIApplication([PathPrefixRoute('/nimsapi', routes)], debug=True)


# /experiments                  experiment info for all experiments
# /experiments/ID/sessions      experiment info with embedded sessions for one experiment
# /experiments/ID/epochs        experiment info with embedded sessions and embedded epochs for one experiment
# /sessions/ID/epochs           experiment info with embedded sessions and embedded epochs for one session

# /sessions                     experiment info with embedded sessions for all experiments
# /epochs                       experiment info with embedded sessions and embedded epochs for all sessions

# /experiments/ID               experiment details for one experiment
# /sessions/ID                  sessions details for one session
# /epochs/ID                    epoch details for one epoch
