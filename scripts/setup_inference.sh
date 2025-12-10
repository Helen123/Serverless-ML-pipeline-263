#!/bin/bash
# Setup Script for Online & Batch Inference
# This script sets up both online and batch inference capabilities

set -e  # Exit on error

echo "=========================================="
echo "Setting up Online & Batch Inference"
echo "=========================================="

# Step 1: Set Environment Variables
echo ""
echo "Step 1: Setting environment variables..."
export AWS_PROFILE=${AWS_PROFILE:-helen1}
export AWS_REGION=${AWS_REGION:-us-west-2}
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export BUCKET=${BUCKET:-ml-pipeline-dev-954976298878-us-west-2-helen}
export ENDPOINT_NAME=${ENDPOINT_NAME:-house-price-endpoint}
export MODEL_TABLE_NAME=${MODEL_TABLE_NAME:-ml-pipeline-models}
export ONLINE_INFER_FUNCTION_NAME=${ONLINE_INFER_FUNCTION_NAME:-OnlineInfer}
export BATCH_INFER_FUNCTION_NAME=${BATCH_INFER_FUNCTION_NAME:-BatchInfer}

echo "✓ Environment variables set:"
echo "  - AWS_REGION: $AWS_REGION"
echo "  - ACCOUNT_ID: $ACCOUNT_ID"
echo "  - BUCKET: $BUCKET"
echo "  - ENDPOINT_NAME: $ENDPOINT_NAME"
echo "  - ONLINE_INFER_FUNCTION_NAME: $ONLINE_INFER_FUNCTION_NAME"
echo "  - BATCH_INFER_FUNCTION_NAME: $BATCH_INFER_FUNCTION_NAME"

# Step 2: Create S3 Prefixes for Batch Inference
echo ""
echo "Step 2: Creating S3 prefixes for batch inference..."
aws s3api put-object --bucket $BUCKET --key to_infer/ --region $AWS_REGION || echo "  to_infer/ prefix may already exist"
aws s3api put-object --bucket $BUCKET --key predicted/ --region $AWS_REGION || echo "  predicted/ prefix may already exist"
echo "✓ S3 prefixes created: to_infer/ and predicted/"

# Step 3: Create IAM Role for Online Inference Lambda
echo ""
echo "Step 3: Creating IAM role for Online Inference Lambda..."
if aws iam get-role --role-name OnlineInferLambdaRole --region $AWS_REGION &>/dev/null; then
    echo "  OnlineInferLambdaRole already exists, skipping creation"
else
    cat > /tmp/online-infer-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    aws iam create-role \
      --role-name OnlineInferLambdaRole \
      --assume-role-policy-document file:///tmp/online-infer-trust-policy.json \
      --region $AWS_REGION

    aws iam attach-role-policy \
      --role-name OnlineInferLambdaRole \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
      --region $AWS_REGION

    cat > /tmp/online-infer-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker-runtime:InvokeEndpoint"
      ],
      "Resource": "arn:aws:sagemaker:${AWS_REGION}:${ACCOUNT_ID}:endpoint/${ENDPOINT_NAME}"
    }
  ]
}
EOF

    aws iam put-role-policy \
      --role-name OnlineInferLambdaRole \
      --policy-name OnlineInferLambdaPolicy \
      --policy-document file:///tmp/online-infer-policy.json \
      --region $AWS_REGION

    echo "✓ OnlineInferLambdaRole created"
fi

ONLINE_INFER_ROLE_ARN=$(aws iam get-role --role-name OnlineInferLambdaRole --query 'Role.Arn' --output text --region $AWS_REGION)
echo "  Role ARN: $ONLINE_INFER_ROLE_ARN"

# Step 4: Create IAM Role for Batch Inference Lambda
echo ""
echo "Step 4: Creating IAM role for Batch Inference Lambda..."
if aws iam get-role --role-name BatchInferLambdaRole --region $AWS_REGION &>/dev/null; then
    echo "  BatchInferLambdaRole already exists, skipping creation"
else
    cat > /tmp/batch-infer-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    aws iam create-role \
      --role-name BatchInferLambdaRole \
      --assume-role-policy-document file:///tmp/batch-infer-trust-policy.json \
      --region $AWS_REGION

    aws iam attach-role-policy \
      --role-name BatchInferLambdaRole \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
      --region $AWS_REGION

    cat > /tmp/batch-infer-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::${BUCKET}/to_infer/*",
        "arn:aws:s3:::${BUCKET}/predicted/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker:CreateTransformJob",
        "sagemaker:DescribeTransformJob"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/SageMakerExecutionRole"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/${MODEL_TABLE_NAME}"
    }
  ]
}
EOF

    aws iam put-role-policy \
      --role-name BatchInferLambdaRole \
      --policy-name BatchInferLambdaPolicy \
      --policy-document file:///tmp/batch-infer-policy.json \
      --region $AWS_REGION

    echo "✓ BatchInferLambdaRole created"
fi

BATCH_INFER_ROLE_ARN=$(aws iam get-role --role-name BatchInferLambdaRole --query 'Role.Arn' --output text --region $AWS_REGION)
echo "  Role ARN: $BATCH_INFER_ROLE_ARN"

# Wait for IAM roles to propagate
echo ""
echo "Waiting for IAM roles to propagate..."
sleep 5

# Step 5: Package and Deploy Online Inference Lambda
echo ""
echo "Step 5: Packaging and deploying Online Inference Lambda..."
cd lambdas/online_infer
zip -q -r function.zip handler.py
cd ../..

if aws lambda get-function --function-name $ONLINE_INFER_FUNCTION_NAME --region $AWS_REGION &>/dev/null; then
    echo "  Function $ONLINE_INFER_FUNCTION_NAME already exists, updating..."
    aws lambda update-function-code \
      --function-name $ONLINE_INFER_FUNCTION_NAME \
      --zip-file fileb://lambdas/online_infer/function.zip \
      --region $AWS_REGION > /dev/null
    
    aws lambda update-function-configuration \
      --function-name $ONLINE_INFER_FUNCTION_NAME \
      --timeout 30 \
      --memory-size 256 \
      --environment "Variables={ENDPOINT_NAME=$ENDPOINT_NAME}" \
      --region $AWS_REGION > /dev/null
else
    aws lambda create-function \
      --function-name $ONLINE_INFER_FUNCTION_NAME \
      --runtime python3.11 \
      --role $ONLINE_INFER_ROLE_ARN \
      --handler handler.lambda_handler \
      --zip-file fileb://lambdas/online_infer/function.zip \
      --timeout 30 \
      --memory-size 256 \
      --environment "Variables={ENDPOINT_NAME=$ENDPOINT_NAME}" \
      --description "Online inference via API Gateway for real-time predictions" \
      --region $AWS_REGION > /dev/null
fi

echo "✓ Online Inference Lambda deployed"

# Step 6: Package and Deploy Batch Inference Lambda
echo ""
echo "Step 6: Packaging and deploying Batch Inference Lambda..."
cd lambdas/batch_infer
zip -q -r function.zip handler.py
cd ../..

if aws lambda get-function --function-name $BATCH_INFER_FUNCTION_NAME --region $AWS_REGION &>/dev/null; then
    echo "  Function $BATCH_INFER_FUNCTION_NAME already exists, updating..."
    aws lambda update-function-code \
      --function-name $BATCH_INFER_FUNCTION_NAME \
      --zip-file fileb://lambdas/batch_infer/function.zip \
      --region $AWS_REGION > /dev/null
    
    aws lambda update-function-configuration \
      --function-name $BATCH_INFER_FUNCTION_NAME \
      --timeout 600 \
      --memory-size 512 \
      --environment "Variables={BUCKET=$BUCKET,MODEL_TABLE_NAME=$MODEL_TABLE_NAME}" \
      --region $AWS_REGION > /dev/null
else
    aws lambda create-function \
      --function-name $BATCH_INFER_FUNCTION_NAME \
      --runtime python3.11 \
      --role $BATCH_INFER_ROLE_ARN \
      --handler handler.lambda_handler \
      --zip-file fileb://lambdas/batch_infer/function.zip \
      --timeout 600 \
      --memory-size 512 \
      --environment "Variables={BUCKET=$BUCKET,MODEL_TABLE_NAME=$MODEL_TABLE_NAME}" \
      --description "Batch inference triggered by S3 uploads to to_infer/" \
      --region $AWS_REGION > /dev/null
fi

echo "✓ Batch Inference Lambda deployed"

# Step 7: Set up EventBridge Rule for Batch Inference
echo ""
echo "Step 7: Setting up EventBridge rule for batch inference..."
cat > /tmp/batch-infer-rule.json <<EOF
{
  "EventPattern": {
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {
      "bucket": {
        "name": ["${BUCKET}"]
      },
      "object": {
        "key": [{
          "prefix": "to_infer/"
        }]
      }
    }
  }
}
EOF

if aws events describe-rule --name BatchInferTrigger --region $AWS_REGION &>/dev/null; then
    echo "  EventBridge rule BatchInferTrigger already exists, updating..."
    aws events put-rule \
      --name BatchInferTrigger \
      --event-pattern file:///tmp/batch-infer-rule.json \
      --state ENABLED \
      --region $AWS_REGION > /dev/null
else
    aws events put-rule \
      --name BatchInferTrigger \
      --event-pattern file:///tmp/batch-infer-rule.json \
      --state ENABLED \
      --region $AWS_REGION > /dev/null
fi

# Add Lambda as target
aws events put-targets \
  --rule BatchInferTrigger \
  --targets "Id=1,Arn=arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${BATCH_INFER_FUNCTION_NAME}" \
  --region $AWS_REGION > /dev/null

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
  --function-name $BATCH_INFER_FUNCTION_NAME \
  --statement-id BatchInferEventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/BatchInferTrigger \
  --region $AWS_REGION 2>/dev/null || echo "  Permission may already exist"

echo "✓ EventBridge rule configured"

# Step 8: Set up API Gateway for Online Inference
echo ""
echo "Step 8: Setting up API Gateway for online inference..."

# Check if API already exists
EXISTING_API_ID=$(aws apigateway get-rest-apis --region $AWS_REGION --query "items[?name=='MLPipelineInferenceAPI'].id" --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_API_ID" ] && [ "$EXISTING_API_ID" != "None" ]; then
    echo "  API Gateway already exists (ID: $EXISTING_API_ID), skipping creation"
    API_ID=$EXISTING_API_ID
else
    API_ID=$(aws apigateway create-rest-api \
      --name MLPipelineInferenceAPI \
      --description "API for house price predictions" \
      --endpoint-configuration types=REGIONAL \
      --region $AWS_REGION \
      --query 'id' --output text)
    echo "  Created new API Gateway (ID: $API_ID)"
fi

# Get root resource ID
ROOT_RESOURCE_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --region $AWS_REGION \
  --query 'items[0].id' --output text)

# Check if /predict resource exists
PREDICT_RESOURCE_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --region $AWS_REGION \
  --query "items[?path=='/predict'].id" --output text)

if [ -z "$PREDICT_RESOURCE_ID" ] || [ "$PREDICT_RESOURCE_ID" == "None" ]; then
    # Create /predict resource
    PREDICT_RESOURCE_ID=$(aws apigateway create-resource \
      --rest-api-id $API_ID \
      --parent-id $ROOT_RESOURCE_ID \
      --path-part predict \
      --region $AWS_REGION \
      --query 'id' --output text)
    echo "  Created /predict resource"
fi

# Create or update POST method
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id $PREDICT_RESOURCE_ID \
  --http-method POST \
  --authorization-type NONE \
  --region $AWS_REGION > /dev/null 2>&1 || echo "  POST method may already exist"

# Set up Lambda integration
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id $PREDICT_RESOURCE_ID \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri arn:aws:apigateway:${AWS_REGION}:lambda:path/2015-03-31/functions/arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${ONLINE_INFER_FUNCTION_NAME}/invocations \
  --region $AWS_REGION > /dev/null

# Grant API Gateway permission to invoke Lambda
aws lambda add-permission \
  --function-name $ONLINE_INFER_FUNCTION_NAME \
  --statement-id APIGatewayInvoke-$(date +%s) \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn arn:aws:execute-api:${AWS_REGION}:${ACCOUNT_ID}:${API_ID}/*/* \
  --region $AWS_REGION 2>/dev/null || echo "  Permission may already exist"

# Deploy API
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod \
  --region $AWS_REGION > /dev/null 2>&1 || echo "  Deployment may already exist"

API_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod/predict"
echo "✓ API Gateway configured"
echo "  API Endpoint: $API_URL"

# Step 9: Summary
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Summary:"
echo "  ✓ S3 prefixes created (to_infer/, predicted/)"
echo "  ✓ IAM roles created (OnlineInferLambdaRole, BatchInferLambdaRole)"
echo "  ✓ Lambda functions deployed ($ONLINE_INFER_FUNCTION_NAME, $BATCH_INFER_FUNCTION_NAME)"
echo "  ✓ EventBridge rule configured (BatchInferTrigger)"
echo "  ✓ API Gateway configured"
echo ""
echo "API Endpoint URL:"
echo "  $API_URL"
echo ""
echo "Next Steps:"
echo "  1. Test online inference:"
echo "     curl -X POST $API_URL \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"area_sqm\": 150.5, \"latitude\": 37.7749, \"longitude\": -122.4194, \"house_age\": 25, \"med_inc\": 4.5, \"ave_rooms\": 5.0, \"ave_bedrms\": 2.0, \"population\": 1000, \"ave_occup\": 3.0, \"house_type\": \"house\"}'"
echo ""
echo "  2. Test batch inference:"
echo "     aws s3 cp <your-data.csv> s3://$BUCKET/to_infer/batch_test.csv"
echo ""
echo "   Monitor batch transform jobs:"
echo "     aws sagemaker list-transform-jobs --name-contains batch-infer --region $AWS_REGION"
echo ""

