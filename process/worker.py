#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from os import getenv, path, walk
from sys import exit, argv
import logging
from datetime import datetime, timezone
from json import load as json_load
from hashlib import md5
from typing import Union
import mysql.connector
from uuid import UUID

import sentry_sdk

import hvac

from influxdb import InfluxDBClient

from jinja2 import Environment, FileSystemLoader

from smtplib import SMTP
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication


def before_send(event: Union[dict, None], hint: Union[dict, None]) -> dict:
    """ Sentry - Generate a new fingerprint only based on event message """
    msg = event.get('logentry').get('message')
    if msg is not None:
        event['fingerprint'] = [md5(msg.encode('utf-8')).hexdigest()]
    return event


def duration(start_date: Union[str, None], end_date: Union[str, None]) -> str:
    """  Calculate the duration in H:M:S between two dates in string isoformat """
    if not start_date:
        return "-"

    start = datetime.strptime(start_date, '%Y-%m-%dT%H:%M:%SZ')

    if not end_date:
        end = datetime.now()
    else:
        end = datetime.strptime(end_date, '%Y-%m-%dT%H:%M:%SZ')

    duration_time = end - start
    hours, remainder = divmod(duration_time.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)

    return '{:02}:{:02}:{:02}'.format(int(hours), int(minutes), int(seconds))


def duration_in_seconds(start_date: Union[str, None], end_date: Union[str, None]) -> int:
    """  Calculate the duration in seconds between two dates in string isoformat """

    if not start_date:
        return None

    start = datetime.strptime(start_date, '%Y-%m-%dT%H:%M:%SZ')

    if not end_date:
        end = datetime.now()
    else:
        end = datetime.strptime(end_date, '%Y-%m-%dT%H:%M:%SZ')

    duration_time = end - start

    return duration_time.total_seconds()


def format_datetime_title(date: Union[datetime, None]) -> str:
    """ Format datetime for title
        Example : Sunday, 06 February at 14:30 """
    if date is None or not isinstance(date, datetime):
        return None

    return f'{date.strftime("%A, %d %B at %H:%M")}'


def format_datetime(date: Union[str, None]) -> str:
    """ Format datetime in human readable format
        Example : 2022-02-05 19:02:14 """

    if date is None:
        return '-'
    date = datetime.strptime(date, '%Y-%m-%dT%H:%M:%SZ')
    return date.strftime('%Y-%m-%d %H:%M:%S')


def format_date(date: Union[str, None]) -> str:
    """ Format date in human readable format
        Example : 2022-02-05 """

    if date is None:
        return 'None'
    date = datetime.strptime(date, '%Y-%m-%dT%H:%M:%SZ')
    return date.strftime('%Y-%m-%d')


def error_text(error: Union[str, None]) -> str:
    """ Truncate long error message """

    if error is None:
        return ''
    if len(error) > 80:
        error_return = error.replace('"', '&quot').replace('\n', ' ')
        splited = error_return.split('. ')[0]
        if len(splited) > 80:
            return f'<abbr title="{error_return}">{splited[:80]}</abbr>'
        else:
            return f'<abbr title="{error_return}">{splited}</abbr>'
    else:
        return error


def sizeof_fmt(num: int, suffix: str = 'B') -> str:
    """ Format bytes as human readable with auto suffix """

    for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']:
        if abs(num) < 1000.0:
            return f'{num:3.1f}{unit}{suffix}'
        num /= 1000.0
    return f'{num:.1f}Yi{suffix}'


def lps_duration_color(lps_duration: Union[int, None]) -> str:
    """ Return the CSS color class depending of
        Last Point in Success """

    if not lps_duration:
        return 'bg-error'

    if lps_duration >= 7 * 24 * 3600:
        return 'bg-error'
    elif 2 * 24 * 3600 < lps_duration < 7 * 24 * 3600:
        return 'bg-warning'
    elif 1 * 24 * 3600 < lps_duration < 2 * 24 * 3600:
        return 'bg-info'
    elif lps_duration < 1 * 24 * 3600:
        return 'fg-success'
    else:
        return ''


def rp_color(session: dict) -> str:
    """ Return the CSS color class depending of
        nb RP and comparing the job options """

    if session.get('nb_restore_points') > session.get('retaincycles') * 1.2:
        return 'bg-error'
    elif session.get('nb_restore_points') > session.get('retaincycles'):
        return 'bg-warning'
    elif session.get('nb_restore_points') < session.get('retaincycles'):
        return 'bg-info'
    else:
        return ''


def repo_free_color(free: int) -> str:
    """ Return the CSS color class depending of
        repository free space percent """

    if free <= 3:
        return 'bg-error'
    elif 2 < free <= 8:
        return 'bg-warning'
    else:
        return ''


def percent_mail(nb_sessions_calculated):
    if nb_sessions_calculated == 0:
        output = 0
    elif 0 < nb_sessions_calculated < 1:
        output = "<1"
    else:
        output = int(nb_sessions_calculated)
    return output


def datetime_fmt_to_mysql(date):
    result = date.replace('Z', '').replace('T', ' ')
    return result


# Add capabilities to JSON serialize UUID and datetime objects
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            if obj is None:
                return None
            else:
                return obj.replace(tzinfo=timezone.utc, microsecond=0).isoformat().replace('+00:00', 'Z')
        return json.JSONEncoder.default(self, obj)


# Define logger format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s : %(lineno)d : %(levelname)s : %(module)s : %(funcName)s : %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Initialize Sentry SDK
if not getenv('SENTRY_DSN'):
    logging.error('Environment variable SENTRY_DSN is not defined')
    exit(1)
sentry_sdk.init(
    getenv('SENTRY_DSN'),
    before_send=before_send,
    transport_queue_size=10000
)

# Main

# Global Variables
begin = datetime.now()
scriptPath = path.dirname(path.realpath(__file__))
json_files = []
CI_PIPELINE_ID = getenv('CI_PIPELINE_ID')
CI_PIPELINE_CREATED_AT = getenv('CI_PIPELINE_CREATED_AT')
COMMENT = getenv('COMMENT')

stats = {
    'backup': {
        'sessions': 0, 'total': 0, 'success': 0, 'warning': 0, 'failed': 0, 'running': 0, 'pending': 0, 'idle': 0, 'in_progress': 0, 'undefined': 0
    },
    'tape': {
        'sessions': 0, 'success': 0, 'warning': 0, 'failed': 0, 'running': 0, 'pending': 0, 'idle': 0, 'in_progress': 0, 'undefined': 0
    },
    'repositories': 0
}

# Get SQL queries
sql_insert_pipeline = open(scriptPath + '/sql/insert_pipeline.sql', 'r').read()
sql_insert_info = open(scriptPath + '/sql/insert_info.sql', 'r').read()
sql_insert_tape = open(scriptPath + '/sql/insert_tape.sql', 'r').read()
sql_insert_in_progress = open(scriptPath + '/sql/insert_in_progress.sql', 'r').read()
sql_insert_failed = open(scriptPath + '/sql/insert_failed.sql', 'r').read()
sql_insert_repositorie = open(scriptPath + '/sql/insert_repositorie.sql', 'r').read()

server_infos = dict()
sessions_tape = dict()
sessions_failed = dict()
sessions_in_progress = dict()
repositories = dict()

logging.info('Script start : %s' % __file__)
logging.info('Parameters : %s' % (', '.join(argv[1:]) or 'None'))

# Retrieve credentials from Vault or read them from env vars
if getenv('VAULT_ADDR'):
    for var in ['VAULT_ADDR', 'VAULT_TOKEN', 'VAULT_CREDENTIALS_PATH']:
        if not getenv(var):
            raise Exception(f'Required environment variable {var} is not defined')

    vault = hvac.Client(url=getenv('VAULT_ADDR'),
                        token=getenv('VAULT_TOKEN'))
    vault_res = vault.is_authenticated()
    logging.info('Vault auth res  : ' + str(vault_res))
    read_secret_synapps_result = vault.read(
        getenv('VAULT_CREDENTIALS_PATH')
    )
    if read_secret_synapps_result['data']['data']:
        credentials = read_secret_synapps_result['data']['data']
        DATABASE_ADDRESS = credentials.get('DATABASE_ADDRESS')
        DATABASE_PORT = credentials.get('DATABASE_PORT')
        DATABASE_NAME = credentials.get('DATABASE_NAME')
        DATABASE_USERNAME = credentials.get('DATABASE_USERNAME')
        DATABASE_PASSWORD = credentials.get('DATABASE_PASSWORD')
    else:
        raise Exception('Unable to retrieve credentials from Vault')
else:
    for var in ['DATABASE_ADDRESS', 'DATABASE_PORT', 'DATABASE_NAME', 'DATABASE_USERNAME', 'DATABASE_PASSWORD']:
        if not getenv(var):
            raise Exception(f'Required environment variable {var} is not defined')
    DATABASE_ADDRESS = getenv('DATABASE_ADDRESS')
    DATABASE_PORT = getenv('DATABASE_PORT')
    DATABASE_NAME = getenv('DATABASE_NAME')
    DATABASE_USERNAME = getenv('DATABASE_USERNAME')
    DATABASE_PASSWORD = getenv('DATABASE_PASSWORD')

# List all JSON files
for root, dirs, files in walk('artifacts/'):
    for file in files:
        if file.lower().endswith('.json'):
            json_files.append(path.join(root, file))

# Connect to MYSQL server
conn = mysql.connector.connect(
        host=DATABASE_ADDRESS,
        port=DATABASE_PORT,
        database=DATABASE_NAME,
        user=DATABASE_USERNAME,
        password=DATABASE_PASSWORD)
# Send pipeline data to database
try:
    cursor = conn.cursor()

    cursor.execute(sql_insert_pipeline, (CI_PIPELINE_ID, begin, COMMENT))
    conn.commit()
    id_pipeline = cursor.lastrowid

except Exception as e:
    print("insert mcb_pipeline failed: ", e)
    sentry_sdk.capture_exception(e)

# Iterate JSON files
for file in json_files:
    data, SERVER_NAME, stats_backup, stats_tape, stats_repositories = None, None, None, None, None
    with open(file) as f:
        data = json_load(f)
    if data:
        infos = data.get('infos')
        if infos:
            SERVER_NAME = infos.get('SERVER_NAME')
            # Calculate the sum of all stats and store the infos
            # in the variable server_infos grouped by SERVER_NAME
            if infos.get('stats'):
                if infos.get('stats').get('backup'):
                    stats_backup = infos.get('stats').get('backup')
                    stats['backup']['sessions'] += stats_backup.get('sessions')
                    stats['backup']['total'] += stats_backup.get('total')
                    stats['backup']['success'] += stats_backup.get('success')
                    stats['backup']['warning'] += stats_backup.get('warning')
                    stats['backup']['failed'] += stats_backup.get('failed')
                    stats['backup']['running'] += stats_backup.get('running')
                    stats['backup']['pending'] += stats_backup.get('pending')
                    stats['backup']['idle'] += stats_backup.get('idle')
                    stats['backup']['undefined'] += stats_backup.get('undefined')
                    stats['backup']['in_progress'] += stats_backup.get('running') + stats_backup.get('pending')

                if infos.get('stats').get('tape'):
                    stats_tape = infos.get('stats').get('tape')
                    stats['tape']['sessions'] += stats_tape.get('sessions')
                    stats['tape']['success'] += stats_tape.get('success')
                    stats['tape']['warning'] += stats_tape.get('warning')
                    stats['tape']['failed'] += stats_tape.get('failed')
                    stats['tape']['running'] += stats_tape.get('running')
                    stats['tape']['pending'] += stats_tape.get('pending')
                    stats['tape']['idle'] += stats_tape.get('idle')
                    stats['tape']['undefined'] += stats_tape.get('undefined')
                    stats['tape']['in_progress'] += stats_tape.get('in_progress')

                if infos.get('stats').get('repositories'):
                    stats['repositories'] += infos.get('stats').get('repositories')

            server_infos[SERVER_NAME] = infos

# Send info data to database
try:
    cursor.execute(sql_insert_info, (
        id_pipeline, SERVER_NAME, stats['backup']['sessions'], stats['backup']['total'],
        stats['backup']['success'], stats['backup']['warning'], stats['backup']['failed'],
        stats['backup']['running'], stats['backup']['pending'], stats['backup']['idle'],
        stats['backup']['undefined'], stats['backup']['in_progress'], stats['tape']['sessions'],
        stats['tape']['success'], stats['tape']['warning'], stats['tape']['failed'],
        stats['tape']['running'], stats['tape']['pending'], stats['tape']['idle'],
        stats['tape']['undefined'], stats['tape']['in_progress'], stats['repositories']))
    conn.commit()
    id_infos = cursor.lastrowid

except Exception as e:
    print("insert mcb_info failed: ", e)
    sentry_sdk.capture_exception(e)

for file in json_files:
    data, SERVER_NAME, stats_backup, stats_tape, stats_repositories = None, None, None, None, None
    with open(file) as f:
        data = json_load(f)
    if data:
        infos = data.get('infos')
        # Get server_name
        if infos:
            SERVER_NAME = infos.get('SERVER_NAME')

        sessions_root = data.get('sessions')
        if sessions_root.get('tape'):
            # Formatting values for Jinja2
            for job in sessions_root.get('tape'):
                tape = sessions_root.get('tape').get(job)

                # Send tapes data to database
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql_insert_tape, (
                        id_infos,
                        datetime_fmt_to_mysql(sessions_root['tape'][job]['start_date']),
                        datetime_fmt_to_mysql(sessions_root['tape'][job]['end_date']),
                        sessions_root['tape'][job]['backup_status'],
                        sessions_root['tape'][job]['backup_status_details'],
                        sessions_root['tape'][job]['job_name'],
                        sessions_root['tape'][job]['job_id'],
                        sessions_root['tape'][job]['reason'],
                        sessions_root['tape'][job]['mediapool_name']))
                    conn.commit()
                except Exception as e:
                    print("insert mcb_tape failed: ", e)
                    sentry_sdk.capture_exception(e)

                sessions_root['tape'][job]['reason'] = error_text(tape.get('reason'))
                sessions_root['tape'][job]['duration_color'] = 'bg-error' if duration_in_seconds(tape.get('start_date'), tape.get('end_date')) >= 20 * 3600 else ''
                sessions_root['tape'][job]['duration'] = duration(tape.get('start_date'), tape.get('end_date'))
                sessions_root['tape'][job]['start_date'] = format_datetime(tape.get('start_date'))
                sessions_root['tape'][job]['end_date'] = format_datetime(tape.get('end_date'))
            sessions_tape[SERVER_NAME] = sessions_root.get('tape')

        if sessions_root.get('in_progress'):
            # Formatting values for Jinja2
            for job in sessions_root.get('in_progress'):
                for vm in sessions_root.get('in_progress').get(job):
                    # Send in progress data to database
                    try:
                        cursor = conn.cursor()
                        cursor.execute(sql_insert_in_progress, (
                            id_infos,
                            datetime_fmt_to_mysql(sessions_root['in_progress'][job][vm]['start_date']),
                            sessions_root['in_progress'][job][vm]['session_id'],
                            sessions_root['in_progress'][job][vm]['orig_session_id'],
                            sessions_root['in_progress'][job][vm]['backup_status'],
                            sessions_root['in_progress'][job][vm]['backup_status_details'],
                            datetime_fmt_to_mysql(sessions_root['in_progress'][job][vm]['last_point_success']),
                            sessions_root['in_progress'][job][vm]['object_id'],
                            sessions_root['in_progress'][job][vm]['job_name'],
                            sessions_root['in_progress'][job][vm]['job_id'],
                            sessions_root['in_progress'][job][vm]['type'],
                            sessions_root['in_progress'][job][vm]['object_name'],
                            sessions_root['in_progress'][job][vm]['backup_transport_mode'],
                            sessions_root['in_progress'][job][vm]['target_storage'],
                            sessions_root['in_progress'][job][vm]['proxies'],
                            sessions_root['in_progress'][job][vm]['nb_restore_points'],
                            sessions_root['in_progress'][job][vm]['retaindays'],
                            sessions_root['in_progress'][job][vm]['retaincycles'],
                            sessions_root['in_progress'][job][vm]['retention_maintenance']))
                        conn.commit()
                    except Exception as e:
                        print("insert mcb_in_progress failed: ", e)
                        sentry_sdk.capture_exception(e)

                    in_progress = sessions_root.get('in_progress').get(job).get(vm)
                    sessions_root['in_progress'][job][vm]['duration_color'] = 'bg-error' if duration_in_seconds(in_progress.get('start_date'), None) >= 20 * 3600 else ''
                    sessions_root['in_progress'][job][vm]['lps_duration'] = duration_in_seconds(in_progress.get('last_point_success'), None)
                    sessions_root['in_progress'][job][vm]['lps_color'] = lps_duration_color(sessions_root['in_progress'][job][vm]['lps_duration'])
                    sessions_root['in_progress'][job][vm]['rp_color'] = rp_color(in_progress)
                    sessions_root['in_progress'][job][vm]['duration'] = duration(in_progress.get('start_date'), None)
                    sessions_root['in_progress'][job][vm]['start_date'] = format_datetime(in_progress.get('start_date'))
                    sessions_root['in_progress'][job][vm]['last_point_success'] = format_date(in_progress.get('last_point_success'))
            sessions_in_progress[SERVER_NAME] = sessions_root.get('in_progress')

        if sessions_root.get('failed'):
            # Formatting values for Jinja2
            for job in sessions_root.get('failed'):
                for vm in sessions_root.get('failed').get(job):

                    # Send failed data to database
                    try:
                        cursor = conn.cursor()
                        cursor.execute(sql_insert_failed, (
                            id_infos,
                            datetime_fmt_to_mysql(sessions_root['failed'][job][vm]['start_date']),
                            datetime_fmt_to_mysql(sessions_root['failed'][job][vm]['end_date']),
                            sessions_root['failed'][job][vm]['session_id'],
                            sessions_root['failed'][job][vm]['orig_session_id'],
                            sessions_root['failed'][job][vm]['backup_status'],
                            sessions_root['failed'][job][vm]['backup_status_details'],
                            datetime_fmt_to_mysql(sessions_root['failed'][job][vm]['last_point_success']),
                            sessions_root['failed'][job][vm]['object_id'],
                            sessions_root['failed'][job][vm]['job_name'],
                            sessions_root['failed'][job][vm]['job_id'],
                            sessions_root['failed'][job][vm]['type'],
                            sessions_root['failed'][job][vm]['reason'],
                            sessions_root['failed'][job][vm]['object_name'],
                            sessions_root['failed'][job][vm]['backup_transport_mode'],
                            sessions_root['failed'][job][vm]['target_storage'],
                            sessions_root['failed'][job][vm]['proxies'],
                            sessions_root['failed'][job][vm]['nb_restore_points'],
                            sessions_root['failed'][job][vm]['retaindays'],
                            sessions_root['failed'][job][vm]['retaincycles'],
                            sessions_root['failed'][job][vm]['retention_maintenance']))
                        conn.commit()
                    except Exception as e:
                        print("insert mcb_failed failed: ", e)
                        sentry_sdk.capture_exception(e)

                    failed = sessions_root.get('failed').get(job).get(vm)
                    sessions_root['failed'][job][vm]['duration_color'] = 'bg-error' if duration_in_seconds(failed.get('start_date'), failed.get('end_date')) >= 20 * 3600 else ''
                    sessions_root['failed'][job][vm]['lps_duration'] = duration_in_seconds(failed.get('last_point_success'), None)
                    sessions_root['failed'][job][vm]['lps_color'] = lps_duration_color(sessions_root['failed'][job][vm]['lps_duration'])
                    sessions_root['failed'][job][vm]['rp_color'] = rp_color(failed)
                    sessions_root['failed'][job][vm]['last_point_success'] = format_date(failed.get('last_point_success'))
                    sessions_root['failed'][job][vm]['reason'] = error_text(failed.get('reason'))
                    sessions_root['failed'][job][vm]['duration'] = duration(failed.get('start_date'), failed.get('end_date'))
                    sessions_root['failed'][job][vm]['start_date'] = format_datetime(failed.get('start_date'))
                    sessions_root['failed'][job][vm]['end_date'] = format_datetime(failed.get('end_date'))
            sessions_failed[SERVER_NAME] = sessions_root.get('failed')

        if data.get('repositories'):
            # Formatting values for Jinja2
            # Show only repository with free space <= 8
            for repo in data['repositories'].copy():
                if data['repositories'].get(repo).get('id'):

                    # Send repositories (without scale-out) data to database
                    try:
                        cursor = conn.cursor()
                        cursor.execute(sql_insert_repositorie, (
                            id_infos,
                            data['repositories'][repo]['id'],
                            data['repositories'][repo]['name'],
                            None,
                            data['repositories'][repo]['description'],
                            data['repositories'][repo]['type'],
                            data['repositories'][repo]['path'],
                            data['repositories'][repo]['status'],
                            data['repositories'][repo]['host_name'],
                            data['repositories'][repo]['host_ip'],
                            None,
                            data['repositories'][repo]['free'],
                            data['repositories'][repo]['total'],
                            data['repositories'][repo]['used'],))
                        conn.commit()
                    except Exception as e:
                        print("insert mcb_repositorie (without scale-out) failed: ", e)
                        sentry_sdk.capture_exception(e)

                    current_repo = data['repositories'].get(repo)

                    data['repositories'][repo]['free_percent'] = int(current_repo.get('free') * 100 / current_repo.get('total'))
                    data['repositories'][repo]['free_percent_color'] = repo_free_color(data['repositories'][repo]['free_percent'])

                    data['repositories'][repo]['free'] = sizeof_fmt(data['repositories'][repo]['free'])
                    data['repositories'][repo]['used'] = sizeof_fmt(data['repositories'][repo]['used'])
                    data['repositories'][repo]['total'] = sizeof_fmt(data['repositories'][repo]['total'])

                    if data['repositories'][repo]['free_percent'] > 8:
                        del data['repositories'][repo]
                else:
                    scaleout_free, scaleout_used, scaleout_total = 0, 0, 0
                    hasSizeAlert = False
                    for extent in data['repositories'][repo].copy():
                        current_repo = data['repositories'].get(repo).get(extent)
                        scaleout_free += current_repo.get('free')
                        scaleout_used += current_repo.get('used')
                        scaleout_total += current_repo.get('total')
                        if int(current_repo.get('free') * 100 / current_repo.get('total')) <= 8:
                            hasSizeAlert = True
                    if not hasSizeAlert:
                        del data['repositories'][repo]

                    if data.get('repositories').get(repo):
                        for extent in data['repositories'][repo]:

                            # Send repositories (with scale-out) data to database
                            try:
                                cursor = conn.cursor()
                                cursor.execute(sql_insert_repositorie, (
                                    id_infos,
                                    data['repositories'][repo][extent]['id'],
                                    data['repositories'][repo][extent]['name'],
                                    extent,
                                    data['repositories'][repo][extent]['description'],
                                    data['repositories'][repo][extent]['type'],
                                    data['repositories'][repo][extent]['path'],
                                    data['repositories'][repo][extent]['status'],
                                    data['repositories'][repo][extent]['host_name'],
                                    data['repositories'][repo][extent]['host_ip'],
                                    data['repositories'][repo][extent]['scale_out_name'],
                                    data['repositories'][repo][extent]['free'],
                                    data['repositories'][repo][extent]['total'],
                                    data['repositories'][repo][extent]['used'],))
                                conn.commit()
                            except Exception as e:
                                print("insert mcb_repositorie (with scale-out) failed: ", e)
                                sentry_sdk.capture_exception(e)

                            current_repo = data['repositories'].get(repo).get(extent)

                            data['repositories'][repo][extent]['scaleout_free_percent'] = int(scaleout_free * 100 / scaleout_total)
                            data['repositories'][repo][extent]['scaleout_free_percent_color'] = repo_free_color(data['repositories'][repo][extent]['scaleout_free_percent'])
                            data['repositories'][repo][extent]['free_percent'] = int(current_repo.get('free') * 100 / current_repo.get('total'))
                            data['repositories'][repo][extent]['free_percent_color'] = repo_free_color(data['repositories'][repo][extent]['free_percent'])

                            data['repositories'][repo][extent]['scaleout_free'] = sizeof_fmt(scaleout_free)
                            data['repositories'][repo][extent]['scaleout_used'] = sizeof_fmt(scaleout_used)
                            data['repositories'][repo][extent]['scaleout_total'] = sizeof_fmt(scaleout_total)
                            data['repositories'][repo][extent]['free'] = sizeof_fmt(data['repositories'][repo][extent]['free'])
                            data['repositories'][repo][extent]['used'] = sizeof_fmt(data['repositories'][repo][extent]['used'])
                            data['repositories'][repo][extent]['total'] = sizeof_fmt(data['repositories'][repo][extent]['total'])
            if data.get('repositories'):
                repositories[SERVER_NAME] = data.get('repositories')

if len(json_files) == 0:
    sentry_sdk.flush(120)
    logging.info('No JSON found from crawlers')
    logging.info('Script end')
    exit(1)

# Calculate percentages
percent_failed = int(stats['backup']['failed'] * 100 / int(stats['backup']['total']))

stats['backup']['success%'] = percent_mail(stats['backup']['success'] * 100 / int(stats['backup']['total']))
stats['backup']['failed%'] = percent_mail(stats['backup']['failed'] * 100 / int(stats['backup']['total']))
stats['backup']['warning%'] = percent_mail(stats['backup']['warning'] * 100 / int(stats['backup']['total']))
stats['backup']['in_progress%'] = percent_mail(stats['backup']['in_progress'] * 100 / int(stats['backup']['total']))
# Set emoji and color for global status
if percent_failed < 25:
    stats['backup']['emoji'] = '&#128578;'
    stats['backup']['color'] = 'bg-success'
elif 25 <= percent_failed < 50:
    stats['backup']['emoji'] = '&#128528;'
    stats['backup']['color'] = 'bg-warning'
else:
    stats['backup']['emoji'] = '&#128544;'
    stats['backup']['color'] = 'bg-error'

# Define the default folder for Jinja2 files
file_loader = FileSystemLoader(scriptPath + '/jinja')
env = Environment(loader=file_loader)

# Load the Jinja2 template
template = env.get_template('template.j2')

# Render the jinja2 template
html = template.render(
    today=format_datetime_title(begin),
    stats=stats,
    tapes=sessions_tape,
    in_progress=sessions_in_progress,
    failed=sessions_failed,
    repositories=repositories,
    server_infos=server_infos
)

# Write the rendered template to a file
with open('artifacts/output.html', 'w+') as f:
    f.write(html)

delta = datetime.now() - begin

# Send the rendered template by mail
if getenv('DISABLE_MAIL') != '1':
    # Create message container - the correct MIME type is multipart/alternative.
    msg = MIMEMultipart('alternative')

    # Define the mail Subject
    msg['Subject'] = 'Morning check backup - T:{} | S:{} ({}%) | F:{} ({}%) | W:{} ({}%) | IP:{} ({}%)'.format(
        stats.get('backup').get('sessions'),
        stats.get('backup').get('success'),
        stats['backup']['success%'],
        stats.get('backup').get('failed'),
        stats['backup']['failed%'],
        stats.get('backup').get('warning'),
        stats['backup']['warning%'],
        stats.get('backup').get('in_progress'),
        stats['backup']['in_progress%']
    )

    # Define the sender of the mail
    msg['From'] = 'morning-check-backup@ablondel.lycee'

    # Define the recipient(s) of the mail
    msg['To'] = 'FR-infra-stockage@ablondel.lycee'

    # Add HTML body
    part1 = MIMEText(html, 'html')
    msg.attach(part1)

    # Add HTML attachment
    attachment = MIMEApplication(html, Name='Morning check backup.html')
    attachment['Content-Disposition'] = 'attachment; filename="Morning check backup.html"'
    msg.attach(attachment)

    logging.info(f'Sending mail to {msg["To"].split(",")}')
    # Send the mail via internal Claranet' SMTP relay
    with SMTP('smtp-relay-interne.lycee.fr.arno.net') as s:
        s.sendmail(msg['From'], msg['To'].split(','), msg.as_string())

logging.info(f'Total execution time : {str(delta.total_seconds())}')

# Send statistics
if getenv('DISABLE_INFLUXDB') != '1':
    logging.info('Sending stats/metrics to InfluxDB')

    # Create InfluxDB client instance
    client = InfluxDBClient(host='server.adm.fr.arno.net', port=8086)

    # Define InfluxDB templates
    template_influx = '{},job=%s,type=process value={}' % getenv('CI_JOB_NAME')
    template_influx_stats = '{},job=%s,type=process success={},warning={},failed={},running={},pending={},undefined={},sessions={}' % getenv('CI_JOB_NAME')

    influx_data = []

    # Add execution_time metric
    influx_data.append(template_influx.format('execution_time', delta.total_seconds()))

    # Add backups statistics
    influx_data.append(template_influx_stats.format(
        'backup',
        stats['backup']['success'],
        stats['backup']['warning'],
        stats['backup']['failed'],
        stats['backup']['running'],
        stats['backup']['pending'],
        stats['backup']['undefined'],
        stats['backup']['sessions'],
    ))

    # Add tapes statistics
    influx_data.append(template_influx_stats.format(
        'tape',
        stats['tape']['success'],
        stats['tape']['warning'],
        stats['tape']['failed'],
        stats['tape']['running'],
        stats['tape']['pending'],
        stats['tape']['undefined'],
        stats['tape']['sessions'],
    ))

    # Send to InfluxDB
    client.write_points(influx_data, database='morning_check_backup', time_precision='ms', batch_size=10000, protocol='line')

# Flush Sentry SDK queue if needed
sentry_sdk.flush(120)

logging.info('Script end')
