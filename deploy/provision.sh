#!/bin/zsh
# One-time AWS provisioning for TCG Predictor (us-east-1, t3.small, 30GB gp3,
# Elastic IP). Idempotent-ish: tags everything tcg-predictor and skips pieces
# that already exist. Prints the SSH line and Elastic IP at the end.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
NAME=tcg-predictor
KEY_FILE=~/.ssh/${NAME}.pem

# --- key pair ---------------------------------------------------------------
if ! aws ec2 describe-key-pairs --key-names $NAME >/dev/null 2>&1; then
  aws ec2 create-key-pair --key-name $NAME \
    --query 'KeyMaterial' --output text > $KEY_FILE
  chmod 600 $KEY_FILE
  echo "created key pair -> $KEY_FILE"
fi

# --- security group: 80/443 world, 22 from this machine's IP ----------------
MYIP=$(curl -s https://checkip.amazonaws.com)/32
SG_ID=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --group-name $NAME \
    --description "tcg-predictor web" --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --group-id $SG_ID \
    --protocol tcp --port 80 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress --group-id $SG_ID \
    --protocol tcp --port 443 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress --group-id $SG_ID \
    --protocol tcp --port 22 --cidr $MYIP
  echo "created security group $SG_ID (ssh limited to $MYIP)"
fi

# --- instance: Ubuntu 24.04 LTS amd64 ----------------------------------------
AMI=$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameter.Value' --output text)
IID=$(aws ec2 describe-instances \
  --filters Name=tag:Name,Values=$NAME Name=instance-state-name,Values=pending,running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null)
if [ "$IID" = "None" ] || [ -z "$IID" ]; then
  IID=$(aws ec2 run-instances \
    --image-id $AMI --instance-type t3.small --key-name $NAME \
    --security-group-ids $SG_ID \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
  echo "launched $IID (AMI $AMI)"
  aws ec2 wait instance-running --instance-ids $IID
fi

# --- elastic ip ---------------------------------------------------------------
EIP_ALLOC=$(aws ec2 describe-addresses --filters Name=tag:Name,Values=$NAME \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null)
if [ "$EIP_ALLOC" = "None" ] || [ -z "$EIP_ALLOC" ]; then
  EIP_ALLOC=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'AllocationId' --output text)
fi
aws ec2 associate-address --instance-id $IID --allocation-id $EIP_ALLOC >/dev/null
EIP=$(aws ec2 describe-addresses --allocation-ids $EIP_ALLOC \
  --query 'Addresses[0].PublicIp' --output text)

echo ""
echo "instance: $IID"
echo "elastic ip: $EIP"
echo "ssh: ssh -i $KEY_FILE ubuntu@$EIP"
