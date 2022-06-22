#!/opt/sysadmws/misc/shebang_python_switcher.sh -u
# -*- coding: utf-8 -*-

# -u - for unbuffered output https://docs.python.org/2/using/cmdline.html#cmdoption-u
# Otherwise output is getting mixed in pipelines

import os
import sys
import yaml
import textwrap
import logging
from logging.handlers import RotatingFileHandler
import argparse
from datetime import datetime
import socket
import lockfile
import time
import subprocess

# Constants
LOGO="rsnapshot_backup"
WORK_DIR = "/opt/sysadmws/rsnapshot_backup"
CONFIG_FILE = "rsnapshot_backup.yaml"
LOG_DIR = "/opt/sysadmws/rsnapshot_backup/log"
LOG_FILE = "rsnapshot_backup.log"
SELF_HOSTNAME = socket.gethostname()
# Keep lock file in /run tmpfs - for stale lock file cleanup on reboot
LOCK_FILE = "/run/rsnapshot_backup"
RSNAPSHOT_CONF = "/opt/sysadmws/rsnapshot_backup/rsnapshot.conf"
RSNAPSHOT_PASSWD = "/opt/sysadmws/rsnapshot_backup/rsnapshot.passwd"

# Functions

def log_and_print(kind, text, logger):
    # Replace words that trigger error detection in pipelines
    text_safe = text.replace("False", "F_alse")
    if kind == "NOTICE":
        logger.info(text)
    if kind == "ERROR":
        logger.info(text)
    sys.stderr.write(datetime.now().strftime("%F %T"))
    sys.stderr.write(" {kind}: ".format(kind=kind))
    sys.stderr.write(text_safe)
    sys.stderr.write("\n")

def run_cmd(cmd):

    process = subprocess.Popen(cmd, shell=True, executable="/bin/bash")
    try:
        process.communicate(None)
    except:
        process.kill()
        process.wait()
        raise

    retcode = process.poll()

    return retcode

def run_cmd_pipe(cmd):

    process = subprocess.Popen(cmd, shell=True, executable="/bin/bash", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(None)
    except:
        process.kill()
        process.wait()
        raise

    retcode = process.poll()
    stdout = stdout.decode()
    stderr = stderr.decode()

    return retcode, stdout, stderr

# Main

if __name__ == "__main__":

    # Set default encoding for python 2.x (no need in python 3.x)
    if sys.version_info[0] < 3:
        reload(sys)
        sys.setdefaultencoding("utf-8")

    # Set logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    if not os.path.isdir(LOG_DIR):
        os.mkdir(LOG_DIR, 0o755)
    log_handler = RotatingFileHandler("{0}/{1}".format(LOG_DIR, LOG_FILE), maxBytes=10485760, backupCount=10, encoding="utf-8")
    os.chmod("{0}/{1}".format(LOG_DIR, LOG_FILE), 0o600)
    log_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    formatter = logging.Formatter(fmt='%(asctime)s %(filename)s %(name)s %(process)d/%(threadName)s %(levelname)s: %(message)s', datefmt="%Y-%m-%d %H:%M:%S %Z")
    log_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(log_handler)
    logger.addHandler(console_handler)

    # Set parser and parse args

    parser = argparse.ArgumentParser(description="{LOGO} functions.".format(LOGO=LOGO))

    parser.add_argument("--debug", dest="debug", help="enable debug", action="store_true")
    parser.add_argument("--config", dest="config", help="override config")
    parser.add_argument("--item-number", dest="item_number", help="run only for config item NUMBER", nargs=1, metavar=("NUMBER"))
    parser.add_argument("--host", dest="host", help="run only for items with HOST", nargs=1, metavar=("HOST"))

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", dest="sync", help="prepare rsnapshot configs and run sync, we use sync_first rsnapshot option", action="store_true")
    group.add_argument("--rotate-hourly", dest="rotate_hourly", help="prepare rsnapshot configs and run hourly rotate", action="store_true")
    group.add_argument("--rotate-daily", dest="rotate_daily", help="prepare rsnapshot configs and run daily rotate", action="store_true")
    group.add_argument("--rotate-weekly", dest="rotate_weekly", help="prepare rsnapshot configs and run weekly rotate", action="store_true")
    group.add_argument("--rotate-monthly", dest="rotate_monthly", help="prepare rsnapshot configs and run monthly rotate", action="store_true")
    #TBD: group.add_argument("--check", dest="check", help="run checks for rsnapshot backups", action="store_true")

    if len(sys.argv) > 1:
        args = parser.parse_args()
    else:
        parser.print_help()
        sys.exit(1)

    # Enable debug
    if args.debug:
        console_handler.setLevel(logging.DEBUG)

    # Catch exception to logger
    try:

        # Load YAML config
        if args.config:
            # Override config
            logger.info("Loading YAML config {config_file}".format(config_file=args.config))
            with open("{config_file}".format(config_file=args.config), 'r') as yaml_file:
                config = yaml.load(yaml_file, Loader=yaml.SafeLoader)
        else:
            logger.info("Loading YAML config {work_dir}/{config_file}".format(work_dir=WORK_DIR, config_file=CONFIG_FILE))
            with open("{work_dir}/{config_file}".format(work_dir=WORK_DIR, config_file=CONFIG_FILE), 'r') as yaml_file:
                config = yaml.load(yaml_file, Loader=yaml.SafeLoader)

        # Check if enabled in config
        if config["enabled"] != True:
            logger.info("{LOGO} not enabled in config, exiting".format(LOGO=LOGO))
            sys.exit(0)

        log_and_print("NOTICE", "Starting {LOGO} on {hostname}".format(LOGO=LOGO, hostname=SELF_HOSTNAME), logger)

        # Chdir to work dir
        os.chdir(WORK_DIR)

        # Lock before trying to run, exception and exit on timeout is ok
        lock = lockfile.LockFile(LOCK_FILE)
        try:

            # timeout=0 = do not wait if locked
            lock.acquire(timeout=0)

            errors = 0
            paths_processed = []

            # Loop backup items
            for item in config["items"]:

                if not item["enabled"]:
                    continue

                # Apply filters
                if args.item_number is not None:
                    if str(item["number"]) != str(args.item_number[0]):
                        continue
                if args.host is not None:
                    if item["host"] != str(args.host[0]):
                        continue

                # Backup items errors should not stop other items
                try:

                    log_and_print("NOTICE", "Processing item number {number}: {item}".format(number=item["number"], item=item), logger)

                    # Item defaults

                    if "retain_daily" not in item:
                        item["retain_daily"] = 7
                    if "retain_weekly" not in item:
                        item["retain_weekly"] = 4
                    if "retain_monthly" not in item:
                        item["retain_monthly"] = 3

                    if "connect_user" not in item:
                        item["connect_user"] = "root"

                    if "validate_hostname" not in item:
                        item["validate_hostname"] = True

                    if "mysql_noevents" not in item:
                        item["mysql_noevents"] = False
                    if "postgresql_noclean" not in item:
                        item["postgresql_noclean"] = False

                    if "native_txt_check" not in item:
                        item["native_txt_check"] = False
                    if "native_10h_limit" not in item:
                        item["native_10h_limit"] = False

                    if args.debug:
                        item["verbosity_level"] = 5
                        item["rsync_verbosity_args"] = "--human-readable --progress"
                    else:
                        item["verbosity_level"] = 2
                        item["rsync_verbosity_args"] = ""

                    if "rsync_args" not in item:
                        item["rsync_args"] = ""

                    if "mysql_dump_dir" not in item:
                        item["mysql_dump_dir"] = "/var/backups/mysql"
                    if "postgresql_dump_dir" not in item:
                        item["postgresql_dump_dir"] = "/var/backups/postgresql"
                    if "mongodb_dump_dir" not in item:
                        item["mongodb_dump_dir"] = "/var/backups/mongodb"

                    if "mysqldump_args" not in item:
                        item["mysqldump_args"] = ""
                    if "pg_dump_args" not in item:
                        item["pg_dump_args"] = ""
                    if "mongo_args" not in item:
                        item["mongo_args"] = ""

                    if "xtrabackup_throttle" not in item:
                        item["xtrabackup_throttle"] = "20" # 20 MB IO limit by default https://www.percona.com/doc/percona-xtrabackup/2.3/advanced/throttling_backups.html
                    if "xtrabackup_parallel" not in item:
                        item["xtrabackup_parallel"] = "2"
                    if "xtrabackup_compress_threads" not in item:
                        item["xtrabackup_compress_threads"] = "2"
                    if "xtrabackup_args" not in item:
                        item["xtrabackup_args"] = ""

                    # Check before_backup_check and skip item if failed
                    # It is needed for both rotations and sync
                    if "before_backup_check" in item:
                        log_and_print("NOTICE", "Executing local before_backup_check on item number {number}:".format(number=item["number"]), logger)
                        log_and_print("NOTICE", "{cmd}".format(cmd=item["before_backup_check"]), logger)
                        try:
                            retcode = run_cmd(item["before_backup_check"])
                            if retcode == 0:
                                log_and_print("NOTICE", "Local execution of before_backup_check succeeded on item number {number}".format(number=item["number"]), logger)
                            else:
                                log_and_print("ERROR", "Local execution of before_backup_check failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                errors += 1
                                continue
                        except Exception as e:
                            logger.exception(e)
                            raise Exception("Caught exception on subprocess.run execution")

                    # Rotations
                    if args.rotate_hourly or args.rotate_daily or args.rotate_weekly or args.rotate_monthly:

                        if args.rotate_hourly:
                            rsnapshot_command = "hourly"
                        if args.rotate_daily:
                            rsnapshot_command = "daily"
                        if args.rotate_weekly:
                            rsnapshot_command = "weekly"
                        if args.rotate_monthly:
                            rsnapshot_command = "monthly"

                        # Process paths from many items only once on rotations
                        if item["path"] in paths_processed:
                            log_and_print("NOTICE", "Path {path} on item number {number} already rotated, skipping".format(path=item["path"], number=item["number"]), logger)
                            continue
                        paths_processed.append(item["path"])

                        with open(RSNAPSHOT_CONF, "w") as file_to_write:
                            file_to_write.write(textwrap.dedent(
                                """\
                                config_version	1.2
                                snapshot_root	{snapshot_root}
                                cmd_cp		/bin/cp
                                cmd_rm		/bin/rm
                                cmd_rsync	/usr/bin/rsync
                                cmd_ssh		/usr/bin/ssh
                                cmd_logger	/usr/bin/logger
                                {retain_hourly_comment}retain		hourly	{retain_hourly}
                                retain		daily	{retain_daily}
                                retain		weekly	{retain_weekly}
                                retain		monthly	{retain_monthly}
                                verbose		{verbosity_level}
                                loglevel	3
                                logfile		/opt/sysadmws/rsnapshot_backup/rsnapshot.log
                                lockfile	/opt/sysadmws/rsnapshot_backup/rsnapshot.pid
                                sync_first	1
                                # any backup definition enough for rotation
                                backup		/etc/		rsnapshot/
                                """
                            ).format(
                                snapshot_root=item["path"],
                                retain_hourly_comment="" if "retain_hourly" in item else "#",
                                retain_hourly=item["retain_hourly"] if "retain_hourly" in item else "NONE",
                                retain_daily=item["retain_daily"],
                                retain_weekly=item["retain_weekly"],
                                retain_monthly=item["retain_monthly"],
                                verbosity_level=item["verbosity_level"]
                            ))
                        
                        # Run rsnapshot
                        log_and_print("NOTICE", "Running rsnapshot {command} on item number {number}".format(command=rsnapshot_command, number=item["number"]), logger)
                        try:
                            retcode = run_cmd("rsnapshot -c {conf} {command}".format(conf=RSNAPSHOT_CONF, command=rsnapshot_command))
                            if retcode == 0:
                                log_and_print("NOTICE", "Rsnapshot succeeded on item number {number}".format(number=item["number"]), logger)
                            else:
                                log_and_print("ERROR", "Rsnapshot failed on item number {number}".format(number=item["number"]), logger)
                                errors += 1
                        except Exception as e:
                            logger.exception(e)
                            raise Exception("Caught exception on subprocess.run execution")
                    
                    # Sync
                    if args.sync:

                        if item["type"] in ["RSYNC_SSH", "MYSQL_SSH", "POSTGRESQL_SSH", "MONGODB_SSH"]:

                            ssh_args = "-o BatchMode=yes -o StrictHostKeyChecking=no"

                            if ":" in item["connect"]:
                                item["connect_host"] = item["connect"].split(":")[0]
                                item["connect_port"] = item["connect"].split(":")[1]
                            else:
                                item["connect_host"] = item["connect"]
                                item["connect_port"] = 22
                            
                            # Check SSH

                            log_and_print("NOTICE", "Checking remote SSH on item number {number}:".format(number=item["number"]), logger)
                            try:
                                retcode = run_cmd("ssh {ssh_args} -p {port} {user}@{host} 'hostname'".format(ssh_args=ssh_args, port=item["connect_port"], user=item["connect_user"], host=item["connect_host"]))
                                if retcode == 0:
                                    log_and_print("NOTICE", "SSH without password succeeded on item number {number}".format(number=item["number"]), logger)
                                else:

                                    if item["host"] == SELF_HOSTNAME:

                                        log_and_print("NOTICE", "Loopback connect detected on item number {number}, trying to add server key to authorized".format(number=item["number"]), logger)
                                        script = textwrap.dedent(
                                            """\
                                            #!/bin/bash
                                            set -e
                                            
                                            if [[ ! -e /root/.ssh/id_rsa.pub ]]; then
                                                    ssh-keygen -b 4096 -f /root/.ssh/id_rsa -q -N ''
                                            fi
                                            
                                            if [[ ! -e /root/.ssh/authorized_keys ]]; then
                                                    cat /root/.ssh/id_rsa.pub >> /root/.ssh/authorized_keys
                                                    chmod 600 /root/.ssh/authorized_keys
                                            fi
                                            
                                            if ! grep -q "$(cat /root/.ssh/id_rsa.pub)" /root/.ssh/authorized_keys; then
                                                    cat /root/.ssh/id_rsa.pub >> /root/.ssh/authorized_keys
                                                    chmod 600 /root/.ssh/authorized_keys
                                            fi
                                            """
                                        )
                                        try:
                                            retcode = run_cmd(script)
                                            if retcode == 0:
                                                log_and_print("NOTICE", "Loopback authorization script succeeded on item number {number}".format(number=item["number"]), logger)
                                            else:
                                                log_and_print("ERROR", "Loopback authorization script failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                                errors += 1
                                                continue
                                        except Exception as e:
                                            logger.exception(e)
                                            raise Exception("Caught exception on subprocess.run execution")
                                        
                                        log_and_print("NOTICE", "Checking again remote SSH on item number {number}:".format(number=item["number"]), logger)
                                        try:
                                            retcode = run_cmd("ssh {ssh_args} -p {port} {user}@{host} 'hostname'".format(ssh_args=ssh_args, port=item["connect_port"], user=item["connect_user"], host=item["connect_host"]))
                                            if retcode == 0:
                                                log_and_print("NOTICE", "SSH without password succeeded on item number {number}".format(number=item["number"]), logger)
                                            else:
                                                log_and_print("ERROR", "SSH without password failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                                errors += 1
                                                continue
                                        except Exception as e:
                                            logger.exception(e)
                                            raise Exception("Caught exception on subprocess.run execution")

                                    else:
                                        log_and_print("ERROR", "SSH without password failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                        errors += 1
                                        continue
                            
                            except Exception as e:
                                logger.exception(e)
                                raise Exception("Caught exception on subprocess.run execution")

                            # Validate hostname
                            if item["validate_hostname"]:
                                log_and_print("NOTICE", "Hostname validation required on item number {number}".format(number=item["number"]), logger)
                            try:
                                retcode, stdout, stderr = run_cmd_pipe("ssh {ssh_args} -p {port} {user}@{host} 'hostname'".format(ssh_args=ssh_args, port=item["connect_port"], user=item["connect_user"], host=item["connect_host"]))
                                if retcode == 0:
                                    hostname_received = stdout.lstrip().rstrip()
                                    if hostname_received == item["host"]:
                                        log_and_print("NOTICE", "Remote hostname {hostname} received and validated on item number {number}".format(hostname=hostname_received, number=item["number"]), logger)
                                    else:
                                        log_and_print("ERROR", "Remote hostname {hostname} received, {expected} expected and validation failed on item number {number}, not doing sync".format(hostname=hostname_received, expected=item["host"], number=item["number"]), logger)
                                        errors += 1
                                        continue
                                else:
                                    log_and_print("ERROR", "Remote hostname validation failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                    errors += 1
                                    continue
                            except Exception as e:
                                logger.exception(e)
                                raise Exception("Caught exception on subprocess.run execution")

                            # Exec exec_before_rsync
                            if "exec_before_rsync" in item:
                                log_and_print("NOTICE", "Executing remote exec_before_rsync on item number {number}".format(number=item["number"]), logger)
                                log_and_print("NOTICE", "{cmd}".format(cmd=item["exec_before_rsync"]), logger)
                                try:
                                    retcode = run_cmd("ssh {ssh_args} -p {port} {user}@{host} '{cmd}'".format(ssh_args=ssh_args, port=item["connect_port"], user=item["connect_user"], host=item["connect_host"], cmd=item["exec_before_rsync"]))
                                    if retcode == 0:
                                        log_and_print("NOTICE", "Remote execution of exec_before_rsync succeeded on item number {number}".format(number=item["number"]), logger)
                                    else:
                                        log_and_print("ERROR", "Remote execution of exec_before_rsync failed on item number {number}, but script continues".format(number=item["number"]), logger)
                                        errors += 1
                                except Exception as e:
                                    logger.exception(e)
                                    raise Exception("Caught exception on subprocess.run execution")

                            # DB dumps before rsync

                            if item["type"] in ["MYSQL_SSH", "POSTGRESQL_SSH", "MONGODB_SSH"]:

                                # Generic grep filter for excludes
                                if "exclude" in item:
                                    grep_db_filter = "| grep -v"
                                    for db_to_exclude in item["exclude"]:
                                        grep_db_filter += " -e {db_to_exclude}".format(db_to_exclude=db_to_exclude)
                                else:
                                    grep_db_filter = ""

                                if item["type"] == "MYSQL_SSH":

                                    if "mysql_dump_type" in item and item["mysql_dump_type"] == "xtrabackup":

                                        if "exclude" in item:
                                            databases_exclude = "--databases-exclude=\""
                                            databases_exclude += " ".join(item["exclude"])
                                            databases_exclude += "\""
                                        else:
                                            databases_exclude = ""

                                        if item["source"] == "ALL":
                                            script_dump_part = textwrap.dedent(
                                                """\
                                                if [[ ! -d {mysql_dump_dir}/all.xtrabackup ]]; then
                                                        xtrabackup --backup --compress --throttle={xtrabackup_throttle} --parallel={xtrabackup_parallel} --compress-threads={xtrabackup_compress_threads} --target-dir={mysql_dump_dir}/all.xtrabackup {databases_exclude} {xtrabackup_args} 2>&1 | grep -v -e "log scanned up to" -e "Skipping"
                                                fi
                                                """
                                            ).format(
                                                xtrabackup_throttle=item["xtrabackup_throttle"],
                                                xtrabackup_parallel=item["xtrabackup_parallel"],
                                                xtrabackup_compress_threads=item["xtrabackup_compress_threads"],
                                                mysql_dump_dir=item["mysql_dump_dir"],
                                                databases_exclude=databases_exclude,
                                                xtrabackup_args=item["xtrabackup_args"]
                                            )
                                        else:
                                            script_dump_part = textwrap.dedent(
                                                """\
                                                if [[ ! -d {mysql_dump_dir}/{source}.xtrabackup ]]; then
                                                        xtrabackup --backup --compress --throttle={xtrabackup_throttle} --parallel={xtrabackup_parallel} --compress-threads={xtrabackup_compress_threads} --target-dir={mysql_dump_dir}/{source}.xtrabackup --databases={source} {xtrabackup_args} 2>&1 | grep -v -e "log scanned up to" -e "Skipping"
                                                fi
                                                """
                                            ).format(
                                                xtrabackup_throttle=item["xtrabackup_throttle"],
                                                xtrabackup_parallel=item["xtrabackup_parallel"],
                                                xtrabackup_compress_threads=item["xtrabackup_compress_threads"],
                                                mysql_dump_dir=item["mysql_dump_dir"],
                                                source=item["source"],
                                                xtrabackup_args=item["xtrabackup_args"]
                                            )

                                        # If hourly retains are used keep dumps only for 59 minutes
                                        script = textwrap.dedent(
                                            """\
                                            #!/bin/bash
                                            set -e

                                            ssh {ssh_args} -p {port} {user}@{host} '
                                                set -x
                                                set -e
                                                set -o pipefail
                                                mkdir -p {mysql_dump_dir}
                                                chmod 700 {mysql_dump_dir}
                                                while [[ -d {mysql_dump_dir}/dump.lock ]]; do
                                                        sleep 5
                                                done
                                                mkdir {mysql_dump_dir}/dump.lock
                                                trap "rm -rf {mysql_dump_dir}/dump.lock" 0
                                                cd {mysql_dump_dir}
                                                find {mysql_dump_dir} -type d -name "*.xtrabackup" -mmin +{mmin} -exec rm -rf {{}} +
                                                {script_dump_part}
                                            '
                                            """
                                        ).format(
                                            ssh_args=ssh_args,
                                            port=item["connect_port"],
                                            user=item["connect_user"],
                                            host=item["connect_host"],
                                            mysql_dump_dir=item["mysql_dump_dir"],
                                            mmin="59" if "retain_hourly" in item else "720",
                                            script_dump_part=script_dump_part
                                        )

                                    else:

                                        if item["source"] == "ALL":
                                            script_dump_part = textwrap.dedent(
                                                """\
                                                mysql --defaults-file=/etc/mysql/debian.cnf --skip-column-names --batch -e "SHOW DATABASES;" | grep -v -e information_schema -e performance_schema {grep_db_filter} > {mysql_dump_dir}/db_list.txt
                                                for db in $(cat {mysql_dump_dir}/db_list.txt); do
                                                        if [[ ! -f {mysql_dump_dir}/$db.gz ]]; then
                                                                mysqldump --defaults-file=/etc/mysql/debian.cnf --force --opt --single-transaction --quick --skip-lock-tables {mysql_events} --databases $db {mysqldump_args} --max_allowed_packet=1G | gzip > {mysql_dump_dir}/$db.gz
                                                        fi
                                                done
                                                """
                                            ).format(
                                                mysql_dump_dir=item["mysql_dump_dir"],
                                                mysql_events="" if item["mysql_noevents"] else "--events",
                                                mysqldump_args=item["mysqldump_args"],
                                                grep_db_filter=grep_db_filter
                                            )
                                        else:
                                            script_dump_part = textwrap.dedent(
                                                """\
                                                if [[ ! -f {mysql_dump_dir}/{source}.gz ]]; then
                                                        mysqldump --defaults-file=/etc/mysql/debian.cnf --force --opt --single-transaction --quick --skip-lock-tables {mysql_events} --databases {source} {mysqldump_args} --max_allowed_packet=1G | gzip > {mysql_dump_dir}/{source}.gz
                                                fi
                                                """
                                            ).format(
                                                mysql_dump_dir=item["mysql_dump_dir"],
                                                mysql_events="" if item["mysql_noevents"] else "--events",
                                                mysqldump_args=item["mysqldump_args"],
                                                grep_db_filter=grep_db_filter,
                                                source=item["source"]
                                            )

                                        # If hourly retains are used keep dumps only for 59 minutes
                                        script = textwrap.dedent(
                                            """\
                                            #!/bin/bash
                                            set -e

                                            ssh {ssh_args} -p {port} {user}@{host} '
                                                set -x
                                                set -e
                                                set -o pipefail
                                                mkdir -p {mysql_dump_dir}
                                                chmod 700 {mysql_dump_dir}
                                                while [[ -d {mysql_dump_dir}/dump.lock ]]; do
                                                        sleep 5
                                                done
                                                mkdir {mysql_dump_dir}/dump.lock
                                                trap "rm -rf {mysql_dump_dir}/dump.lock" 0
                                                cd {mysql_dump_dir}
                                                find {mysql_dump_dir} -type f -name "*.gz" -mmin +{mmin} -delete
                                                {script_dump_part}
                                            '
                                            """
                                        ).format(
                                            ssh_args=ssh_args,
                                            port=item["connect_port"],
                                            user=item["connect_user"],
                                            host=item["connect_host"],
                                            mysql_dump_dir=item["mysql_dump_dir"],
                                            mmin="59" if "retain_hourly" in item else "720",
                                            script_dump_part=script_dump_part
                                        )

                                if item["type"] == "POSTGRESQL_SSH":

                                    if item["source"] == "ALL":
                                        script_dump_part = textwrap.dedent(
                                            """\
                                            su - postgres -c "echo SELECT datname FROM pg_database | psql --no-align -t template1" {grep_db_filter} > {postgresql_dump_dir}/db_list.txt
                                            for db in $(cat {postgresql_dump_dir}/db_list.txt); do
                                                    if [[ ! -f {postgresql_dump_dir}/$db.gz ]]; then
                                                            su - postgres -c "pg_dump --create {postgresql_clean} {pg_dump_args} --verbose $db 2>/dev/null" | gzip > {postgresql_dump_dir}/$db.gz
                                                    fi
                                            done
                                            """
                                        ).format(
                                            postgresql_dump_dir=item["postgresql_dump_dir"],
                                            postgresql_clean="" if item["postgresql_noclean"] else "--clean",
                                            pg_dump_args=item["pg_dump_args"],
                                            grep_db_filter=grep_db_filter
                                        )
                                    else:
                                        script_dump_part = textwrap.dedent(
                                            """\
                                            if [[ ! -f {postgresql_dump_dir}/{source}.gz ]]; then
                                                    su - postgres -c "pg_dump --create {postgresql_clean} {pg_dump_args} --verbose {source} 2>/dev/null" | gzip > {postgresql_dump_dir}/{source}.gz
                                            fi
                                            """
                                        ).format(
                                            postgresql_dump_dir=item["postgresql_dump_dir"],
                                            postgresql_clean="" if item["postgresql_noclean"] else "--clean",
                                            pg_dump_args=item["pg_dump_args"],
                                            grep_db_filter=grep_db_filter,
                                            source=item["source"]
                                        )

                                    # If hourly retains are used keep dumps only for 59 minutes
                                    script = textwrap.dedent(
                                        """\
                                        #!/bin/bash
                                        set -e

                                        ssh {ssh_args} -p {port} {user}@{host} '
                                            set -x
                                            set -e
                                            set -o pipefail
                                            mkdir -p {postgresql_dump_dir}
                                            chmod 700 {postgresql_dump_dir}
                                            while [[ -d {postgresql_dump_dir}/dump.lock ]]; do
                                                    sleep 5
                                            done
                                            mkdir {postgresql_dump_dir}/dump.lock
                                            trap "rm -rf {postgresql_dump_dir}/dump.lock" 0
                                            cd {postgresql_dump_dir}
                                            find {postgresql_dump_dir} -type f -name "*.gz" -mmin +{mmin} -delete
                                            su - postgres -c "pg_dumpall --clean --schema-only --verbose 2>/dev/null" | gzip > {postgresql_dump_dir}/globals.gz
                                            {script_dump_part}
                                        '
                                        """
                                    ).format(
                                        ssh_args=ssh_args,
                                        port=item["connect_port"],
                                        user=item["connect_user"],
                                        host=item["connect_host"],
                                        postgresql_dump_dir=item["postgresql_dump_dir"],
                                        mmin="59" if "retain_hourly" in item else "720",
                                        script_dump_part=script_dump_part
                                    )

                                if item["type"] == "MONGODB_SSH":

                                    if item["source"] == "ALL":
                                        script_dump_part = textwrap.dedent(
                                            """\
                                            echo show dbs | mongo --quiet {mongo_args} | cut -f1 -d" " | grep -v -e local {grep_db_filter} > {mongodb_dump_dir}/db_list.txt
                                            for db in $(cat {mongodb_dump_dir}/db_list.txt); do
                                                    if [[ ! -f {mongodb_dump_dir}/$db.tar.gz ]]; then
                                                            mongodump --quiet {mongo_args} --out {mongodb_dump_dir} --dumpDbUsersAndRoles --db $db
                                                            cd {mongodb_dump_dir}
                                                            tar zcvf {mongodb_dump_dir}/$db.tar.gz $db
                                                            rm -rf {mongodb_dump_dir}/$db
                                                    fi
                                            done
                                            """
                                        ).format(
                                            mongodb_dump_dir=item["mongodb_dump_dir"],
                                            mongo_args=item["mongo_args"],
                                            grep_db_filter=grep_db_filter
                                        )
                                    else:
                                        script_dump_part = textwrap.dedent(
                                            """\
                                            if [[ ! -f {mongodb_dump_dir}/{source}.tar.gz ]]; then
                                                    mongodump --quiet {mongo_args} --out {mongodb_dump_dir} --dumpDbUsersAndRoles --db {source}
                                                    cd {mongodb_dump_dir}
                                                    tar zcvf {mongodb_dump_dir}/{source}.tar.gz {source}
                                                    rm -rf {mongodb_dump_dir}/{source}
                                            fi
                                            """
                                        ).format(
                                            mongodb_dump_dir=item["mongodb_dump_dir"],
                                            mongo_args=item["mongo_args"],
                                            grep_db_filter=grep_db_filter,
                                            source=item["source"]
                                        )

                                    # If hourly retains are used keep dumps only for 59 minutes
                                    script = textwrap.dedent(
                                        """\
                                        #!/bin/bash
                                        set -e

                                        ssh {ssh_args} -p {port} {user}@{host} '
                                            set -x
                                            set -e
                                            set -o pipefail
                                            mkdir -p {mongodb_dump_dir}
                                            chmod 700 {mongodb_dump_dir}
                                            while [[ -d {mongodb_dump_dir}/dump.lock ]]; do
                                                    sleep 5
                                            done
                                            mkdir {mongodb_dump_dir}/dump.lock
                                            trap "rm -rf {mongodb_dump_dir}/dump.lock" 0
                                            cd {mongodb_dump_dir}
                                            find {mongodb_dump_dir} -type f -name "*.tar.gz" -mmin +{mmin} -delete
                                            {script_dump_part}
                                        '
                                        """
                                    ).format(
                                        ssh_args=ssh_args,
                                        port=item["connect_port"],
                                        user=item["connect_user"],
                                        host=item["connect_host"],
                                        mongodb_dump_dir=item["mongodb_dump_dir"],
                                        mmin="59" if "retain_hourly" in item else "720",
                                        script_dump_part=script_dump_part
                                    )

                                log_and_print("NOTICE", "Running remote dump on item number {number}:".format(number=item["number"]), logger)
                                try:
                                    retcode = run_cmd(script)
                                    if retcode == 0:
                                        log_and_print("NOTICE", "Remote dump succeeded on item number {number}".format(number=item["number"]), logger)
                                    else:
                                        log_and_print("ERROR", "Remote dump failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                        errors += 1
                                        continue
                                except Exception as e:
                                    logger.exception(e)
                                    raise Exception("Caught exception on subprocess.run execution")

                                # Remove partially downloaded dumps
                                log_and_print("NOTICE", "Removing partially downloaded dummps if any on item number {number}:".format(number=item["number"]), logger)
                                if item["type"] == "MYSQL_SSH":
                                    if "mysql_dump_type" in item and item["mysql_dump_type"] == "xtrabackup":
                                        # For now we don't know what to do with xtrabackup
                                        script = textwrap.dedent(
                                            """\
                                            #!/bin/bash
                                            set -e
                                            #empty
                                            """
                                        ).format(
                                            snapshot_root=item["path"],
                                            mysql_dump_dir=item["mysql_dump_dir"]
                                        )
                                    else:
                                        script = textwrap.dedent(
                                            """\
                                            #!/bin/bash
                                            set -e
                                            rm -f {snapshot_root}/.sync/rsnapshot{mysql_dump_dir}/.*.gz.*
                                            """
                                        ).format(
                                            snapshot_root=item["path"],
                                            mysql_dump_dir=item["mysql_dump_dir"]
                                        )
                                if item["type"] == "POSTGRESQL_SSH":
                                    script = textwrap.dedent(
                                        """\
                                        #!/bin/bash
                                        set -e
                                        rm -f {snapshot_root}/.sync/rsnapshot{postgresql_dump_dir}/.*.gz.*
                                        """
                                    ).format(
                                        snapshot_root=item["path"],
                                        postgresql_dump_dir=item["postgresql_dump_dir"]
                                    )
                                if item["type"] == "MONGODB_SSH":
                                    script = textwrap.dedent(
                                        """\
                                        #!/bin/bash
                                        set -e
                                        rm -f {snapshot_root}/.sync/rsnapshot{mongodb_dump_dir}/.*.tar.gz.*
                                        """
                                    ).format(
                                        snapshot_root=item["path"],
                                        mongodb_dump_dir=item["mongodb_dump_dir"]
                                    )
                                try:
                                    retcode = run_cmd(script)
                                    if retcode == 0:
                                        log_and_print("NOTICE", "Removing partially downloaded dummps command succeeded on item number {number}".format(number=item["number"]), logger)
                                    else:
                                        log_and_print("ERROR", "Removing partially downloaded dummps command failed on item number {number}, but script continues".format(number=item["number"]), logger)
                                        errors += 1
                                except Exception as e:
                                    logger.exception(e)
                                    raise Exception("Caught exception on subprocess.run execution")

                            # Populate backup lines in config

                            conf_backup_line_template = textwrap.dedent(
                                """\
                                backup		{user}@{host}:{source}/	rsnapshot/{tab_before_rsync_long_args}{rsync_long_args}
                                """
                            )
                            conf_backup_lines = ""

                            if item["type"] == "RSYNC_SSH":

                                data_expand = {
                                    "UBUNTU": ["/etc","/home","/root","/var/spool/cron","/var/lib/dpkg","/usr/local","/opt/sysadmws"],
                                    "DEBIAN": ["/etc","/home","/root","/var/spool/cron","/var/lib/dpkg","/usr/local","/opt/sysadmws"],
                                    "CENTOS": ["/etc","/home","/root","/var/spool/cron","/var/lib/rpm","/usr/local","/opt/sysadmws"]
                                }

                                if item["source"] in data_expand:
                                    for source in data_expand[item["source"]]:
                                        if not ("exclude" in item and source in item["exclude"]):
                                            conf_backup_lines += conf_backup_line_template.format(
                                                user=item["connect_user"],
                                                host=item["connect_host"],
                                                source=source,
                                                tab_before_rsync_long_args="\t" if source == "/opt/sysadmws" else "",
                                                rsync_long_args="+rsync_long_args=--exclude=/opt/sysadmws/bulk_log --exclude=log" if source == "/opt/sysadmws" else ""
                                            )
                                else:
                                    conf_backup_lines += conf_backup_line_template.format(
                                        user=item["connect_user"],
                                        host=item["connect_host"],
                                        source=item["source"],
                                        tab_before_rsync_long_args="",
                                        rsync_long_args=""
                                    )

                            if item["type"] == "MYSQL_SSH":
                                # We do not need rsync compression as xtrabackup dumps are already compressed
                                # With compress it takes 10-12 times longer
                                conf_backup_lines += conf_backup_line_template.format(
                                    user=item["connect_user"],
                                    host=item["connect_host"],
                                    source=item["mysql_dump_dir"],
                                    tab_before_rsync_long_args="\t" if "mysql_dump_type" in item and item["mysql_dump_type"] == "xtrabackup" else "",
                                    rsync_long_args="+rsync_long_args=--no-compress" if "mysql_dump_type" in item and item["mysql_dump_type"] == "xtrabackup" else ""
                                )
                            if item["type"] == "POSTGRESQL_SSH":
                                conf_backup_lines += conf_backup_line_template.format(
                                    user=item["connect_user"],
                                    host=item["connect_host"],
                                    source=item["postgresql_dump_dir"],
                                    tab_before_rsync_long_args="",
                                    rsync_long_args=""
                                )
                            if item["type"] == "MONGODB_SSH":
                                conf_backup_lines += conf_backup_line_template.format(
                                    user=item["connect_user"],
                                    host=item["connect_host"],
                                    source=item["mongodb_dump_dir"],
                                    tab_before_rsync_long_args="",
                                    rsync_long_args=""
                                )

                            # Save config
                            with open(RSNAPSHOT_CONF, "w") as file_to_write:
                                file_to_write.write(textwrap.dedent(
                                    """\
                                    config_version	1.2
                                    snapshot_root	{snapshot_root}
                                    cmd_cp		/bin/cp
                                    cmd_rm		/bin/rm
                                    cmd_rsync	/usr/bin/rsync
                                    cmd_ssh		/usr/bin/ssh
                                    cmd_logger	/usr/bin/logger
                                    {retain_hourly_comment}retain		hourly	{retain_hourly}
                                    retain		daily	{retain_daily}
                                    retain		weekly	{retain_weekly}
                                    retain		monthly	{retain_monthly}
                                    verbose		{verbosity_level}
                                    loglevel	3
                                    logfile		/opt/sysadmws/rsnapshot_backup/rsnapshot.log
                                    lockfile	/opt/sysadmws/rsnapshot_backup/rsnapshot.pid
                                    ssh_args	{ssh_args} -p {port}
                                    rsync_long_args	-az --delete --delete-excluded --numeric-ids --relative {rsync_verbosity_args} {rsync_args}
                                    sync_first	1
                                    {conf_backup_lines}
                                    """
                                ).format(
                                    snapshot_root=item["path"],
                                    retain_hourly_comment="" if "retain_hourly" in item else "#",
                                    retain_hourly=item["retain_hourly"] if "retain_hourly" in item else "NONE",
                                    retain_daily=item["retain_daily"],
                                    retain_weekly=item["retain_weekly"],
                                    retain_monthly=item["retain_monthly"],
                                    verbosity_level=item["verbosity_level"],
                                    port=item["connect_port"],
                                    ssh_args=ssh_args,
                                    rsync_verbosity_args=item["rsync_verbosity_args"],
                                    rsync_args=item["rsync_args"],
                                    conf_backup_lines=conf_backup_lines
                                ))
                        
                            # Run rsnapshot
                            log_and_print("NOTICE", "Running rsnapshot sync on item number {number}".format(number=item["number"]), logger)
                            try:
                                retcode = run_cmd("rsnapshot -c {conf} sync".format(conf=RSNAPSHOT_CONF))
                                if retcode == 0:
                                    log_and_print("NOTICE", "Rsnapshot succeeded on item number {number}".format(number=item["number"]), logger)
                                else:
                                    log_and_print("ERROR", "Rsnapshot failed on item number {number}".format(number=item["number"]), logger)
                                    errors += 1
                            except Exception as e:
                                logger.exception(e)
                                raise Exception("Caught exception on subprocess.run execution")

                            # Exec exec_after_rsync
                            if "exec_after_rsync" in item:
                                log_and_print("NOTICE", "Executing remote exec_after_rsync on item number {number}".format(number=item["number"]), logger)
                                log_and_print("NOTICE", "{cmd}".format(cmd=item["exec_after_rsync"]), logger)
                                try:
                                    retcode = run_cmd("ssh {ssh_args} -p {port} {user}@{host} '{cmd}'".format(ssh_args=ssh_args, port=item["connect_port"], user=item["connect_user"], host=item["connect_host"], cmd=item["exec_after_rsync"]))
                                    if retcode == 0:
                                        log_and_print("NOTICE", "Remote execution of exec_after_rsync succeeded on item number {number}".format(number=item["number"]), logger)
                                    else:
                                        log_and_print("ERROR", "Remote execution of exec_after_rsync failed on item number {number}, but script continues".format(number=item["number"]), logger)
                                        errors += 1
                                except Exception as e:
                                    logger.exception(e)
                                    raise Exception("Caught exception on subprocess.run execution")

                        elif item["type"] in ["RSYNC_NATIVE"]:

                            if ":" in item["connect"]:
                                item["connect_host"] = item["connect"].split(":")[0]
                                item["connect_port"] = item["connect"].split(":")[1]
                            else:
                                item["connect_host"] = item["connect"]
                                item["connect_port"] = 873

                            # Check connect password
                            if "connect_password" not in item:
                                log_and_print("ERROR", "No Rsync password provided for native rsync on item number {number}, not doing sync".format(number=item["number"]), logger)
                                errors += 1
                                continue

                            # Save connect password to file
                            with open(RSNAPSHOT_PASSWD, "w") as file_to_write:
                                file_to_write.write(item["connect_password"])
                            os.chmod(RSNAPSHOT_PASSWD, 0o600)
                            
                            # Check remote .backup existance, if no file - skip to next. Remote windows rsync server can give empty set in some cases, which can lead to backup to be erased.
                            if item["native_txt_check"]:
                                log_and_print("NOTICE", "Remote .backup existance check required on item number {number}".format(number=item["number"]), logger)
                                try:
                                    retcode = run_cmd("rsync --password-file={passwd} rsync://{user}@{host}:{port}{source}/ | grep .backup".format(
                                        passwd=RSNAPSHOT_PASSWD,
                                        user=item["connect_user"],
                                        host=item["connect_host"],
                                        port=item["connect_port"],
                                        source=item["source"]
                                    ))
                                    if retcode == 0:
                                        log_and_print("NOTICE", "Remote .backup existance check succeeded on item number {number}".format(number=item["number"]), logger)
                                    else:
                                        log_and_print("ERROR", "Remote .backup existance check failed on item number {number}, not doing sync".format(number=item["number"]), logger)
                                        errors += 1
                                        continue
                                except Exception as e:
                                    logger.exception(e)
                                    raise Exception("Caught exception on subprocess.run execution")

                            # Save config
                            with open(RSNAPSHOT_CONF, "w") as file_to_write:
                                file_to_write.write(textwrap.dedent(
                                    """\
                                    config_version	1.2
                                    snapshot_root	{snapshot_root}
                                    cmd_cp		/bin/cp
                                    cmd_rm		/bin/rm
                                    cmd_rsync	/usr/bin/rsync
                                    cmd_ssh		/usr/bin/ssh
                                    cmd_logger	/usr/bin/logger
                                    {retain_hourly_comment}retain		hourly	{retain_hourly}
                                    retain		daily	{retain_daily}
                                    retain		weekly	{retain_weekly}
                                    retain		monthly	{retain_monthly}
                                    verbose		{verbosity_level}
                                    loglevel	3
                                    logfile		/opt/sysadmws/rsnapshot_backup/rsnapshot.log
                                    lockfile	/opt/sysadmws/rsnapshot_backup/rsnapshot.pid
                                    rsync_long_args	-az --delete --delete-excluded --no-owner --no-group --numeric-ids --relative --password-file={passwd} {rsync_verbosity_args} {rsync_args}
                                    sync_first	1
                                    backup		rsync://{user}@{host}:{port}{source}/		rsnapshot/
                                    """
                                ).format(
                                    snapshot_root=item["path"],
                                    retain_hourly_comment="" if "retain_hourly" in item else "#",
                                    retain_hourly=item["retain_hourly"] if "retain_hourly" in item else "NONE",
                                    retain_daily=item["retain_daily"],
                                    retain_weekly=item["retain_weekly"],
                                    retain_monthly=item["retain_monthly"],
                                    verbosity_level=item["verbosity_level"],
                                    passwd=RSNAPSHOT_PASSWD,
                                    rsync_verbosity_args=item["rsync_verbosity_args"],
                                    rsync_args=item["rsync_args"],
                                    user=item["connect_user"],
                                    host=item["connect_host"],
                                    port=item["connect_port"],
                                    source=item["source"]
                                ))

                            # Run rsnapshot
                            log_and_print("NOTICE", "Running rsnapshot sync on item number {number}".format(number=item["number"]), logger)
                            try:
                                retcode = run_cmd("{timeout}rsnapshot -c {conf} sync".format(
                                    timeout="timeout --preserve-status -k 60 10h " if item["native_10h_limit"] else "",
                                    conf=RSNAPSHOT_CONF
                                ))
                                if retcode == 0:
                                    log_and_print("NOTICE", "Rsnapshot succeeded on item number {number}".format(number=item["number"]), logger)
                                else:
                                    log_and_print("ERROR", "Rsnapshot failed on item number {number}".format(number=item["number"]), logger)
                                    errors += 1
                            except Exception as e:
                                logger.exception(e)
                                raise Exception("Caught exception on subprocess.run execution")
                            
                            # Delete password file
                            os.remove(RSNAPSHOT_PASSWD)
                        
                        else:
                            log_and_print("ERROR", "Unknown item type {type} on item number {number}".format(type=item["type"], number=item["number"]), logger)
                            errors += 1

                except Exception as e:
                    logger.error("Caught exception, but not interrupting")
                    logger.exception(e)
                    errors += 1

            # Exit with error if there were errors
            if errors > 0:
                log_and_print("ERROR", "{LOGO} on {hostname} errors found: {errors}".format(LOGO=LOGO, hostname=SELF_HOSTNAME, errors=errors), logger)
                raise Exception("There were errors")
            else:
                log_and_print("NOTICE", "{LOGO} on {hostname} finished OK".format(LOGO=LOGO, hostname=SELF_HOSTNAME), logger)

        finally:
            lock.release() 

    # Reroute catched exception to log
    except Exception as e:
        logger.exception(e)
        logger.info("Finished {LOGO} with errors".format(LOGO=LOGO))
        sys.exit(1)

    logger.info("Finished {LOGO}".format(LOGO=LOGO))