#!/usr/bin/python
from __future__ import print_function

import os
import logging
import traceback
import threading
import time
import configparser
from launchpadlib.launchpad import Launchpad
from launchpadlib.credentials import Credentials


def create_launchpad_connection(name, log=None):
    """Create a Launchpad connection, using credentials file if available."""
    credentials_file = os.path.expanduser("~/.secret/lp.txt")
    lp = None
    if os.path.exists(credentials_file):
        try:
            # Load credentials directly from the INI file — avoids login_with()
            # which starts an interactive OAuth browser flow when the token is
            # invalid, instead of raising an exception.
            cfg = configparser.ConfigParser()
            cfg.read(credentials_file)
            if not cfg.has_section('1'):
                raise ValueError("Credentials file missing [1] section")
            consumer_key = cfg.get('1', 'consumer_key', fallback='?')
            if log:
                log.info("Launchpad login: loading stored token from %s (consumer_key=%s)" % (credentials_file, consumer_key))
            # Use Credentials.load() — the most portable way across launchpadlib
            # versions; reads consumer_key, access_token etc. directly from the
            # INI file without needing to know the exact constructor signature.
            credentials = Credentials()
            with open(credentials_file) as f:
                credentials.load(f)
            lp = Launchpad(credentials,
                           None,   # authorization_engine
                           None,   # credential_store
                           service_root='https://api.launchpad.net/',
                           version='devel')
            # Validate the token with a lightweight call before declaring success
            _ = lp.me.name
            if log:
                log.info("Launchpad login: authenticated login succeeded (as %s)" % lp.me.name)
        except Exception as e:
            if log:
                log.warning("Launchpad login: authenticated login failed (%s), falling back to anonymous" % e)
            lp = None
    if lp is None:
        if log:
            log.info("Launchpad login: using anonymous access")
        lp = Launchpad.login_anonymously(
            'maubot-queuebot', 'production',
            launchpadlib_dir="/tmp/queuebot-%s/" % name,
            version='devel')
    return lp


class QueueScanner(threading.Thread):
    notices = list()
    log = logging.getLogger(__name__)
    lp = None
    queue_plugin = None

    def run(self):
        scan_start = time.time()
        self.log.info("Queue[%s] scan started" % self.queue)
        try:
            # Create Launchpad connection on first scan, then reuse it
            if self.lp is None:
                self.lp = create_launchpad_connection(self.queue, self.log)
                if self.queue_plugin is not None:
                    self.queue_plugin.lp = self.lp

            self.notices = list()

            ubuntu = self.lp.distributions['ubuntu']
            ubuntu_series = [series for series in ubuntu.series
                             if series.active]

            # In verbose mode, show the current content of the queue
            if self.verbose and self.queue not in self.queue_state:
                self.queue_state[self.queue] = set()

            # Get the content of the current queue
            new_list = set()
            for series in ubuntu_series:
                for pkg in series.getPackageUploads(status=self.queue):
                    # Split the different sub-packages
                    all_name = pkg.display_name.split(', ')
                    all_arch = pkg.display_arches.split(', ')
                    all_pkg = []
                    for name in all_name:
                        all_pkg.append((name, all_arch[all_name.index(name)]))

                    for (name, arch) in all_pkg:
                        if name.startswith('language-pack-'):
                            continue

                        if name.startswith('kde-l10n-'):
                            continue

                        if arch.startswith('raw-'):
                            continue

                        if arch == 'uefi' or arch == 'signing':
                            continue

                        new_list.add(";".join([
                            series.self_link,
                            "%s-%s" % (series.name.lower(),
                                       pkg.pocket.lower()),
                            name,
                            pkg.display_version,
                            arch,
                            pkg.archive.name,
                            pkg.self_link,
                        ]))

            if self.queue in self.queue_state:
                # Print removed packages
                for pkg in sorted(self.queue_state[self.queue] - new_list):
                    pkg_seriesurl, pkg_pocket, pkg_name, pkg_version, \
                        pkg_arch, pkg_archive, pkg_self = pkg.split(';')
                    pkg_status = self.lp.load(pkg_self).status
                    if pkg_status == "Rejected":
                        status = "rejected"
                    elif pkg_status in ("Accepted", "Done"):
                        status = "accepted"
                    else:
                        print("Impossible package status: %s "
                              "(%s, %s, %s, %s, %s)" %
                              (pkg_status, self.queue, pkg_name,
                               pkg_arch, pkg_pocket, pkg_version))
                        continue

                    mute = (
                        "queue;%s" % (pkg_pocket),
                        "queue;%s" % (self.queue.lower()),
                        "queue;%s;%s" % (pkg_pocket, self.queue.lower()),
                        "queue;%s;%s" % (self.queue.lower(), pkg_pocket)
                        )
                    self.notices.append(("%s: %s %s [%s] (%s) [%s]" % (
                        self.queue, status, pkg_name, pkg_arch,
                        pkg_pocket, pkg_version), mute))

                # Print added packages
                for pkg in sorted(new_list - self.queue_state[self.queue]):
                    pkg_seriesurl, pkg_pocket, pkg_name, pkg_version, \
                        pkg_arch, pkg_archive, pkg_self = pkg.split(';')
                    pkg_series = self.lp.load(pkg_seriesurl)

                    # Try to get some more data by looking at
                    # the current archive
                    current_component = 'none'
                    current_version = 'none'
                    current_pkgsets = set()
                    for archive in ubuntu.archives:
                        current_pkg = archive.getPublishedSources(
                            source_name=pkg_name, status="Published",
                            distro_series=pkg_series, exact_match=True)
                        if list(current_pkg):
                            current_component = current_pkg[0].component_name
                            current_version = \
                                current_pkg[0].source_package_version
                            break

                    for pkgset in self.lp.packagesets.setsIncludingSource(
                            distroseries=pkg_series,
                            sourcepackagename=pkg_name):
                        current_pkgsets.add(pkgset.name)

                    # Prepare the packageset list
                    if current_pkgsets:
                        pkg_pkgsets = ", ".join(sorted(current_pkgsets))
                    else:
                        pkg_pkgsets = "no packageset"

                    # Post the message to the channel
                    message = ""
                    if self.queue == 'New':
                        if pkg_arch == "source":
                            message = "%s source: %s (%s/%s) [%s]" % (
                                self.queue, pkg_name, pkg_pocket,
                                pkg_archive, pkg_version)
                        elif pkg_arch == "sync":
                            message = "%s sync: %s (%s/%s) [%s]" % (
                                self.queue, pkg_name, pkg_pocket,
                                pkg_archive, pkg_version)
                        else:
                            message = "%s binary: %s [%s] (%s/%s) [%s] (%s)" \
                                % (self.queue, pkg_name, pkg_arch,
                                    pkg_pocket, current_component,
                                    pkg_version, pkg_pkgsets)
                    else:
                        message = "%s: %s (%s/%s) [%s => %s] (%s)" % (
                            self.queue, pkg_name, pkg_pocket,
                            current_component, current_version,
                            pkg_version, pkg_pkgsets)

                        if pkg_arch == "sync":
                            message += " (sync)"

                    mute = (
                        "queue;%s" % (pkg_pocket),
                        "queue;%s" % (self.queue.lower()),
                        "queue;%s;%s" % (pkg_pocket, self.queue.lower()),
                        "queue;%s;%s" % (self.queue.lower(), pkg_pocket)
                        )
                    self.notices.append((message, mute))
            self.queue_state[self.queue] = new_list
        except:
            # We don't want the bot to crash when LP fails
            traceback.print_exc()
        finally:
            self.log.info("Queue[%s] scan finished in %.1f seconds" % (self.queue, time.time() - scan_start))


class Queue():
    queue_state = dict()
    scanner = QueueScanner()
    name = "queue"
    queue = ""

    def __init__(self, queue, verbose=False, log=None):
        self.queue = queue
        self.verbose = verbose
        self.log = log
        self.queue_state = dict()
        # Persistent Launchpad connection — created on first scan (in background
        # thread), then reused for all subsequent scans to avoid repeated TLS
        # handshake overhead.
        self.lp = None
        self.spawn_scanner()

    def spawn_scanner(self):
        if self.scanner.is_alive():
            raise Exception("Scanner is already running")

        self.scanner = QueueScanner()
        self.scanner.queue_state = self.queue_state
        self.scanner.verbose = self.verbose
        self.scanner.queue = self.queue
        self.scanner.lp = self.lp          # None on first scan; set after
        self.scanner.queue_plugin = self    # Reference so scanner can store lp back
        if self.log is not None:
            self.scanner.log = self.log
        self.scanner.start()

    def update(self):
        if self.scanner.is_alive():
            return False

        # Get the result from the thread
        notices = list(self.scanner.notices)

        # Spawn a new instance of the monitoring thread
        self.spawn_scanner()

        return notices
