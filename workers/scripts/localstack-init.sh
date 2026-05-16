#!/usr/bin/env bash
# LocalStack initialization script.
# Runs automatically when LocalStack is ready.
# Creates local SNS topics, SQS queues, and S3 buckets for development.

set -euo pipefail

ENDPOINT=http://localhost:4566
REGION=us-east-1
ACCOUNT_ID=000000000000

echo "==> Creating SNS topics..."
aws --endpoint-url=$ENDPOINT --region=$REGION sns create-topic \
    --name example-topic

echo "==> Creating SQS queues..."
aws --endpoint-url=$ENDPOINT --region=$REGION sqs create-queue \
    --queue-name example-queue

echo "==> Subscribing SQS queue to SNS topic..."
TOPIC_ARN="arn:aws:sns:$REGION:$ACCOUNT_ID:example-topic"
QUEUE_URL="http://localhost:4566/$ACCOUNT_ID/example-queue"
QUEUE_ARN="arn:aws:sqs:$REGION:$ACCOUNT_ID:example-queue"

aws --endpoint-url=$ENDPOINT --region=$REGION sns subscribe \
    --topic-arn "$TOPIC_ARN" \
    --protocol sqs \
    --notification-endpoint "$QUEUE_ARN"

echo "==> Creating S3 buckets..."
aws --endpoint-url=$ENDPOINT --region=$REGION s3 mb s3://app-storage

echo "==> LocalStack init complete."
