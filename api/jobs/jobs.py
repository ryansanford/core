"""
Jobs
"""

import bson
import copy
import datetime

from ..types import Origin
from ..dao.containerutil import create_filereference_from_dictionary, create_containerreference_from_dictionary, create_containerreference_from_filereference

from .. import config


class Job(object):
    def __init__(self, name, inputs, destination=None, tags=None, attempt=1, previous_job_id=None, created=None, modified=None, state='pending', request=None, id_=None, config_=None, now=False, origin=None, saved_files=None):
        """
        Creates a job.

        Parameters
        ----------
        name: string
            Unique name of the algorithm
        inputs: string -> FileReference map
            The inputs to be used by this job
        destination: ContainerReference (optional)
            Where to place the gear's output. Defaults to one of the input's containers.
        tags: string array (optional)
            Tags that this job should be marked with.
        attempt: integer (optional)
            If an equivalent job has tried & failed before, pass which attempt number we're at. Defaults to 1 (no previous attempts).
        previous_job_id: string (optional)
            If an equivalent job has tried & failed before, pass the last job attempt. Defaults to None (no previous attempts).
        created: datetime (optional)
        modified: datetime (optional)
            Timestamps
        state: string (optional)
            The state of this job. Defaults to 'pending'.
        request: map (optional)
            The request that is used for the engine. Generated when job is started.
        id_: string (optional)
            The database identifier for this job.
        config: map (optional)
            The gear configuration for this job.
        """

        # TODO: validate inputs against the manifest

        time_now = datetime.datetime.utcnow()

        if tags is None:
            tags = []
        if saved_files is None:
            saved_files = []
        if created is None:
            created = time_now
        if modified is None:
            modified = time_now

        if destination is None and inputs is not None:
            # Grab an arbitrary input's container
            key = inputs.keys()[0]
            destination = create_containerreference_from_filereference(inputs[key])

        # A job is always tagged with the name of the gear
        tags.append(name)

        # Trim tags array to unique members...
        tags = list(set(tags))

        # If no origin, mark as system origin
        if origin is None:
            origin = {
                'type': str(Origin.system),
                'id': None
            }


        self.name            = name
        self.inputs          = inputs
        self.destination     = destination
        self.tags            = tags
        self.attempt         = attempt
        self.previous_job_id = previous_job_id
        self.created         = created
        self.modified        = modified
        self.state           = state
        self.request         = request
        self.id_             = id_
        self.config          = config_
        self.now             = now
        self.origin          = origin
        self.saved_files     = saved_files

    @classmethod
    def load(cls, e):
        # TODO: validate

        # Don't modify the map
        d = copy.deepcopy(e)

        if d.get('inputs'):
            input_dict = {}

            for i in d['inputs']:
                inp = i.pop('input')
                input_dict[inp] = create_filereference_from_dictionary(i)

            d['inputs'] = input_dict

        if d.get('destination', None):
            d['destination'] = create_containerreference_from_dictionary(d['destination'])

        d['_id'] = str(d['_id'])

        return cls(d['name'], d.get('inputs'),
            destination=d.get('destination'),
            tags=d['tags'], attempt=d['attempt'],
            previous_job_id=d.get('previous_job_id'),
            created=d['created'],
            modified=d['modified'],
            state=d['state'],
            request=d.get('request'),
            id_=d['_id'],
            config_=d.get('config'),
            now=d.get('now', False),
            origin=d.get('origin'),
            saved_files=d.get('saved_files'))

    @classmethod
    def get(cls, _id):
        doc = config.db.jobs.find_one({'_id': bson.ObjectId(_id)})
        if doc is None:
            raise Exception('Job not found')

        return cls.load(doc)

    def map(self):
        """
        Flatten struct to map
        """

        # Don't modify the job obj
        d = copy.deepcopy(self.__dict__)

        d['id'] = d.pop('id_', None)

        if d.get('inputs'):
            for x in d['inputs'].keys():
                d['inputs'][x] = d['inputs'][x].__dict__
        else:
            d.pop('inputs')

        if d.get('destination'):
            d['destination'] = d['destination'].__dict__
        else:
            d.pop('destination')

        if d['id'] is None:
            d.pop('id')
        if d['previous_job_id'] is None:
            d.pop('previous_job_id')
        if d['request'] is None:
            d.pop('request')
        if d['now'] is False:
            d.pop('now')

        return d

    def mongo(self):
        d = self.map()
        if d.get('id'):
            d['id'] = bson.ObjectId(d['id'])
        if d.get('inputs'):
            input_array = []
            for k, inp in d['inputs'].iteritems():
                inp['input'] = k
                input_array.append(inp)
            d['inputs'] = input_array

        return d

    def insert(self):
        if self.id_ is not None:
            raise Exception('Cannot insert job that has already been inserted')

        result = config.db.jobs.insert_one(self.mongo())
        return result.inserted_id

    def save(self):
        self.modified = datetime.datetime.utcnow()
        update = self.mongo()
        job_id = update.pop('id')
        result = config.db.jobs.replace_one({'_id': job_id}, update)
        if result.modified_count != 1:
            raise Exception('Job modification not saved')
        return {'modified_count': 1}

    def generate_request(self, gear):
        """
        Generate the job's request, save it to the class, and return it

        Parameters
        ----------
        gear: map
            A gear_list map from the gears table.
        """

        r = {
            'inputs': [
                {
                    'type': 'http',
                    'uri': gear['exchange']['rootfs-url'],
                    'vu': 'vu0:x-' + gear['exchange']['rootfs-hash']
                }
            ],
            'target': {
                'command': ['bash', '-c', 'rm -rf output; mkdir -p output; ./run; echo "Exit was $?"'],
                'env': {
                    'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
                },
                'dir': "/flywheel/v0",
            },
            'outputs': [
                {
                    'type': 'scitran',
                    'uri': '',
                    'location': '/flywheel/v0/output',
                },
            ],
        }

        # Map destination to upload URI
        r['outputs'][0]['uri'] = '/engine?level=' + self.destination.type + '&id=' + self.destination.id

        # Add config, if any
        if self.config is not None:

            if self.id_ is None:
                raise Exception('Running a job requires an ID')

            r['inputs'].append({
                'type': 'scitran',
                'uri': '/jobs/' + self.id_ + '/config.json',
                'location': '/flywheel/v0',
            })

        # Add the files
        for input_name in self.inputs.keys():
            i = self.inputs[input_name]

            r['inputs'].append({
                'type': 'scitran',
                'uri': i.file_uri(i.name),
                'location': '/flywheel/v0/input/' + input_name,
            })

        # Log job origin if provided
        if self.id_:
            r['outputs'][0]['uri'] += '&job=' + self.id_

        self.request = r
        return self.request
