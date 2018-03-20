# coding: utf-8
from __future__ import absolute_import
from __future__ import unicode_literals

import atexit
import io
import json
import operator
import threading
import time

import requests
import six

from ._utils.libs import PIL, piexif, json
from ._utils.contexts import on_exception


class InstaDownloader(threading.Thread):

    def __init__(self,
                 queue,
                 destination,
                 namegen,
                 add_metadata=False,
                 dump_json=False,
                 dump_only=False,
                 pbar=None,
                 session=None):

        super(InstaDownloader, self).__init__()

        self.queue = queue
        self.destination = destination
        self.namegen = namegen
        self.session = session or requests.Session()
        self.pbar = pbar

        self.dump_only = dump_only
        self.dump_json = dump_json or dump_only
        self.add_metadata = add_metadata

        self._killed = False
        self._downloading = None

        self._DOWNLOAD_METHODS = {
            "GraphImage": self._download_image,
            "GraphVideo": self._download_video,
            "GraphSidecar": self._download_sidecar,
        }

    def _download_image(self, media):
        url = media['display_url']
        filename = self.namegen.file(media)

        if self.destination.exists(filename):
            return

        # FIXME: find a way to remove failed temporary downloads
        # with on_exception(self.destination.remove, filename):
        with self.destination.open(filename, "wb") as f:
            with self.session.get(url) as res:
                f.write(res.content)

        self._set_time(media, filename)

    def _download_video(self, media):
        url = media['video_url']
        filename = self.namegen.file(media)

        if self.destination.exists(filename):
            return

        # FIXME: find a way to remove failed temporary downloads
        with on_exception(self.destination.remove, filename):
            with self.destination.open(filename, "wb") as f:
                with self.session.get(url) as res:
                    for chunk in res.iter_content(io.DEFAULT_BUFFER_SIZE):
                        f.write(chunk)

        self._set_time(media, filename)

    def _download_sidecar(self, media):
        edges = media.pop('edge_sidecar_to_children')['edges']
        for edge in six.moves.map(operator.itemgetter('node'), edges):
            for key, value in six.iteritems(media):
                edge.setdefault(key, value)
            self._DOWNLOAD_METHODS[edge['__typename']](edge)

    def _set_time(self, media, filename):
        details = {}
        details["modified"] = details["accessed"] = details["created"] = \
            media.get('taken_at_timestamp') or media['date']
        self.destination.setinfo(filename, {"details": details})

    def _dump(self, media):
        basename = self.namegen.base(media)
        filename = "{}.json".format(basename)

        with self.destination.open(filename, "w" if six.PY3 else "wb") as f:
            json.dump(media, f, indent=4, sort_keys=True)

    def run(self):
        while not self._killed:
            try:
                media = self.queue.get_nowait()

                # Received a poison pill: break the loop
                if media is None:
                    self._killed = True

                else:
                    # Download media
                    if not self.dump_only:
                        self._DOWNLOAD_METHODS[media["__typename"]](media)
                    # Dump JSON metadata if needed
                    if self.dump_json:
                        self._dump(media)
                    # Update progress bar if any
                    if self.pbar is not None and not self._killed:
                        with self.pbar.lock():
                            self.pbar.update()

                self.queue.task_done()

            except six.moves.queue.Empty:
                time.sleep(1)

    def terminate(self):
        self._killed = True
