# AWS Setup Guide: Online & Batch Inference

## Overview
This guide sets up inference capabilities for the ML pipeline:
- **Online Inference**: API Gateway → Lambda → SageMaker Endpoint (real-time predictions)
- **Batch Inference**: S3 upload → EventBridge → Lambda → Batch Transform → S3 output

## Prerequisites
- Model deployed to SageMaker endpoint (via DeployModel Lambda)
- S3 bucket with `to_infer/` and `predicted/` prefixes
- SageMaker endpoint in "InService" status

## Step-by-Step Setup

### 1. Set Environment Variables
```bash
export AWS_PROFILE=helen1
export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export BUCKET=ml-pipeline-dev-954976298878-us-west-2-helen
export ENDPOINT_NAME=house-price-endpoint
export ONLINE_INFER_FUNCTION_NAME=OnlineInfer
export BATCH_INFER_FUNCTION_NAME=BatchInfer
```

### 2. Create S3 Prefixes for Batch Inference
```bash
# Create to_infer/ prefix (input for batch inference)
aws s3api put-object --bucket $BUCKET --key to_infer/

# Create predicted/ prefix (output for batch inference)
aws s3api put-object --bucket $BUCKET --key predicted/
```

### 3. Create IAM Role for Online Inference Lambda
```bash
# Create trust policy
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

# Create the role
aws iam create-role \
  --role-name OnlineInferLambdaRole \
  --assume-role-policy-document file:///tmp/online-infer-trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
  --role-name OnlineInferLambdaRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create policy for SageMaker endpoint invocation
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
  --policy-document file:///tmp/online-infer-policy.json

# Get role ARN
export ONLINE_INFER_ROLE_ARN=$(aws iam get-role --role-name OnlineInferLambdaRole --query 'Role.Arn' --output text)
```

### 4. Create IAM Role for Batch Inference Lambda
```bash
# Create trust policy
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

# Create the role
aws iam create-role \
  --role-name BatchInferLambdaRole \
  --assume-role-policy-document file:///tmp/batch-infer-trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
  --role-name BatchInferLambdaRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create policy for SageMaker Batch Transform and S3 access
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
      "Resource": "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/ml-pipeline-models"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name BatchInferLambdaRole \
  --policy-name BatchInferLambdaPolicy \
  --policy-document file:///tmp/batch-infer-policy.json

# Get role ARN
export BATCH_INFER_ROLE_ARN=$(aws iam get-role --role-name BatchInferLambdaRole --query 'Role.Arn' --output text)
```

### 5. Package and Deploy Online Inference Lambda
```bash
cd lambdas/online_infer
zip -r function.zip handler.py
cd ../..

# Create Lambda function
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
  --region $AWS_REGION
```

### 6. Package and Deploy Batch Inference Lambda
```bash
cd lambdas/batch_infer
zip -r function.zip handler.py
cd ../..

# Create Lambda function
aws lambda create-function \
  --function-name $BATCH_INFER_FUNCTION_NAME \
  --runtime python3.11 \
  --role $BATCH_INFER_ROLE_ARN \
  --handler handler.lambda_handler \
  --zip-file fileb://lambdas/batch_infer/function.zip \
  --timeout 600 \
  --memory-size 512 \
  --environment "Variables={BUCKET=$BUCKET,MODEL_TABLE_NAME=ml-pipeline-models}" \
  --description "Batch inference triggered by S3 uploads to to_infer/" \
  --region $AWS_REGION
```

### 7. Set Up EventBridge Rule for Batch Inference
```bash
# Create EventBridge rule to trigger batch inference when files are uploaded to to_infer/
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

# Create the rule
aws events put-rule \
  --name BatchInferTrigger \
  --event-pattern file:///tmp/batch-infer-rule.json \
  --state ENABLED \
  --region $AWS_REGION

# Add Lambda as target
aws events put-targets \
  --rule BatchInferTrigger \
  --targets "Id=1,Arn=arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${BATCH_INFER_FUNCTION_NAME}" \
  --region $AWS_REGION

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
  --function-name $BATCH_INFER_FUNCTION_NAME \
  --statement-id BatchInferEventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/BatchInferTrigger \
  --region $AWS_REGION
```

### 8. Set Up API Gateway for Online Inference
```bash
# Create REST API
API_ID=$(aws apigateway create-rest-api \
  --name MLPipelineInferenceAPI \
  --description "API for house price predictions" \
  --endpoint-configuration types=REGIONAL \
  --region $AWS_REGION \
  --query 'id' --output text)

# Get root resource ID
ROOT_RESOURCE_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --region $AWS_REGION \
  --query 'items[0].id' --output text)

# Create /predict resource
PREDICT_RESOURCE_ID=$(aws apigateway create-resource \
  --rest-api-id $API_ID \
  --parent-id $ROOT_RESOURCE_ID \
  --path-part predict \
  --region $AWS_REGION \
  --query 'id' --output text)

# Create POST method
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id $PREDICT_RESOURCE_ID \
  --http-method POST \
  --authorization-type NONE \
  --region $AWS_REGION

# Set up Lambda integration
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id $PREDICT_RESOURCE_ID \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri arn:aws:apigateway:${AWS_REGION}:lambda:path/2015-03-31/functions/arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${ONLINE_INFER_FUNCTION_NAME}/invocations \
  --region $AWS_REGION

# Grant API Gateway permission to invoke Lambda
aws lambda add-permission \
  --function-name $ONLINE_INFER_FUNCTION_NAME \
  --statement-id APIGatewayInvoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn arn:aws:execute-api:${AWS_REGION}:${ACCOUNT_ID}:${API_ID}/*/* \
  --region $AWS_REGION

# Deploy API
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod \
  --region $AWS_REGION

# Get API endpoint URL
API_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod/predict"
echo "API Endpoint: $API_URL"
```

### 9. Test Online Inference
```bash
# Test prediction request
cat > /tmp/prediction-request.json <<EOF
{
  "area_sqm": 150.5,
  "latitude": 37.7749,
  "longitude": -122.4194,
  "house_age": 25,
  "med_inc": 4.5,
  "ave_rooms": 5.0,
  "ave_bedrms": 2.0,
  "population": 1000,
  "ave_occup": 3.0,
  "house_type": "house"
}
EOF

# Invoke via API Gateway
curl -X POST $API_URL \
  -H "Content-Type: application/json" \
  -d @/tmp/prediction-request.json

# Or test directly via Lambda
aws lambda invoke \
  --function-name $ONLINE_INFER_FUNCTION_NAME \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/prediction-request.json \
  /tmp/online-infer-response.json

cat /tmp/online-infer-response.json | python3 -m json.tool
```

### 10. Test Batch Inference
```bash
# Create a sample batch inference file
cat > /tmp/batch_inference_input.csv <<EOF
area_sqm,latitude,longitude,house_age,med_inc,ave_rooms,ave_bedrms,population,ave_occup,house_type
150.5,37.7749,-122.4194,25,4.5,5.0,2.0,1000,3.0,house
200.0,37.7849,-122.4094,30,5.0,6.0,3.0,1500,3.5,apartment
120.0,37.7649,-122.4294,20,3.5,4.0,2.0,800,2.5,townhouse
EOF

# Upload to S3 to_infer/ prefix (this will trigger batch inference)
aws s3 cp /tmp/batch_inference_input.csv s3://$BUCKET/to_infer/batch_test_$(date +%Y%m%d_%H%M%S).csv

# Monitor batch transform job
aws sagemaker list-transform-jobs \
  --name-contains batch-infer \
  --region $AWS_REGION \
  --query 'TransformJobSummaries[0]' \
  --output json

# Check output in S3
aws s3 ls s3://$BUCKET/predicted/ --recursive
```

## Troubleshooting

### Online Inference Errors

**"Endpoint not found"**
- Verify endpoint name matches environment variable
- Check endpoint status: `aws sagemaker describe-endpoint --endpoint-name $ENDPOINT_NAME`

**"Invalid feature format"**
- Ensure feature engineering matches training data format
- Check column order matches feature_build Lambda output

**CORS errors (if using web frontend)**
- Verify `Access-Control-Allow-Origin` header in Lambda response
- Add CORS configuration in API Gateway if needed

### Batch Inference Errors

**"Model not found"**
- Provide explicit `model_name` in event payload
- Or ensure model is registered in DynamoDB with `is_deployed=true`

**"Transform job failed"**
- Check CloudWatch logs for detailed error
- Verify input CSV format matches training data (with features, not raw data)
- Ensure S3 paths are accessible to SageMaker execution role

**"EventBridge not triggering Lambda"**
- Verify EventBridge rule is enabled
- Check Lambda permissions for EventBridge
- Verify S3 event notifications are configured

## Next Steps

1. **Add input validation** in online inference Lambda
2. **Implement caching** for frequently requested predictions
3. **Add rate limiting** in API Gateway
4. **Set up CloudWatch alarms** for inference latency and errors
5. **Create web frontend** for user-friendly prediction interface

