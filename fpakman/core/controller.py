from datetime import datetime, timedelta
from threading import Lock, Thread
from typing import List

import requests

from fpakman.core import flatpak
from fpakman.core.model import ApplicationStatus, FlatpakApplication, ApplicationData

__FLATHUB_URL__ = 'https://flathub.org'
__FLATHUB_API_URL__ = __FLATHUB_URL__ + '/api/v1'


class FlatpakAsyncDataLoader(Thread):

    def __init__(self, app: FlatpakApplication, http_session, attempts: int = 3):
        super(FlatpakAsyncDataLoader, self).__init__(daemon=True)
        self.app = app
        self.http_session = http_session
        self.attempts = attempts

    def run(self):

        self.app.status = ApplicationStatus.LOADING_DATA

        for _ in range(0, self.attempts):
            try:
                res = self.http_session.get('{}/apps/{}'.format(__FLATHUB_API_URL__, self.app.base_data.id), timeout=30)

                if res.status_code == 200:
                    data = res.json()

                    if not self.app.base_data.version:
                        self.app.base_data.version = data.get('version')

                    self.app.base_data.description = data.get('description', data.get('summary', None))
                    self.app.base_data.icon_url = data.get('iconMobileUrl', None)
                    self.app.base_data.latest_version = data.get('currentReleaseVersion', self.app.base_data.version)

                    if self.app.base_data.icon_url and self.app.base_data.icon_url.startswith('/'):
                        self.app.base_data.icon_url = __FLATHUB_URL__ + self.app.base_data.icon_url

                    self.app.status = ApplicationStatus.READY
                    break
                else:
                    print("Could not retrieve app data for id '{}'. Server response: {}".format(self.app.base_data.id, res.status_code))
            except:
                print("Could not retrieve app data for id '{}'. Timeout".format(self.app.base_data.id))
                return None


class FlatpakManager:

    def __init__(self, cache_expire: int = 60 * 60):
        self.cache_apps = {}
        self.cache_expire = cache_expire
        self.http_session = requests.Session()
        self.lock_db_read = Lock()
        self.lock_read = Lock()

    def load_full_database(self):

        self.lock_db_read.acquire()

        try:
            res = self.http_session.get(__FLATHUB_API_URL__ + '/apps', timeout=30)

            if res.status_code == 200:
                for app in res.json():
                    self.cache_apps[app['flatpakAppId']] = app
        finally:
            self.lock_db_read.release()

    def _request_app_data(self, app_id: str):

        try:
            res = self.http_session.get('{}/apps/{}'.format(__FLATHUB_API_URL__, app_id), timeout=30)

            if res.status_code == 200:
                return res.json()
            else:
                print("Could not retrieve app data for id '{}'. Server response: {}".format(app_id, res.status_code))
        except:
            print("Could not retrieve app data for id '{}'. Timeout".format(app_id))
            return None

    def _map_to_model(self, app: dict) -> FlatpakApplication:

        cached_model = self.cache_apps.get(app['id'])

        if not cached_model or (cached_model and cached_model.expires_at and cached_model.expires_at <= datetime.utcnow()):
            model = FlatpakApplication(arch=app.get('arch'),
                                       branch=app.get('branch'),
                                       origin=app.get('origin'),
                                       runtime=app.get('runtime'),
                                       ref=app.get('ref'),
                                       commit=app.get('commit'),
                                       base_data=ApplicationData(id=app.get('id'),
                                                                 name=app.get('name'),
                                                                 version=app.get('version'),
                                                                 latest_version=app.get('latest_version')))
            if not app['runtime']:
                FlatpakAsyncDataLoader(model, self.http_session).start()

            if self.cache_expire > 0:
                model.expires_at = datetime.utcnow() + timedelta(seconds=self.cache_expire)

            self.cache_apps[app['id']] = model
            return model

        if not cached_model.installed and cached_model.status == ApplicationStatus.READY and cached_model.is_incomplete():  # try to retrieve server data again
            FlatpakAsyncDataLoader(cached_model, self.http_session).start()

        return cached_model

    def search(self, word: str) -> List[FlatpakApplication]:

        res = []
        apps_found = flatpak.search(word)

        if apps_found:

            already_read = set()
            installed_apps = self.read_installed()

            if installed_apps:
                for app_found in apps_found:
                    for installed_app in installed_apps:
                        if app_found['id'] == installed_app.base_data.id:
                            res.append(installed_app)
                            already_read.add(app_found['id'])

            for app_found in apps_found:
                if app_found['id'] not in already_read:
                    res.append(self._map_to_model(app_found))

        return res

    def read_installed(self) -> List[FlatpakApplication]:

        self.lock_read.acquire()

        try:
            installed = flatpak.list_installed()

            if installed:
                installed.sort(key=lambda p: p['name'].lower())

                available_updates = flatpak.list_updates_as_str()

                models = []

                for app in installed:
                    model = self._map_to_model(app)
                    model.installed = True
                    model.update = app['id'] in available_updates
                    models.append(model)

                return models

            return []

        finally:
            self.lock_read.release()

    def downgrade_app(self, app: FlatpakApplication, root_password: str):

        commits = flatpak.get_app_commits(app.ref, app.origin)
        commit_idx = commits.index(app.commit)

        # downgrade is not possible if the app current commit in the first one:
        if commit_idx == len(commits) - 1:
            return None

        return flatpak.downgrade_and_stream(app.ref, commits[commit_idx + 1], root_password)
