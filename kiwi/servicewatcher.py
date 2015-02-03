import json
import logging
import requests
import time
from multiprocessing import Process
from itertools import izip

import defaults
from utils import iter_lines


LOG = logging.getLogger(__name__)


def iter_request_events(fd):
    '''Iterate over the events from a Kubernetes event stream.'''

    lines = iter_lines(fd)
    for expected_len, data, marker in izip(lines, lines, lines):
        expected_len = int(expected_len, base=16)
        actual_len = len(data)
        if expected_len != actual_len + 1:
            raise ValueError('data length mismatch (expected %d, have %d)',
                             expected_len,
                             actual_len)

        yield json.loads(data)


def iter_events(url, interval=1):
    '''Generates an infinite string of Kubernetes events'''

    while True:
        try:
            r = requests.get(url, stream=True)
            r.raise_for_status()
            for event in iter_request_events(r.raw):
                yield event
        except Exception as exc:
            LOG.error('connection failed: %s' % exc)
            time.sleep(interval)


class ServiceWatcher (Process):
    '''Watches the Kubernetes API for changes to services, and pushes
    events onto the message queue.'''

    def __init__(self,
                 queue,
                 reconnect_interval=defaults.reconnect_interval,
                 kube_endpoint=defaults.kube_endpoint):
        super(ServiceWatcher, self).__init__()

        self.q = queue
        self.kube_api = '%s/api/v1beta1' % kube_endpoint
        self.reconnect_interval = reconnect_interval

    def run(self):
        url = '%s/watch/services' % self.kube_api

        for event in iter_events(url, interval=self.reconnect_interval):
            service = event['object']
            LOG.debug('received %s for %s',
                      event['type'],
                      service['id'])

            handler = getattr(self,
                              'handle_%s' % event['type'].lower())

            # we log missing handlers at debug level because we probably
            # intentionally have not written a handler for the event.
            if not handler:
                LOG.debug('unknown event: %(type)s' % event)
                continue

            handler(service)

    def handle_added(self, service):
        self.q.put({'message': 'add-service',
                    'target': service['id'],
                    'service': service})

    def handle_deleted(self, service):
        self.q.put({'message': 'delete-service',
                    'target': service['id'],
                    'service': service})

    def handle_modified(self, service):
        self.q.put({'message': 'update-service',
                    'target': service['id'],
                    'service': service})

if __name__ == '__main__':
    from multiprocessing import Queue
    import pprint

    logging.basicConfig(level=logging.DEBUG)
    q = Queue()
    s = ServiceWatcher(q)
    s.start()

    while True:
        msg = q.get()
        pprint.pprint(msg)
