#!/usr/bin/python
from __future__ import print_function

import os
import logging
import traceback
import threading
import time
from launchpadlib.launchpad import Launchpad


class PackagesetScanner(threading.Thread):
    notices = list()
    log = logging.getLogger(__name__)
    series_statuses = []

    def run(self):
        scan_start = time.time()
        self.log.info("Packageset[%s] scan started" % self.queue)
        try:
            # Login to Launchpad (authenticated if credentials exist, otherwise anonymous)
            credentials_file = os.path.expanduser("~/.secret/lp.txt")
            self.lp = None
            if os.path.exists(credentials_file):
                try:
                    self.log.info("Launchpad login: attempting authenticated login from %s" % credentials_file)
                    self.lp = Launchpad.login_with(
                        'maubot-queuebot', 'production',
                        credentials_file=credentials_file,
                        launchpadlib_dir="/tmp/queuebot-%s/" % self.queue)
                    self.log.info("Launchpad login: authenticated login succeeded")
                except Exception as e:
                    self.log.warning("Launchpad login: authenticated login failed (%s), falling back to anonymous" % e)
                    self.lp = None
            if self.lp is None:
                self.log.info("Launchpad login: using anonymous access")
                self.lp = Launchpad.login_anonymously(
                    'maubot-queuebot', 'production',
                    launchpadlib_dir="/tmp/queuebot-%s/" % self.queue)

            self.notices = list()

            ubuntu = self.lp.distributions['ubuntu']
            all_statuses = self.series_statuses
            ubuntu_series = [series for series in ubuntu.series
                             if series.active and (not all_statuses or series.status in all_statuses)]
            self.log.info("Packageset[%s] scanning %d series: %s" % (
                self.queue, len(ubuntu_series),
                ", ".join("%s (%s)" % (s.name, s.status) for s in ubuntu_series)))

            # In verbose mode, show the current content of the queue
            if self.verbose and self.queue not in self.queue_state:
                self.queue_state[self.queue] = set()

            # Get the content of the current queue
            new_list = set()
            pkgset_count = 0
            api_call_count = 0
            for series in ubuntu_series:
                pkgsets = list(self.lp.packagesets.getBySeries(distroseries=series))
                api_call_count += 1
                self.log.debug("Packageset[%s] series %s has %d packagesets" % (self.queue, series.name, len(pkgsets)))
                for pkgset in pkgsets:
                    pkgset_count += 1
                    sources = list(pkgset.getSourcesIncluded())
                    api_call_count += 1
                    for pkg in sources:
                        new_list.add(";".join([
                            series.self_link,
                            series.name,
                            pkgset.name,
                            pkg
                        ]))
            self.log.info("Packageset[%s] fetched %d packagesets with %d API calls, %d total entries" % (
                self.queue, pkgset_count, api_call_count, len(new_list)))

            if self.queue in self.queue_state:
                if len(new_list - self.queue_state[self.queue]) > 25:
                    self.notices.append(("%s: %s entries have been"
                                         " added or removed" %
                                         (self.queue,
                                          len(new_list -
                                              self.queue_state[self.queue])),
                                         ['packageset']))
                elif len(self.queue_state[self.queue] - new_list) > 25:
                    self.notices.append(("%s: %s entries have been"
                                         " added or removed" %
                                         (self.queue,
                                          len(self.queue_state[self.queue] -
                                              new_list)),
                                         ['packageset']))
                else:
                    # Print removed packages
                    for pkg in sorted(self.queue_state[self.queue] - new_list):
                        pkg_seriesurl, pkg_series, pkg_set, \
                            pkg_name = pkg.split(';')

                        self.notices.append(("%s: Removed %s from %s in %s" % (
                            self.queue, pkg_name, pkg_set, pkg_series),
                            ['packageset']))

                    # Print added packages
                    for pkg in sorted(new_list - self.queue_state[self.queue]):
                        pkg_seriesurl, pkg_series, pkg_set, \
                            pkg_name = pkg.split(';')

                        self.notices.append(("%s: Added %s to %s in %s" % (
                            self.queue, pkg_name, pkg_set, pkg_series),
                            ['packageset']))

            self.queue_state[self.queue] = new_list
        except:
            # We don't want the bot to crash when LP fails
            traceback.print_exc()
        finally:
            self.log.info("Packageset[%s] scan finished in %.1f seconds" % (self.queue, time.time() - scan_start))


class Packageset():
    queue_state = dict()
    scanner = PackagesetScanner()
    name = "packageset"
    queue = ""

    def __init__(self, queue, verbose=False, log=None, series_statuses=None):
        self.queue = queue
        self.verbose = verbose
        self.log = log
        self.series_statuses = series_statuses or []
        self.queue_state = dict()
        self.spawn_scanner()

    def spawn_scanner(self):
        if self.scanner.is_alive():
            raise Exception("Scanner is already running")

        self.scanner = PackagesetScanner()
        self.scanner.queue_state = self.queue_state
        self.scanner.verbose = self.verbose
        self.scanner.queue = self.queue
        self.scanner.series_statuses = self.series_statuses
        if self.log is not None:
            self.scanner.log = self.log
        self.scanner.start()

    def update(self):
        if self.scanner.is_alive():
            return False

        # Get the result from the thread
        notices = list(self.scanner.notices)

        # Spawn a new insance of the monitoring thread
        self.spawn_scanner()

        return notices
