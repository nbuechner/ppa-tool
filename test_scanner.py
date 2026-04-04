#!/usr/bin/env python3
"""
Simulate two consecutive packageset scanner runs to measure:
- Time per run
- Number of API calls per run
- Speed improvement from reusing the lp connection
"""
import os
import time
import logging
import configparser
from launchpadlib.launchpad import Launchpad
from launchpadlib.credentials import Credentials

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger('test_scanner')

SERIES_STATUSES = ['Active Development', 'Pre-release Freeze', 'Frozen', 'Current Stable Release']
CACHE_DIR = '/tmp/queuebot-test/'
CREDENTIALS_FILE = os.path.expanduser('~/.secret/lp.txt')


def create_lp(force_anonymous=False):
    if not force_anonymous and os.path.exists(CREDENTIALS_FILE):
        try:
            log.info("Loading credentials from: %s" % CREDENTIALS_FILE)
            # Load credentials directly from the INI file — avoids login_with()
            # which starts an interactive OAuth browser flow when the token is
            # invalid, instead of raising an exception.
            cfg = configparser.ConfigParser()
            cfg.read(CREDENTIALS_FILE)
            if not cfg.has_section('1'):
                raise ValueError("Credentials file missing [1] section — needs regeneration via lp.py")
            consumer_key = cfg.get('1', 'consumer_key', fallback='?')
            log.info("Loading token for consumer_key=%s" % consumer_key)
            # Use Credentials.load() — portable across launchpadlib versions;
            # reads consumer_key, access_token etc. directly from the INI file.
            credentials = Credentials()
            with open(CREDENTIALS_FILE) as f:
                credentials.load(f)
            lp = Launchpad(credentials,
                           None,   # authorization_engine
                           None,   # credential_store
                           service_root='https://api.launchpad.net/',
                           version='devel')
            # Validate the token with a lightweight call — raises if revoked
            me = lp.me.name
            log.info("Authenticated login succeeded (as %s)" % me)
            return lp
        except Exception as e:
            log.warning("Authenticated login failed: %s — falling back to anonymous" % e)
    log.info("Using anonymous login")
    return Launchpad.login_anonymously(
        'maubot-queuebot', 'production',
        launchpadlib_dir=CACHE_DIR,
        version='devel')


def run_packageset_scan(lp, run_number):
    log.info("=" * 60)
    log.info("=== PACKAGESET SCAN RUN %d ===" % run_number)
    log.info("=" * 60)
    scan_start = time.time()

    ubuntu = lp.distributions['ubuntu']
    ubuntu_series = [s for s in ubuntu.series
                     if s.active and s.status in SERIES_STATUSES]

    log.info("Series to scan: %s" % ", ".join("%s (%s)" % (s.name, s.status) for s in ubuntu_series))

    new_list = set()
    pkgset_count = 0
    api_call_count = 0
    series_times = []

    for series in ubuntu_series:
        series_start = time.time()
        pkgsets = list(lp.packagesets.getBySeries(distroseries=series))
        api_call_count += 1
        log.info("  Series %s: %d packagesets" % (series.name, len(pkgsets)))

        for pkgset in pkgsets:
            pkgset_count += 1
            call_start = time.time()
            sources = list(pkgset.getSourcesIncluded())
            api_call_count += 1
            call_time = time.time() - call_start
            if call_time > 1.0:
                log.info("    SLOW: %s took %.2fs (%d sources)" % (pkgset.name, call_time, len(sources)))
            for pkg in sources:
                new_list.add("%s;%s;%s" % (series.name, pkgset.name, pkg))

        series_elapsed = time.time() - series_start
        series_times.append((series.name, len(pkgsets), series_elapsed))
        log.info("  Series %s done in %.1fs" % (series.name, series_elapsed))

    total_time = time.time() - scan_start
    log.info("")
    log.info("=== RUN %d SUMMARY ===" % run_number)
    log.info("  Total time:      %.1f seconds" % total_time)
    log.info("  Packagesets:     %d" % pkgset_count)
    log.info("  API calls:       %d" % api_call_count)
    log.info("  Total entries:   %d" % len(new_list))
    log.info("  Avg per call:    %.3f seconds" % (total_time / api_call_count if api_call_count else 0))
    log.info("")

    return new_list, total_time


def run_queue_scan(lp, queue_name, run_number):
    log.info("=" * 60)
    log.info("=== QUEUE[%s] SCAN RUN %d ===" % (queue_name, run_number))
    log.info("=" * 60)
    scan_start = time.time()

    ubuntu = lp.distributions['ubuntu']
    ubuntu_series = [s for s in ubuntu.series if s.active]
    log.info("Active series: %d" % len(ubuntu_series))

    new_list = set()
    api_call_count = 0
    for series in ubuntu_series:
        call_start = time.time()
        pkgs = list(series.getPackageUploads(status=queue_name))
        api_call_count += 1
        log.info("  %s: %d packages in %s queue (%.2fs)" % (
            series.name, len(pkgs), queue_name, time.time() - call_start))
        for pkg in pkgs:
            new_list.add("%s;%s" % (series.name, pkg.display_name))

    total_time = time.time() - scan_start
    log.info("=== Queue[%s] Run %d: %.1fs, %d calls, %d packages ===" % (
        queue_name, run_number, total_time, api_call_count, len(new_list)))
    return new_list, total_time


if __name__ == '__main__':
    import sys
    force_anon = '--anonymous' in sys.argv
    log.info("Creating Launchpad connection (will be reused across runs)...")
    connect_start = time.time()
    lp = create_lp(force_anonymous=force_anon)
    log.info("Connection created in %.1fs" % (time.time() - connect_start))

    # --- Run 1 ---
    ps_list1, ps_time1 = run_packageset_scan(lp, 1)
    q_new_list1, q_new_time1 = run_queue_scan(lp, 'New', 1)
    q_unapp_list1, q_unapp_time1 = run_queue_scan(lp, 'Unapproved', 1)

    log.info("")
    log.info("Sleeping 5 seconds between runs (simulating connection reuse)...")
    time.sleep(5)

    # --- Run 2 (same lp connection) ---
    ps_list2, ps_time2 = run_packageset_scan(lp, 2)
    q_new_list2, q_new_time2 = run_queue_scan(lp, 'New', 2)
    q_unapp_list2, q_unapp_time2 = run_queue_scan(lp, 'Unapproved', 2)

    # --- Final comparison ---
    log.info("")
    log.info("=" * 60)
    log.info("=== COMPARISON ===")
    log.info("=" * 60)
    log.info("                    Run 1      Run 2    Speedup")
    log.info("Packageset scan:  %6.1fs    %6.1fs    %4.1fx" % (ps_time1, ps_time2, ps_time1 / ps_time2 if ps_time2 else 0))
    log.info("Queue[New]:       %6.1fs    %6.1fs    %4.1fx" % (q_new_time1, q_new_time2, q_new_time1 / q_new_time2 if q_new_time2 else 0))
    log.info("Queue[Unapproved]:%6.1fs    %6.1fs    %4.1fx" % (q_unapp_time1, q_unapp_time2, q_unapp_time1 / q_unapp_time2 if q_unapp_time2 else 0))

    # Check for differences between runs
    ps_diff = ps_list1.symmetric_difference(ps_list2)
    if ps_diff:
        log.info("Packageset changes between runs: %d entries changed" % len(ps_diff))
    else:
        log.info("Packageset: no changes between runs (expected for 5s gap)")
