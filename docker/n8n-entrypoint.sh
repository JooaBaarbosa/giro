#!/bin/sh
# Importa (ou reimporta) os workflows versionados no repositório e os publica (ativa).
# Em modo normal o n8n não aceita --activeState no import, por isso publicamos por id.
set -e
n8n import:workflow --separate --input=/workflows
for id in $(grep -oh '"id": *"giroWf[A-Za-z0-9]*"' /workflows/*.json | sed 's/.*"\(giroWf[A-Za-z0-9]*\)"/\1/'); do
  n8n publish:workflow --id="$id"
done
exec n8n start
