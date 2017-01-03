import atexit
import json
import os
import sys
import traceback
import webapp2

from ..api import endpoints
from .. import config
from . import encoder
from .. import util
from .request import SciTranRequest

try:
    import uwsgi
except ImportError:
    uwsgi = None

log = config.log

def dispatcher(router, request, response):
    try:
        if uwsgi is not None:
            uwsgi.set_logvar('request_id', request.id)
    except: # pylint: disable=bare-except
        request.logger.error("Error setting request_id log var", exc_info=True)

    try:
        rv = router.default_dispatcher(request, response)
        if rv is not None:
            response.write(json.dumps(rv, default=encoder.custom_json_serializer))
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
    except webapp2.HTTPException as e:
        util.send_json_http_exception(response, str(e), e.code)
    except Exception as e: # pylint: disable=broad-except
        request.logger.error("Error dispatching request", exc_info=True)
        if config.get_item('core', 'debug'):
            message = traceback.format_exc()
        else:
            message = 'Internal Server Error'
        util.send_json_http_exception(response, message, 500)

def app_factory(*_, **__):
    # pylint: disable=protected-access,unused-argument

    # don't use config.get_item() as we don't want to require the database at startup
    application = webapp2.WSGIApplication(endpoints, debug=config.__config['core']['debug'])
    application.router.set_dispatcher(dispatcher)
    application.request_class = SciTranRequest
    if os.environ.get("SCITRAN_RUNTIME_COVERAGE") == "true":
        start_coverage()
    # configure new relic
    if config.__config['core']['newrelic']:
        try:
            import newrelic.agent, newrelic.api.exceptions
            newrelic.agent.initialize(config.__config['core']['newrelic'])
            application = newrelic.agent.WSGIApplicationWrapper(application)
            log.info('New Relic detected and loaded. Monitoring enabled.')
        except ImportError:
            log.critical('New Relic libraries not found.')
            sys.exit(1)
        except newrelic.api.exceptions.ConfigurationError:
            log.critical('New Relic detected, but configuration invalid.')
            sys.exit(1)

    return application

# Functions to enable code coverage when API is started for testing
def start_coverage():
    import coverage
    config.log.info("Enabling code coverage")
    cov = coverage.coverage(source=["api"], data_suffix="integration-tests")
    cov.start()
    atexit.register(save_coverage, cov)

def save_coverage(cov):
    config.log.info("Saving coverage")
    cov.stop()
    cov.save()