#!/usr/bin/env bash
# Imprime el estado resumido del replica set rs0 (para P2 y P8).
# Pregunta al primer nodo que responda, así sirve aunque haya nodos caídos.
# Uso: pruebas/estado_rs.sh [mongo1|mongo2|mongo3 ...]
set -u
cd "$(dirname "$0")/.."

if [ $# -gt 0 ]; then nodos=("$@"); else nodos=(mongo1 mongo2 mongo3); fi

js='
const s = rs.status();
print("conjunto: " + s.set + "  term: " + s.term + "  consultado en: " + db.hello().me);
s.members.forEach(m => print(
  m.name.padEnd(14) + m.stateStr.padEnd(24) + "health=" + m.health +
  "  optime=" + (m.health === 1 && m.optimeDate ? m.optimeDate.toISOString() : "-")));
'

date -u +%FT%TZ
for n in "${nodos[@]}"; do
  if out=$(docker compose exec -T "$n" mongosh --quiet --eval "$js" 2>/dev/null); then
    echo "$out"
    exit 0
  fi
done
echo "ningún nodo respondió (${nodos[*]})" >&2
exit 1
