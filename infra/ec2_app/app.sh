#!/usr/bin/env bash
# Full NilaSaatchi app on ONE small EC2 server, on demand (D-079). Team 49, ap-south-1 only.
#   bash infra/ec2_app/app.sh deploy   # upload bundle to private S3, create SG + t3.small, first-boot setup (~15 min)
#   bash infra/ec2_app/app.sh start    # start the stopped server (auto-stops 3 h after every boot)
#   bash infra/ec2_app/app.sh stop     # stop it (disk kept, no compute cost)
#   bash infra/ec2_app/app.sh status   # state, URL, setup progress
#   bash infra/ec2_app/app.sh down     # terminate the server + delete its security group (asks first)
# Needs the SSO session (make aws-login). EC2 via profile fai-compute, S3 via fai-builder.
set -euo pipefail
cd "$(dirname "$0")/../.."
C="--profile fai-compute --region ap-south-1"; B="--profile fai-builder --region ap-south-1"
NAME=fai-tce-team49-app; SGNAME=fai-tce-team49-app-sg; BUCKET=fai-tce-team49-data; PFX=app-bundle
TYPE=${TYPE:-t3.small}; RES=infra/RESOURCES.md; SCR=${TMPDIR:-/tmp}/nila-ec2; mkdir -p "$SCR"
now() { date -u +"%Y-%m-%d %H:%M UTC"; }
iid() { aws ec2 describe-instances $C --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
          --query 'Reservations[0].Instances[0].InstanceId' --output text; }
url() { aws ec2 describe-instances $C --instance-ids "$1" --query 'Reservations[0].Instances[0].PublicDnsName' --output text; }

# presigned links are full of '&', so they are substituted in Python (sed would treat '&' as "the match").
# The script is wrapped in a MIME part that runs on every boot; it exits early once setup has completed.
render_userdata() {
  local keys="code.tar.gz web.tar.gz env nilasaatchi.dump data.tar.gz" k
  for k in $keys; do aws s3 presign "s3://$BUCKET/$PFX/$k" $B --expires-in 7200 > "$SCR/url_$k"; done
  SCR="$SCR" python3 - <<'PY'
import os
scr = os.environ["SCR"]
url = lambda k: open(f"{scr}/url_{k}").read().strip()
s = open("infra/ec2_app/userdata.sh.tmpl").read()
for ph, k in [("__CODE_URL__", "code.tar.gz"), ("__WEB_URL__", "web.tar.gz"), ("__ENV_URL__", "env"),
              ("__DUMP_URL__", "nilasaatchi.dump"), ("__DATA_URL__", "data.tar.gz")]:
    s = s.replace(ph, url(k))
assert not any(ph in s for ph in ("__CODE_URL__", "__WEB_URL__", "__ENV_URL__", "__DUMP_URL__", "__DATA_URL__"))
b = "NILA-BOUNDARY"
cfg = "#cloud-config\ncloud_final_modules:\n- [scripts-user, always]\n"
mime = (f'Content-Type: multipart/mixed; boundary="{b}"\nMIME-Version: 1.0\n\n'
        f'--{b}\nContent-Type: text/cloud-config; charset="us-ascii"\n\n{cfg}\n'
        f'--{b}\nContent-Type: text/x-shellscript; charset="us-ascii"\n\n{s}\n--{b}--\n')
open(f"{scr}/userdata.sh", "w").write(mime)
PY
  rm -f "$SCR"/url_*
}

deploy() {
  [ "$(iid)" = "None" ] || { echo "server already exists ($(iid)); use start/status"; exit 1; }
  if [ "${SKIP_UPLOAD:-0}" != 1 ]; then   # SKIP_UPLOAD=1 reuses the files already in S3
  echo "==> build uploads: code, web (served under /api), .env, database dump, data"
  tar -czf "$SCR/code.tar.gz" --exclude=./Dataset --exclude=./Documents --exclude=./ref --exclude=./data --exclude=./dist \
    --exclude=./.env --exclude=./.venv --exclude=./.venv-win --exclude=./web/node_modules --exclude=./web/dist --exclude='__pycache__' \
    --exclude=./.git --exclude=./.pytest_cache --exclude=./.ruff_cache --exclude=./.hypothesis --exclude=./web/test-results .
  (cd web && VITE_API_BASE=/api npx vite build --outDir "$SCR/web-dist" --emptyOutDir >/dev/null) && tar -czf "$SCR/web.tar.gz" -C "$SCR/web-dist" .
  D=dist/nilasaatchi-demo/bundle; [ -f "$D/nilasaatchi.dump" ] && [ -f "$D/data.tar.gz" ] || { echo "run make package first"; exit 1; }
  echo "==> upload to s3://$BUCKET/$PFX/ (private)"
  aws s3 cp "$SCR/code.tar.gz" "s3://$BUCKET/$PFX/code.tar.gz" $B --only-show-errors
  aws s3 cp "$SCR/web.tar.gz" "s3://$BUCKET/$PFX/web.tar.gz" $B --only-show-errors
  aws s3 cp "$D/nilasaatchi.dump" "s3://$BUCKET/$PFX/nilasaatchi.dump" $B --only-show-errors
  aws s3 cp "$D/data.tar.gz" "s3://$BUCKET/$PFX/data.tar.gz" $B --only-show-errors
  fi
  aws s3 cp .env "s3://$BUCKET/$PFX/env" $B --only-show-errors   # always fresh; remove after boot: app.sh clean-env
  render_userdata
  echo "   user-data: $(wc -c < "$SCR/userdata.sh") bytes (limit 16384)"

  echo "==> security group $SGNAME (HTTP 80 from anywhere)"
  VPC=$(aws ec2 describe-vpcs $C --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  SG=$(aws ec2 describe-security-groups $C --filters "Name=group-name,Values=$SGNAME" --query 'SecurityGroups[0].GroupId' --output text)
  if [ "$SG" = "None" ]; then
    SG=$(aws ec2 create-security-group $C --group-name $SGNAME --description "NilaSaatchi app HTTP (team 49)" --vpc-id "$VPC" --query GroupId --output text)
    aws ec2 authorize-security-group-ingress $C --group-id "$SG" --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
    echo "| Security group | \`$SGNAME\` (\`$SG\`) | $(now) | — | inbound TCP 80 from 0.0.0.0/0 (HTTP to the app) |" >> "$RES"
  fi
  AMI=$(aws ec2 describe-images $C --owners 099720109477 --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
          "Name=state,Values=available" --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)
  SUB=$(aws ec2 describe-subnets $C --filters "Name=vpc-id,Values=$VPC" "Name=default-for-az,Values=true" --query 'Subnets[0].SubnetId' --output text)
  echo "==> launch $TYPE ($AMI, $SUB)"
  ID=$(aws ec2 run-instances $C --image-id "$AMI" --instance-type "$TYPE" --subnet-id "$SUB" --security-group-ids "$SG" --count 1 \
        --metadata-options HttpTokens=required,HttpEndpoint=enabled \
        --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}]' \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Team,Value=team49}]" "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{Key=Team,Value=team49}]" \
        --user-data "file://$SCR/userdata.sh" --query 'Instances[0].InstanceId' --output text)
  echo "| EC2 instance | \`$NAME\` (\`$ID\`, $TYPE, 30 GB gp3 encrypted) | $(now) | — | full app (docker PostGIS + API + nginx), auto-stop 3 h after boot; no AWS credentials on the server (Bedrock excluded) |" >> "$RES"
  aws ec2 wait instance-running $C --instance-ids "$ID"
  echo "server $ID running; first-boot setup takes ~15 min. Progress: bash infra/ec2_app/app.sh status"
  echo "URL (when ready): http://$(url "$ID")/"
}

status() {
  ID=$(iid); [ "$ID" = "None" ] && { echo "no server"; return; }
  ST=$(aws ec2 describe-instances $C --instance-ids "$ID" --query 'Reservations[0].Instances[0].State.Name' --output text)
  echo "server $ID: $ST"
  [ "$ST" = "running" ] || return 0
  U="http://$(url "$ID")"; echo "URL: $U/"
  # (ec2:GetConsoleOutput is denied to both team roles, so progress is judged from outside)
  curl -s -m 5 "$U/" | grep -q "Welcome to nginx" && echo "setup still running (packages installed, app not yet served)"
  printf 'API health: '; curl -s -m 5 "$U/api/health" || echo "not ready"
  echo
}

case "${1:-status}" in
  deploy) deploy ;;
  start) ID=$(iid); aws ec2 start-instances $C --instance-ids "$ID" >/dev/null; aws ec2 wait instance-running $C --instance-ids "$ID"
         echo "started (auto-stops in 3 h). URL: http://$(url "$ID")/ (ready ~1-2 min after start)" ;;
  stop) ID=$(iid); aws ec2 stop-instances $C --instance-ids "$ID" >/dev/null; echo "stopping $ID (disk kept)" ;;
  status) status ;;
  clean-env) aws s3 rm "s3://$BUCKET/$PFX/env" $B && echo "removed the .env copy from S3 (the server keeps its own)" ;;
  reprovision) ID=$(iid)   # re-run first-boot setup with fresh links (stop -> replace user-data -> start)
         aws ec2 stop-instances $C --instance-ids "$ID" >/dev/null; aws ec2 wait instance-stopped $C --instance-ids "$ID"
         render_userdata
         aws ec2 modify-instance-attribute $C --instance-id "$ID" --user-data "{\"Value\": \"$(base64 -w0 "$SCR/userdata.sh")\"}"
         aws ec2 start-instances $C --instance-ids "$ID" >/dev/null; aws ec2 wait instance-running $C --instance-ids "$ID"
         echo "restarted $ID with new setup; URL: http://$(url "$ID")/" ;;
  down) ID=$(iid); read -r -p "Terminate $ID and delete $SGNAME? Type 'delete': " ok; [ "$ok" = "delete" ] || exit 1
        aws ec2 terminate-instances $C --instance-ids "$ID" >/dev/null; aws ec2 wait instance-terminated $C --instance-ids "$ID"
        SG=$(aws ec2 describe-security-groups $C --filters "Name=group-name,Values=$SGNAME" --query 'SecurityGroups[0].GroupId' --output text)
        aws ec2 delete-security-group $C --group-id "$SG"; aws s3 rm "s3://$BUCKET/$PFX/" --recursive $B --only-show-errors
        echo "| (teardown) | EC2 $ID + SG $SG + s3 $PFX/ | — | $(now) | infra/ec2_app/app.sh down |" >> "$RES"; echo "removed" ;;
  *) echo "usage: $0 deploy|start|stop|status|clean-env|reprovision|down"; exit 1 ;;
esac
