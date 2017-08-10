#!/bin/bash

# Check run syntax
if [ "$1" != "0" ] && [ "$1" != "1" ]; then
	date '+%F %T ' | tr -d '\n'
	echo -e >&2 "ERROR: Use $0 0|1"
	echo -e >&2 "ERROR: 1 to show NOTICE lines or 0 to skip them"
        exit 1
fi

if [ "$1" == "1" ]; then
	date '+%F %T ' | tr -d '\n'
	echo -n "NOTICE: Hostname: "
	salt-call --local grains.item fqdn 2>&1 | tail -n 1 | sed 's/^ *//'
fi

if find /opt/sysadmws-utils/rsnapshot_backup -name 'no-compress_*' | grep -q no-compress; then
	date '+%F %T ' | tr -d '\n'
	echo -e >&2 "WARNING: Both skip file $SKIP_FILE and config file $CONF_FILE exist on backup server and config file contains non comment lines"
fi 