"""
Module defining RefererHandler and it's subclasses. RefererHandler
generalizes the handling of documents that are not part of the container
hierarchy, are always associated with (referencing) a parent container,
and are stored in their own collection instead of an embedded list on the
container (eg. ListHandler)
"""

import os
from abc import ABCMeta, abstractproperty

from .. import config
from .. import upload
from .. import util
from .. import validators
from ..auth import containerauth, always_ok
from ..dao import APIStorageException, containerstorage, noop
from ..web import base
from ..web.request import log_access, AccessType


log = config.log


class RefererHandler(base.RequestHandler):
    __metaclass__ = ABCMeta

    storage = abstractproperty()
    payload_schema_file = abstractproperty()
    permchecker = containerauth.default_referer

    @property
    def input_validator(self):
        input_schema_uri = validators.schema_uri('input', self.payload_schema_file)
        input_validator = validators.from_schema_path(input_schema_uri)
        return input_validator

    def get_permchecker(self, parent_container):
        if self.superuser_request:
            return always_ok
        elif self.public_request:
            return containerauth.public_request(self, container=parent_container)
        else:
            # NOTE The handler (self) is passed implicitly
            return self.permchecker(parent_container=parent_container)


class AnalysesHandler(RefererHandler):
    storage = containerstorage.AnalysisStorage()
    payload_schema_file = 'analysis.json'


    def post(self, cont_name, cid):
        """
        Default behavior:
            Creates an analysis object and uploads supplied input
            and output files.
        When param ``job`` is true:
            Creates an analysis object and job object that reference
            each other via ``job`` and ``destination`` fields. Job based
            analyses are only allowed at the session level.
        """
        parent = self.storage.get_parent(cont_name, cid)
        permchecker = self.get_permchecker(parent)
        permchecker(noop)('POST')

        if self.is_true('job'):
            if cont_name != 'sessions':
                self.abort(400, 'Analysis created via a job must be at the session level')

            payload = self.request.json_body
            analysis = payload.get('analysis')
            job = payload.get('job')
            if not analysis or not job:
                self.abort(400, 'JSON body must contain map for "analysis" and "job"')
            self.input_validator(analysis, 'POST')
            result = self.storage.create_job_and_analysis(cont_name, cid, analysis, job, self.origin)
            return {'_id': result['analysis']['_id']}

        analysis = upload.process_upload(self.request, upload.Strategy.analysis, origin=self.origin)
        self.storage.fill_values(analysis, cont_name, cid, self.origin)
        result = self.storage.create_el(analysis)

        if result.acknowledged:
            return {'_id': result.inserted_id}
        else:
            self.abort(500, 'Analysis not added for container {} {}'.format(cont_name, cid))


    def get(self, cont_name, cid, _id):
        parent = self.storage.get_parent(cont_name, cid)
        permchecker = self.get_permchecker(parent)
        permchecker(noop)('GET')
        return self.storage.get_container(_id)


    @log_access(AccessType.delete_analysis)
    def delete(self, cont_name, cid, _id):
        parent = self.storage.get_parent(cont_name, cid)
        permchecker = self.get_permchecker(parent)
        permchecker(noop)('DELETE')
        self.log_user_access(AccessType.delete_file, cont_name=cont_name, cont_id=cid)

        try:
            result = self.storage.delete_el(_id)
        except APIStorageException as e:
            self.abort(400, e.message)
        if result.deleted_count == 1:
            return {'deleted': result.deleted_count}
        else:
            self.abort(404, 'Analysis {} not removed from container {} {}'.format(_id, cont_name, cid))

    def _check_ticket(self, ticket_id, _id, filename):
        ticket = config.db.downloads.find_one({'_id': ticket_id})
        if not ticket:
            self.abort(404, 'no such ticket')
        if ticket['ip'] != self.request.client_addr:
            self.abort(400, 'ticket not for this source IP')
        if not filename:
            return self._check_ticket_for_batch(ticket)
        if ticket.get('filename') != filename or ticket['target'] != _id:
            self.abort(400, 'ticket not for this resource')
        return ticket


    def _check_ticket_for_batch(self, ticket):
        if ticket.get('type') != 'batch':
            self.abort(400, 'ticket not for this resource')
        return ticket


    def _prepare_batch(self, fileinfo):
        ## duplicated code from download.py
        ## we need a way to avoid this
        targets = []
        total_size = total_cnt = 0
        data_path = config.get_item('persistent', 'data_path')
        for f in fileinfo:
            filepath = os.path.join(data_path, util.path_from_hash(f['hash']))
            if os.path.exists(filepath): # silently skip missing files
                targets.append((filepath, 'analyses/' + f['name'], f['size']))
                total_size += f['size']
                total_cnt += 1
        return targets, total_size, total_cnt


    def _send_batch(self, ticket):
        self.abort(400, 'This endpoint does not download files, only returns ticket {} for the download'.format(ticket))
