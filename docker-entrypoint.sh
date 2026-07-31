#!/bin/sh
set -eu

database_path="${HLTV_DATABASE_PATH:-/data/hltv-service.sqlite}"
persistent_directory="${HLTV_PERSISTENT_DIRECTORY:-/data}"

case "$database_path" in
    /*) ;;
    *) echo "HLTV_DATABASE_PATH must be absolute in the container" >&2; exit 64 ;;
esac
case "$persistent_directory" in
    /*) ;;
    *) echo "HLTV_PERSISTENT_DIRECTORY must be absolute" >&2; exit 64 ;;
esac
if [ "$persistent_directory" = "/" ]; then
    echo "HLTV_PERSISTENT_DIRECTORY cannot be the filesystem root" >&2
    exit 64
fi

database_directory="$(dirname -- "$database_path")"
database_name="$(basename -- "$database_path")"
if [ "$database_directory" = "/" ]; then
    echo "Refusing to prepare the filesystem root" >&2
    exit 64
fi
case "$database_directory/" in
    "$persistent_directory/"|"$persistent_directory/"*) ;;
    *)
        echo "Database must be inside the persistent directory" >&2
        exit 64
        ;;
esac

install -d -m 0750 -o hltv -g hltv "$database_directory"
chown hltv:hltv "$database_directory"
for suffix in "" "-wal" "-shm"; do
    candidate="$database_directory/$database_name$suffix"
    if [ -e "$candidate" ]; then
        chown hltv:hltv "$candidate"
    fi
done

exec gosu hltv "$@"
