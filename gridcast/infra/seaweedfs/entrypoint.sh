#!/bin/sh
# Render the S3 identity file from the environment, then start SeaweedFS (master + volume +
# filer + S3 gateway in one process).
set -eu
cat > /tmp/s3.json <<JSON
{"identities": [{"name": "gridcast", "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}], "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
JSON
exec weed server -dir=/data -s3 -s3.config=/tmp/s3.json -master.volumeSizeLimitMB=128 -volume.max=0 -ip.bind=0.0.0.0
