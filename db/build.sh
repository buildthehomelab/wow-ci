#!/usr/bin/env bash
# Builds ghcr.io/buildthehomelab/wow-ci-db: MySQL 8.4 preloaded with the server's
# databases (core base + updates, then every module in config/server-modules.tsv at its
# pinned commit). Each module repo's SQL check starts from this snapshot, so a PR's SQL is
# tested the way the live server applies it: on top of what's already there.
#
# The official image keeps its data in a VOLUME (/var/lib/mysql) that `docker commit`
# would drop, so the data lives in /data instead.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=/dev/null
. "$HERE/config/core.env"
IMAGE=${IMAGE:-ghcr.io/buildthehomelab/wow-ci-db}
WORK=${RUNNER_TEMP:-/tmp}/wow-ci-db
mkdir -p "$WORK"
export MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PWD=ci

fetch() {  # fetch URL COMMIT DIR [sparse paths...]
  local url=$1 ref=$2 dir=$3; shift 3
  rm -rf "$dir"; git init -q "$dir"
  git -C "$dir" remote add origin "$url"
  if [ $# -gt 0 ]; then
    git -C "$dir" sparse-checkout set --no-cone "$@"
  fi
  git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$ref"
  git -C "$dir" checkout -q FETCH_HEAD
}

echo "::group::Fetch core $CORE_REF (data/sql only)"
fetch "$CORE_REPO" "$CORE_REF" "$WORK/core" /data/sql/
echo "::endgroup::"

MYSQLD_OPTS=(--datadir=/data --skip-log-bin --innodb-buffer-pool-size=2G
             --innodb-flush-log-at-trx-commit=0 --innodb-doublewrite=OFF
             --max-allowed-packet=1G --character-set-server=utf8mb4)
docker rm -f wow-ci-db >/dev/null 2>&1 || true
docker run -d --name wow-ci-db -p 3306:3306 -e MYSQL_ROOT_PASSWORD=ci -e MYSQL_ROOT_HOST=% \
  mysql:8.4 "${MYSQLD_OPTS[@]}"
for i in $(seq 1 120); do
  mysql -h127.0.0.1 -uroot -e 'SELECT 1' >/dev/null 2>&1 && break
  sleep 2
done
mysql -h127.0.0.1 -uroot -e 'SELECT VERSION()'

mysql -h127.0.0.1 -uroot <<'SQL'
CREATE DATABASE acore_world      DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE acore_characters DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE acore_auth       DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE acore_playerbots DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE ci_meta;
CREATE TABLE ci_meta.sql_files (db VARCHAR(32), module VARCHAR(128), name VARCHAR(255), ok TINYINT,
                                PRIMARY KEY (db, module, name));
CREATE TABLE ci_meta.info (k VARCHAR(64) PRIMARY KEY, v TEXT);
SQL

apply_dir() {  # apply_dir DIR DATABASE LABEL
  local dir=$1 db=$2 label=$3 n=0
  [ -d "$dir" ] || return 0
  while IFS= read -r f; do
    mysql -h127.0.0.1 -uroot --default-character-set=utf8 --max-allowed-packet=1GB "$db" <"$f" \
      || { echo "::error::$label: $(basename "$f") failed"; exit 1; }
    if [ "$label" = core-updates ]; then
      mysql -h127.0.0.1 -uroot -e "INSERT IGNORE INTO ci_meta.sql_files VALUES ('$db', 'core', '$(basename "$f")', 1)"
    fi
    n=$((n + 1))
  done < <(find "$dir" -name '*.sql' | awk -F/ '{print $NF"\t"$0}' | sort | cut -f2-)
  echo "$label $db: $n file(s)"
}

for pair in auth:acore_auth characters:acore_characters world:acore_world; do
  key=${pair%%:*} db=${pair#*:}
  echo "::group::Core $db"
  apply_dir "$WORK/core/data/sql/base/db_$key" "$db" core-base
  apply_dir "$WORK/core/data/sql/updates/db_$key" "$db" core-updates
  apply_dir "$WORK/core/data/sql/updates/pending_db_$key" "$db" core-updates
  echo "::endgroup::"
done

# Server modules, in folder order.
tail -n +2 "$HERE/config/server-modules.tsv" | while IFS=$'\t' read -r folder url commit; do
  echo "::group::$folder @ ${commit:0:7}"
  if fetch "$url" "$commit" "$WORK/modules/$folder" /data/sql/ /src/; then
    python3 "$HERE/scripts/sqlcheck.py" apply "$WORK/modules/$folder" --module "$folder" --record
  else
    echo "::warning::could not fetch $folder"
  fi
  echo "::endgroup::"
done

mysql -h127.0.0.1 -uroot -e "REPLACE INTO ci_meta.info VALUES ('core_ref', '$CORE_REF'), ('built', NOW())"
mysql -h127.0.0.1 -uroot -N -e "SELECT module, SUM(ok=0) FROM ci_meta.sql_files GROUP BY module HAVING SUM(ok=0) > 0" \
  | while read -r m n; do echo "::warning::$m: $n file(s) failed to apply in the snapshot"; done

docker exec wow-ci-db mysqladmin -uroot -pci shutdown
docker wait wow-ci-db >/dev/null || true
short=${CORE_REF:0:7}
cmd=$(printf '"%s",' mysqld "${MYSQLD_OPTS[@]}")
docker commit --change "CMD [${cmd%,}]" --change "ENV MYSQL_ROOT_PASSWORD=ci" \
  --change "LABEL org.opencontainers.image.source=https://github.com/buildthehomelab/wow-ci" \
  --change "LABEL org.opencontainers.image.description=AzerothCore DB snapshot for module CI (core $short)" \
  wow-ci-db "$IMAGE:$short"
docker tag "$IMAGE:$short" "$IMAGE:latest"
docker images "$IMAGE"
