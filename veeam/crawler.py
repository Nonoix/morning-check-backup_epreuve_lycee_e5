#!/usr/bin/env python
# -*- coding: utf-8 -*-

from os import getenv, path
from sys import exit, argv
import json
from uuid import UUID

from hashlib import md5
from datetime import datetime, timezone, timedelta

import xml.etree.ElementTree as ET
import re
import logging
import pyodbc
import sentry_sdk
import hvac
from influxdb import InfluxDBClient


def before_send(event: dict, hint: dict) -> dict:
    """ Sentry - Generate a new fingerprint only based on event message """

    msg = event.get('logentry').get('message')
    if msg is not None:
        event['fingerprint'] = [md5(msg.encode('utf-8')).hexdigest()]
    return event


def backup_status_mapping(status: int) -> str:
    """ Map backup status """

    if status == -1:
        return 'Idle'
    elif status == 0:
        return 'Success'
    elif status == 1:
        return 'Warning'
    elif status == 2:
        return 'Failed'
    elif status == 3:
        return 'Warning'
    elif status == 5:
        return 'Running'
    elif status == 6:
        return 'Pending'
    else:
        logging.error(f'Unhandled backup status {status}')
        return 'Unhandled'


def jobtype_mapping(jobType: int) -> str:
    """ Map job type """

    if jobType == 0:
        return 'Backup'
    elif jobType == 1:
        return 'Replica'
    elif jobType == 28:
        return 'Backup Tape'
    elif jobType == 51 or jobType == 63 or jobType == 65:
        return 'Backup Copy'
    else:
        return 'Unknown'


def session_log_analysis(xml: str) -> tuple:
    """ Parse the XML to extract session informations """

    if xml and isinstance(xml, bytes):
        xml = xml.decode('utf-8')

    # BackupTransportMode
    san = r'\[(san)\]'
    nbd = r'\[(nbd)\]'
    hotadd = r'\[(hotadd)\]'

    # Datastores
    datastore = r'Saving \[([.a-zA-Z0-9_-]*)\] '

    # Proxies
    proxy = r'Using backup proxy ([. a-zA-Z0-9_-]*) for'
    guest_proxy = r'Using guest interaction proxy ([. a-zA-Z0-9_-]*)'

    search_san = re.findall(san, xml)
    search_nbd = re.findall(nbd, xml)
    search_hotadd = re.findall(hotadd, xml)
    ret_datastores = list(dict.fromkeys(re.findall(datastore, xml)))
    ret_proxies = list(dict.fromkeys(re.findall(proxy, xml)))
    ret_guest_proxies = list(dict.fromkeys(re.findall(guest_proxy, xml)))

    if len(search_hotadd) > 0:
        ret_BackupTransportMode = 'hotadd'
    elif len(search_nbd) > 0:
        ret_BackupTransportMode = 'nbd'
    elif len(search_san) > 0:
        ret_BackupTransportMode = 'san'
    else:
        ret_BackupTransportMode = ''

    return ret_BackupTransportMode, ret_datastores, ret_proxies, ret_guest_proxies


def job_options_analysis(xml: str) -> tuple:
    """ Parse the XML to extract job options """

    if xml and isinstance(xml, bytes):
        xml = xml.decode('utf-8')

    root = ET.fromstring(xml)

    ret_RetainDays = -1
    ret_RetainCycles = -1
    ret_EnableDeletedVmDataRetention = False

    # Maintenance activée
    if root.find('EnableDeletedVmDataRetention') is not None:
        ret_EnableDeletedVmDataRetention = True if root.find('EnableDeletedVmDataRetention').text == 'True' else False

    # Nombre de points pour la maintenance
    if ret_EnableDeletedVmDataRetention and root.find('RetainDays') is not None:
        ret_RetainDays = int(root.find('RetainDays').text)

    # Nombre de points à conserver (Dépend de RetentionType si = 1 (days) alors RetainDaysToKeep)
    if root.find('RetainCycles') is not None:
        ret_RetainCycles = int(root.find('RetainCycles').text)
    if root.find('RetainDaysToKeep') is not None:
        ret_RetainCycles = int(root.find('RetainDaysToKeep').text)

    return ret_RetainDays, ret_RetainCycles, ret_EnableDeletedVmDataRetention


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


# Main

# Initialize Sentry SDK
logging.info('Initialize Sentry SDK')

if not getenv('SENTRY_DSN'):
    logging.error('Environment variable SENTRY_DSN is not defined')
    exit(1)

sentry_sdk.init(
    getenv('SENTRY_DSN'),
    before_send=before_send,
    transport_queue_size=10000
)

# Global Variables
begin = datetime.now()
scriptPath = path.dirname(path.realpath(__file__))
OUTFILE = 'artifacts/' + getenv('CI_JOB_NAME') + '.json'

SERVER_NAME = getenv('SERVER_NAME')
DATABASE_ADDRESS = getenv('DATABASE_ADDRESS')
DATABASE_PORT = getenv('DATABASE_PORT')
DATABASE_NAME = getenv('DATABASE_NAME')

START_DATE = datetime.strftime(datetime.today() - timedelta(days=1), '%Y-%m-%d %H:%M:%S')
END_DATE = datetime.strftime(datetime.today(), '%Y-%m-%d %H:%M:%S')

sessions_tape = dict()
sessions_in_progress = dict()
sessions_failed = dict()
repositories = dict()
output = dict()

stats = {
    'backup': {
        'sessions': 0, 'success': 0, 'warning': 0, 'failed': 0, 'running': 0, 'pending': 0, 'idle': 0, 'in_progress': 0, 'undefined': 0
    },
    'tape': {
        'sessions': 0, 'success': 0, 'warning': 0, 'failed': 0, 'running': 0, 'pending': 0, 'idle': 0, 'in_progress': 0, 'undefined': 0
    },
    'repositories': 0
}

# Get SQL queries
sql_tapes = open(scriptPath + '/sql/tapes.sql', 'r').read()
sql_backups = open(scriptPath + '/sql/backups.sql', 'r').read().format(START_DATE, END_DATE)
sql_repositories = open(scriptPath + '/sql/repositories.sql', 'r').read()

# Retrieve credentials from Vault or read them from env vars
if getenv('VAULT_ADDR'):
    # Vérification des variable d'environnement
    for var in ['VAULT_ADDR', 'VAULT_TOKEN', 'VAULT_CREDENTIALS_PATH',
                'SERVER_NAME', 'DATABASE_ADDRESS', 'DATABASE_PORT',
                'DATABASE_NAME']:
        if not getenv(var):
            raise Exception(f'Required environment variable {var} is not defined')
    # Objet hvac.Client avec les valeurs des variables d'environnement.
    vault = hvac.Client(token=getenv('VAULT_TOKEN'),
                        url=getenv('VAULT_ADDR'))

    vault_res = vault.is_authenticated()
    logging.info('Vault auth res  : ' + str(vault_res))
    read_secret_veeam_result = vault.read(getenv('VAULT_CREDENTIALS_PATH'))
    # Test de présence dans Vault des information de connexion de la base de donnée
    if read_secret_veeam_result['data']['data']:
        veeam_credentials = read_secret_veeam_result['data']['data']
    else:
        raise Exception('Unable to retrieve Veeam MSSQL database credentials from Vault')

    SQL_USERNAME = veeam_credentials.get('DB_USERNAME')
    SQL_PASSWORD = veeam_credentials.get('DB_PASSWORD')
# Assignation des informations de connexions à la base de données en cas de lancement local
else:
    SQL_USERNAME = getenv('DB_USERNAME')
    SQL_PASSWORD = getenv('DB_PASSWORD')

logging.info('Script start : %s' % __file__)
logging.info('Parameters : %s' % (', '.join(argv[1:]) or 'None'))
logging.info('Output file : ' + OUTFILE)

try:

    # Connect to MSSQL server
    with pyodbc.connect(
            Driver='{ODBC Driver 17 for SQL Server}',
            Server=f'{DATABASE_ADDRESS},{DATABASE_PORT}',
            Database=DATABASE_NAME,
            UID=SQL_USERNAME,
            PWD=SQL_PASSWORD) as conn:

        # TAPES
        logging.info('Beginning of tape sessions extraction')

        # Instantiate a new cursor
        cursor = conn.cursor()

        # Execute the SQL query
        logging.info(sql_tapes)
        cursor.execute(sql_tapes)

        # Iterate tape sessions
        for session in cursor:
            backup_status_str = backup_status_mapping(session.result)

            obj_dict = dict()
            obj_dict['start_date'] = session.creation_time
            obj_dict['end_date'] = session.end_time
            obj_dict['backup_status'] = session.result
            obj_dict['backup_status_details'] = backup_status_str
            obj_dict['job_name'] = session.job_name
            obj_dict['job_id'] = session.job_id
            obj_dict['reason'] = session.reason
            obj_dict['mediapool_name'] = session.mediapool_name

            job_name = obj_dict['job_name']

            # Only keep the last session of job_name
            if sessions_tape.get(job_name):
                if sessions_tape[job_name]['start_date'] < obj_dict['start_date']:
                    sessions_tape[job_name] = obj_dict
            else:
                sessions_tape[job_name] = obj_dict

        # Calculate stats
        for job in sessions_tape:
            session = sessions_tape.get(job)
            stats['tape']['sessions'] += 1
            if session.get('backup_status') == -1:
                stats['tape']['idle'] += 1
            elif session.get('backup_status') == 0:
                stats['tape']['success'] += 1
            elif session.get('backup_status') in [1, 3]:
                stats['tape']['warning'] += 1
            elif session.get('backup_status') == 2:
                stats['tape']['failed'] += 1
            elif session.get('backup_status') == 5:
                stats['tape']['running'] += 1
            elif session.get('backup_status') == 6:
                stats['tape']['pending'] += 1
            else:
                stats['tape']['undefined'] += 1

            # Setting end_date to None for in progress sessions and counting
            if session.get('backup_status') in [-1, 5, 6]:
                sessions_tape[job]['end_date'] = None
                stats['tape']['in_progress'] += 1

        logging.info('End of tape sessions extraction')

        # BACKUP
        logging.info('Beginning of backup sessions extraction : start={}, end={}'.format(START_DATE, END_DATE))

        # Execute the SQL query
        logging.info(sql_backups)
        cursor.execute(sql_backups)

        # Iterate backup sessions
        for session in cursor:
            stats['backup']['sessions'] += 1
            backup_status_str = backup_status_mapping(session.status)

            BTM, datastores, proxies, guest_proxies = session_log_analysis(session.log_xml)

            RetainDays, RetainCycles, EnableDeletedVmDataRetention = job_options_analysis(session.options)

            obj_dict = dict()
            obj_dict['start_date'] = session.creation_time
            obj_dict['end_date'] = session.end_time
            obj_dict['session_id'] = session.session_id
            obj_dict['orig_session_id'] = session.orig_session_id
            obj_dict['backup_status'] = session.status
            obj_dict['backup_status_details'] = backup_status_str
            obj_dict['last_point_success'] = session.last_point_success
            obj_dict['object_id'] = session.object_id
            obj_dict['job_name'] = session.job_name
            obj_dict['job_id'] = session.job_id
            obj_dict['type'] = jobtype_mapping(session.job_type)
            obj_dict['reason'] = session.reason
            obj_dict['object_name'] = session.object_name.upper()
            obj_dict['backup_transport_mode'] = BTM
            obj_dict['target_storage'] = session.repository_name
            obj_dict['proxies'] = ','.join(proxies)
            obj_dict['nb_restore_points'] = session.nb_restore_points
            obj_dict['retaindays'] = RetainDays
            obj_dict['retaincycles'] = RetainCycles
            obj_dict['retention_maintenance'] = EnableDeletedVmDataRetention

            job_name = obj_dict['job_name']
            job_id = obj_dict['job_id']
            vm_name = obj_dict['object_name']

            if session.status == 2:  # Status 2 = Failed
                # Test if sessions_failed[job_name] is defined
                if sessions_failed.get(job_name):
                    # Test if sessions_failed[job_name][job_id] is defined
                    if sessions_failed.get(job_name).get(job_id):
                        # Test if sessions_failed[job_name][job_id][vm_name] is defined
                        if sessions_failed.get(job_name).get(job_id).get(vm_name):
                            # Compare if local obj_dict['start_date'] is the most recent
                            if sessions_failed[job_name][job_id][vm_name]['start_date'] < obj_dict['start_date']:
                                sessions_failed[job_name][job_id][vm_name] = obj_dict
                        else:
                            sessions_failed[job_name][job_id][vm_name] = obj_dict
                    else:
                        sessions_failed[job_name][job_id] = dict()
                        sessions_failed[job_name][job_id][vm_name] = obj_dict
                else:
                    sessions_failed[job_name] = dict()
                    sessions_failed[job_name][job_id] = dict()
                    sessions_failed[job_name][job_id][vm_name] = obj_dict
            elif session.status in [-1, 5, 6]:  # Stauts -1 = Idle, Status 5 = Running, Status 6 = Pending
                if session.status == -1:
                    stats['backup']['idle'] += 1
                    stats['backup']['in_progress'] += 1
                elif session.status == 5:
                    stats['backup']['running'] += 1
                    stats['backup']['in_progress'] += 1
                elif session.status == 6:
                    stats['backup']['pending'] += 1
                    stats['backup']['in_progress'] += 1

                # Remove from sessions_failed if found a session with status 5 or 6
                # Test if sessions_failed[job_name] is defined
                if sessions_failed.get(job_name):
                    # Test if sessions_failed[job_name][job_id] is defined
                    if sessions_failed.get(job_name).get(job_id):
                        # Test if sessions_failed[job_name][job_id][vm_name] is defined
                        if sessions_failed.get(job_name).get(job_id).get(vm_name):
                            del sessions_failed[job_name][job_id][vm_name]
                            if len(sessions_failed.get(job_name).get(job_id)) == 0:
                                del sessions_failed[job_name][job_id]
                            if len(sessions_failed.get(job_name)) == 0:
                                del sessions_failed[job_name]

                # Remove irrelevant fields for idle, running and pending sessions
                del obj_dict['end_date']
                del obj_dict['reason']

                # Test if sessions_in_progress[job_name] is defined
                if sessions_in_progress.get(job_name):
                    # Test if sessions_in_progress[job_name][job_id] is defined
                    if sessions_in_progress.get(job_name).get(job_id):
                        sessions_in_progress[job_name][job_id][vm_name] = obj_dict
                    else:
                        sessions_in_progress[job_name][job_id] = dict()
                        sessions_in_progress[job_name][job_id][vm_name] = obj_dict
                else:
                    sessions_in_progress[job_name] = dict()
                    sessions_in_progress[job_name][job_id] = dict()
                    sessions_in_progress[job_name][job_id][vm_name] = obj_dict

            elif session.status in [0, 1, 3]:  # Status 0 = Success, Status 1 or 3 = Warning
                if session.status == 0:
                    stats['backup']['success'] += 1
                elif session.status == 1 or session.status == 3:
                    stats['backup']['warning'] += 1
                # Remove from sessions_failed if found a session with status 0, 1 or 3
                # Test if sessions_failed[job_name] is defined
                if sessions_failed.get(job_name):
                    # Test if sessions_failed[job_name][job_id] is defined
                    if sessions_failed.get(job_name).get(job_id):
                        # Test if sessions_failed[job_name][job_id][vm_name] is defined
                        if sessions_failed.get(job_name).get(job_id).get(vm_name):
                            del sessions_failed[job_name][job_id][vm_name]
                            if len(sessions_failed.get(job_name).get(job_id)) == 0:
                                del sessions_failed[job_name][job_id]
                            if len(sessions_failed.get(job_name)) == 0:
                                del sessions_failed[job_name]

        # Remove job_id & calculate the number of failed sessions
        for job in sessions_failed:
            for job_id in sessions_failed.get(job):
                if sessions_failed.get(job).get(job_id):
                    for vm in sessions_failed.get(job).get(job_id):
                        stats['backup']['failed'] += 1
                    sessions_failed[job] = sessions_failed[job][job_id]

        # Remove job_id
        for job in sessions_in_progress:
            for job_id in sessions_in_progress.get(job):
                sessions_in_progress[job] = sessions_in_progress[job][job_id]

        # Calculate total number of unique sessions
        stats['backup']['total'] = int(stats['backup']['success']) + int(stats['backup']['failed']) + int(stats['backup']['warning']) + int(stats['backup']['in_progress'])

        logging.info('End of backup sessions extraction')

        # REPOSITORIES
        logging.info('Beginning of repositories informations extraction')

        # Execute the SQL query
        logging.info(sql_repositories)
        cursor.execute(sql_repositories)

        # Iterate repositories
        for repository in cursor:
            stats['repositories'] += 1
            obj_dict = dict()
            obj_dict['id'] = repository.id
            obj_dict['name'] = repository.name
            obj_dict['description'] = repository.description
            obj_dict['type'] = repository.type
            obj_dict['path'] = repository.path
            obj_dict['status'] = repository.status
            obj_dict['host_name'] = SERVER_NAME if repository.host_name == 'This server' else repository.host_name
            obj_dict['host_ip'] = repository.host_ip
            obj_dict['scale_out_name'] = repository.scale_out_name
            obj_dict['free'] = repository.freeSpace
            obj_dict['total'] = repository.totalSpace
            obj_dict['used'] = int(repository.totalSpace) - int(repository.freeSpace)

            if repository.scale_out_name:
                group_name = repository.scale_out_name
                repo_name = repository.name
                if not repositories.get(group_name):
                    repositories[group_name] = dict()
                repositories[group_name][repo_name] = obj_dict

            else:
                group_name = repository.name
                if not repositories.get(group_name):
                    repositories[group_name] = dict()
                del obj_dict['scale_out_name']
                repositories[group_name] = obj_dict

    logging.info('End of repositories informations extraction')

    output['infos'] = dict()
    output['infos']['SERVER_NAME'] = SERVER_NAME
    output['infos']['stats'] = stats

    output['sessions'] = dict()
    output['sessions']['tape'] = sessions_tape
    output['sessions']['in_progress'] = sessions_in_progress
    output['sessions']['failed'] = sessions_failed
    output['repositories'] = repositories

    # write to JSON file
    with open(OUTFILE, 'w+') as f:
        f.write(json.dumps(output, indent=4, cls=CustomJSONEncoder))
        f.close()

    delta = datetime.now() - begin

    logging.info('Tape sessions : {} [ Success = {}, Warning = {}, Failed = {}, Running = {}, Pending = {}, Idle = {}, Undefined = {}]'.format(
        stats['tape']['sessions'],
        stats['tape']['success'],
        stats['tape']['warning'],
        stats['tape']['failed'],
        stats['tape']['running'],
        stats['tape']['pending'],
        stats['tape']['idle'],
        stats['tape']['undefined']
    ))
    logging.info('Backup sessions : {} [ Success = {}, Warning = {}, Failed = {}, Running = {}, Pending = {}, Idle = {}, Undefined = {}]'.format(
        stats['backup']['sessions'],
        stats['backup']['success'],
        stats['backup']['warning'],
        stats['backup']['failed'],
        stats['backup']['running'],
        stats['backup']['pending'],
        stats['backup']['idle'],
        stats['backup']['undefined']
    ))
    logging.info('Repositories : {}'.format(stats['repositories']))

    logging.info(f'Total execution time : {str(delta.total_seconds())}')

    # Send statistics
    if getenv('DISABLE_INFLUXDB') != '1':
        logging.info('Sending stats/metrics to InfluxDB')

        # Create InfluxDB client instance
        client = InfluxDBClient(host='100.0.00.1', port=8086)  # server.adm.fr.arno.net

        # Define InfluxDB templates
        template_influx = '{},job=%s,type=crawler value={}' % getenv('CI_JOB_NAME')
        template_influx_stats = '{},job=%s,type=crawler success={},warning={},failed={},running={},pending={},idle={},undefined={},sessions={}' % getenv('CI_JOB_NAME')
        template_influx_repository = '{},job=%s,type=crawler,repo="{}",extent="{}" free={},used={},total={}' % getenv('CI_JOB_NAME')
        template_influx_scaleout = '{},job=%s,type=crawler,scaleout="{}" free={},used={},total={}' % getenv('CI_JOB_NAME')

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
            stats['backup']['idle'],
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
            stats['tape']['idle'],
            stats['tape']['undefined'],
            stats['tape']['sessions'],
        ))

        # Add repositories statistics
        for repository in repositories:
            repository_fmt = repository.replace(' ', '-')  # InfluxDB does not support spaces in metric names
            if repositories.get(repository).get('id'):
                repo = repositories.get(repository)
                influx_data.append(template_influx_repository.format('repository', repository_fmt, None, repo.get('free'), repo.get('used'), repo.get('total')))
            else:
                scaleout_free, scaleout_used, scaleout_total = 0, 0, 0
                for extent in repositories.get(repository):
                    extent_fmt = extent.replace(' ', '-')
                    repo = repositories.get(repository).get(extent)
                    influx_data.append(template_influx_repository.format('repository', repository_fmt, extent_fmt, repo.get('free'), repo.get('used'), repo.get('total')))
                    scaleout_free += repo.get('free')
                    scaleout_used += repo.get('used')
                    scaleout_total += repo.get('total')
                influx_data.append(template_influx_scaleout.format('scaleout', repository_fmt, repo.get('free'), repo.get('used'), repo.get('total')))

        # Send to InfluxDB
        client.write_points(influx_data, database='morning_check_backup', time_precision='ms', batch_size=10000, protocol='line')

except Exception as e:
    print(e)
    sentry_sdk.capture_exception(e)
    exit(1)

sentry_sdk.flush(120)

logging.info('Script end')
