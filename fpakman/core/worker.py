import time
import traceback
from datetime import datetime, timedelta
from io import StringIO
from threading import Thread
from typing import List

import requests
from colorama import Fore

from fpakman.core.constants import FLATHUB_API_URL, FLATHUB_URL
from fpakman.core.model import FlatpakApplication, ApplicationStatus


class FlatpakAsyncDataLoader(Thread):

    def __init__(self, http_session, api_cache: dict, cache_expiration: int, attempts: int = 3):
        super(FlatpakAsyncDataLoader, self).__init__(daemon=True)
        self.apps = []
        self.http_session = http_session
        self.attempts = attempts
        self.api_cache = api_cache
        self.cache_expiration = cache_expiration
        self.id_ = '{}#{}'.format(self.__class__.__name__, id(self))
        self.stop = False

    def log_msg(self, msg: str, color: int = None):
        final_msg = StringIO()

        if color:
            final_msg.write(str(color))

        final_msg.write('[{}] '.format(self.id_))

        final_msg.write(msg)

        if color:
            final_msg.write(Fore.RESET)

        final_msg.seek(0)

        print(final_msg.read())

    def run(self):
        while True:
            if not self.apps and self.stop:
                break  # stop working
            else:
                if self.apps:
                    app = self.apps[0]
                    app.status = ApplicationStatus.LOADING_DATA

                    for _ in range(0, self.attempts):
                        try:
                            res = self.http_session.get('{}/apps/{}'.format(FLATHUB_API_URL, app.base_data.id), timeout=30)

                            if res.status_code == 200 and res.text:
                                data = res.json()

                                if not app.base_data.version:
                                    app.base_data.version = data.get('version')

                                app.base_data.description = data.get('description', data.get('summary', None))
                                app.base_data.icon_url = data.get('iconMobileUrl', None)
                                app.base_data.latest_version = data.get('currentReleaseVersion', app.base_data.version)

                                if app.base_data.icon_url and app.base_data.icon_url.startswith('/'):
                                    app.base_data.icon_url = FLATHUB_URL + app.base_data.icon_url

                                if self.cache_expiration > 0:
                                    self.api_cache[app.base_data.id] = {
                                        'description': app.base_data.description,
                                        'icon_url': app.base_data.icon_url,
                                        'latest_version': app.base_data.latest_version,
                                        'version': app.base_data.version,
                                        'expires_at': datetime.utcnow() + timedelta(seconds=self.cache_expiration)
                                    }

                                app.status = ApplicationStatus.READY
                                break
                            else:
                                self.log_msg("Could not retrieve app data for id '{}'. Server response: {}. Body: {}".format(app.base_data.id, res.status_code, res.content.decode()), Fore.RED)
                        except:
                            self.log_msg("Could not retrieve app data for id '{}'".format(app.base_data.id), Fore.YELLOW)
                            traceback.print_exc()

                    del self.apps[0]

    def add(self, app: FlatpakApplication):
        self.apps.append(app)

    def current_load(self):
        return len(self.apps)


class FlatpakAsyncDataLoaderManager:

    def __init__(self, api_cache: dict, cache_expiration: int, worker_load: int = 3, workers: List[FlatpakAsyncDataLoader] = []):
        self.worker_load = worker_load
        self.current_workers = workers
        self.http_session = requests.Session()
        self.api_cache = api_cache
        self.cache_expiration = cache_expiration

    def load(self, app: FlatpakApplication):

            available_workers = [w for w in self.current_workers if w.current_load() < self.worker_load]

            if available_workers:
                worker = available_workers[0]
            else:  # new worker
                worker = FlatpakAsyncDataLoader(http_session=self.http_session,
                                                api_cache=self.api_cache,
                                                cache_expiration=self.cache_expiration)
                worker.start()
                self.current_workers.append(worker)

            worker.add(app)

    def stop_current_workers(self):

        for w in self.current_workers:
            w.stop = True

        self.current_workers = []
